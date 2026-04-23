"""Tests for the #768 auto-claim layer on the task_assignment sweep.

The sweep now claims queued worker-role tasks on behalf of the user
(bounded by ``max_concurrent_per_project``) whenever the plan-gate is
open. The self-heal layer unclaims in-progress tasks whose tmux window
is missing so a crashed worker doesn't permanently lock its task in
``in_progress``.

These tests exercise the pure-Python branches of the auto-claim
helpers — no real tmux sessions are started. The Notesy regression is
specifically pinned: a project with an approved plan + queued tasks +
zero active workers auto-claims one per sweep tick.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pollypm.plugins_builtin.task_assignment_notify.handlers.sweep import (
    _auto_claim_enabled_for_project,
    _auto_claim_next,
    _max_concurrent_for_project,
    _recover_dead_claims,
)
from pollypm.plugins_builtin.task_assignment_notify.resolver import _RuntimeServices
from pollypm.work.models import WorkStatus
from pollypm.work.sqlite_service import SQLiteWorkService


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------


class _FakeProject:
    def __init__(
        self,
        *,
        key: str = "proj",
        path: Path = Path("/tmp/proj"),
        auto_claim: bool | None = None,
        max_concurrent_workers: int | None = None,
    ) -> None:
        self.key = key
        self.path = path
        self.auto_claim = auto_claim
        self.max_concurrent_workers = max_concurrent_workers


def _svc_defaults(**overrides) -> _RuntimeServices:
    defaults = dict(
        session_service=None, state_store=None, work_service=None,
        project_root=Path("/tmp"),
        auto_claim=True, max_concurrent_per_project=2,
    )
    defaults.update(overrides)
    return _RuntimeServices(**defaults)


def test_auto_claim_enabled_follows_defaults() -> None:
    assert _auto_claim_enabled_for_project(_svc_defaults(), _FakeProject()) is True


def test_auto_claim_disabled_by_global_flag() -> None:
    svc = _svc_defaults(auto_claim=False)
    assert _auto_claim_enabled_for_project(svc, _FakeProject()) is False


def test_auto_claim_disabled_per_project() -> None:
    """Explicit per-project ``auto_claim = false`` wins over global True."""
    project = _FakeProject(auto_claim=False)
    assert _auto_claim_enabled_for_project(_svc_defaults(), project) is False


def test_auto_claim_per_project_none_defers_to_global() -> None:
    """``auto_claim=None`` on the project means "use the global default"."""
    project = _FakeProject(auto_claim=None)
    assert _auto_claim_enabled_for_project(_svc_defaults(auto_claim=True), project) is True
    assert _auto_claim_enabled_for_project(_svc_defaults(auto_claim=False), project) is False


def test_max_concurrent_per_project_override_wins() -> None:
    project = _FakeProject(max_concurrent_workers=5)
    assert _max_concurrent_for_project(_svc_defaults(), project) == 5


def test_max_concurrent_default_when_no_override() -> None:
    svc = _svc_defaults(max_concurrent_per_project=3)
    assert _max_concurrent_for_project(svc, _FakeProject()) == 3


def test_max_concurrent_floors_at_one() -> None:
    """A zero / negative config value shouldn't silently disable claims —
    ``auto_claim=false`` is the documented off switch. Floor at 1."""
    svc = _svc_defaults(max_concurrent_per_project=0)
    assert _max_concurrent_for_project(svc, _FakeProject()) == 1


# ---------------------------------------------------------------------------
# Full auto-claim integration against a real work service
# ---------------------------------------------------------------------------


def _write_plan(project_path: Path) -> None:
    plan_path = project_path / "docs" / "project-plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Plan\n" + "body " * 200, encoding="utf-8")


def _seed_approved_plan(
    project_path: Path, project_key: str, *, approved_at: datetime | None = None,
) -> None:
    """Stamp a done+approved plan_project task via direct SQL so the
    gate sees an approved plan without running the full flow."""
    from pollypm.work.models import Decision, ExecutionStatus

    db_path = project_path / ".pollypm" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    svc = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        task = svc.create(
            title="Plan project",
            description="planning",
            type="task",
            project=project_key,
            flow_template="plan_project",
            roles={"architect": "architect"},
            priority="high",
        )
        stamp = approved_at or datetime.now(timezone.utc) - timedelta(minutes=5)
        stamp_iso = stamp.isoformat()
        svc._conn.execute(
            "UPDATE work_tasks SET work_status = ? "
            "WHERE project = ? AND task_number = ?",
            (WorkStatus.DONE.value, project_key, task.task_number),
        )
        svc._conn.execute(
            "INSERT INTO work_node_executions "
            "(task_project, task_number, node_id, visit, status, "
            "decision, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_key, task.task_number, "user_approval", 1,
                ExecutionStatus.COMPLETED.value, Decision.APPROVED.value,
                stamp_iso, stamp_iso,
            ),
        )
        svc._conn.execute(
            "INSERT INTO work_context_entries "
            "(task_project, task_number, entry_type, actor, created_at, text) "
            "VALUES (?, ?, 'plan_approved', 'system', ?, '{}')",
            (project_key, task.task_number, stamp_iso),
        )
        svc._conn.commit()
    finally:
        svc.close()


def _seed_queued_worker_task(
    project_path: Path,
    project_key: str,
    *,
    title: str = "Work item",
    link_to_plan: bool = True,
) -> str:
    """Create a queued worker-role task. When ``link_to_plan=True``, link
    it as a child of the approved plan_project task so the staleness
    check (#281) doesn't flag it as post-plan drift."""
    db_path = project_path / ".pollypm" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    svc = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        task = svc.create(
            title=title,
            description="implementation",
            type="task",
            project=project_key,
            flow_template="standard",
            roles={"worker": "worker", "reviewer": "reviewer"},
            priority="normal",
        )
        svc.queue(task.task_id, "test")
        if link_to_plan:
            plans = [
                t for t in svc.list_tasks(project=project_key)
                if t.flow_template_id == "plan_project"
                and t.work_status == WorkStatus.DONE
            ]
            if plans:
                svc.link(plans[0].task_id, task.task_id, "parent")
        return task.task_id
    finally:
        svc.close()


def test_auto_claim_claims_next_queued_task_when_capacity_available(tmp_path: Path) -> None:
    """The Notesy regression: project has an approved plan + queued
    worker-role task + zero active workers → sweep auto-claims it."""
    project_path = tmp_path / "proj"
    project_path.mkdir()
    _write_plan(project_path)
    _seed_approved_plan(project_path, "proj")
    task_id = _seed_queued_worker_task(project_path, "proj")

    db_path = project_path / ".pollypm" / "state.db"
    work = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        svc = _svc_defaults()
        totals = {"considered": 0, "by_outcome": {}}
        _auto_claim_next(
            svc, work, _FakeProject(key="proj", path=project_path), totals,
        )
        # Task should now be in_progress + reflected in totals.
        assert totals["by_outcome"].get("auto_claim_spawned", 0) == 1
        task = work.get(task_id)
        assert task.work_status == WorkStatus.IN_PROGRESS
    finally:
        work.close()


def test_auto_claim_respects_capacity_cap(tmp_path: Path) -> None:
    """Cap of 1 + already-active worker → no new claim."""
    project_path = tmp_path / "proj"
    project_path.mkdir()
    _write_plan(project_path)
    _seed_approved_plan(project_path, "proj")
    _seed_queued_worker_task(project_path, "proj", title="first")
    task_id_2 = _seed_queued_worker_task(project_path, "proj", title="second")

    db_path = project_path / ".pollypm" / "state.db"
    work = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        # First claim fills the 1-slot cap.
        svc = _svc_defaults(max_concurrent_per_project=1)
        totals = {"considered": 0, "by_outcome": {}}
        _auto_claim_next(svc, work, _FakeProject(key="proj", path=project_path), totals)
        assert totals["by_outcome"].get("auto_claim_spawned", 0) == 1

        # Second call: cap hit, no new spawn.
        totals2 = {"considered": 0, "by_outcome": {}}
        _auto_claim_next(svc, work, _FakeProject(key="proj", path=project_path), totals2)
        assert totals2["by_outcome"].get("auto_claim_spawned", 0) == 0
        assert work.get(task_id_2).work_status == WorkStatus.QUEUED
    finally:
        work.close()


def test_auto_claim_skips_when_plan_gate_closed(tmp_path: Path) -> None:
    """No approved plan → no auto-claim. Same bar as pm task claim."""
    project_path = tmp_path / "proj"
    project_path.mkdir()
    # No plan file, no plan-project approval.
    task_id = _seed_queued_worker_task(project_path, "proj")

    db_path = project_path / ".pollypm" / "state.db"
    work = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        svc = _svc_defaults()
        totals = {"considered": 0, "by_outcome": {}}
        _auto_claim_next(svc, work, _FakeProject(key="proj", path=project_path), totals)
        assert "auto_claim_spawned" not in totals["by_outcome"]
        assert work.get(task_id).work_status == WorkStatus.QUEUED
    finally:
        work.close()
