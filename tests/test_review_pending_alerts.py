"""Tests for ``review_pending`` alerts emitted from the sweep handler.

#1053: tasks in ``review`` state are work-for-the-human (approve / reject
/ inspect) but were previously invisible to ``pm alerts``. This module
covers the ``_emit_review_pending_alert`` integration with
``task_assignment_sweep_handler`` — emit on review-state tasks, no-op
otherwise, and clear when a task transitions out of review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pollypm.plugins_builtin.task_assignment_notify.handlers.sweep import (
    _decode_review_pending_session_name,
    _emit_review_pending_alert,
    task_assignment_sweep_handler,
)
from pollypm.plugins_builtin.task_assignment_notify.resolver import _RuntimeServices
from pollypm.storage.state import StateStore
from pollypm.work import task_assignment as bus
from pollypm.work.sqlite_service import SQLiteWorkService


# ---------------------------------------------------------------------------
# Test doubles (mirror tests/test_plan_presence_gate.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeHandle:
    name: str


@dataclass
class FakeSessionService:
    handles: list[FakeHandle]
    sent: list[tuple[str, str]] = field(default_factory=list)
    busy: set[str] = field(default_factory=set)

    def list(self) -> list[FakeHandle]:
        return list(self.handles)

    def send(self, name: str, text: str, *, press_enter: bool = True) -> None:
        self.sent.append((name, text))

    def is_turn_active(self, name: str) -> bool:
        return name in self.busy


@dataclass
class FakeKnownProject:
    key: str
    path: Path
    enforce_plan: bool | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_fake_loader(monkeypatch, services_factory):
    monkeypatch.setattr(
        "pollypm.plugins_builtin.task_assignment_notify.handlers.sweep.load_runtime_services",
        lambda *, config_path=None: services_factory(),
    )


def _factory_for(*, store, session_svc, project_key, project_path):
    """Build a ``_RuntimeServices`` factory pointing at one project."""

    def factory():
        return _RuntimeServices(
            session_service=session_svc,
            state_store=store,
            work_service=None,
            project_root=project_path.parent,
            known_projects=(
                FakeKnownProject(key=project_key, path=project_path),
            ),
            enforce_plan=False,
            plan_dir="docs/plan",
        )

    return factory


def _create_review_state_task(
    project_path: Path, *, project_key: str, title: str = "Implement thing",
) -> str:
    """Create + queue + advance a task to ``review`` so it sits awaiting approval.

    Mirrors ``tests/test_plan_presence_gate.py::_advance_task_to_review``
    but folded into a single helper for the review-pending flow tests.
    """
    db_path = project_path / ".pollypm" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    bus.clear_listeners()
    work = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        task = work.create(
            title=title,
            description="impl task",
            type="task",
            project=project_key,
            flow_template="standard",
            roles={"worker": "agent-1", "reviewer": "agent-2"},
            priority="normal",
        )
        work.queue(task.task_id, "pm")
        work.claim(task.task_id, "worker")
        work.node_done(
            task.task_id,
            "worker",
            work_output={
                "type": "code_change",
                "summary": "implemented the thing",
                "artifacts": [
                    {
                        "kind": "commit",
                        "description": "implementation",
                        "ref": "HEAD",
                    },
                ],
            },
        )
        return task.task_id
    finally:
        work.close()


def _create_queued_task(
    project_path: Path, *, project_key: str, title: str = "Queued thing",
) -> str:
    """Create a non-review (queued) task so the sweep has something to skip past."""
    db_path = project_path / ".pollypm" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    bus.clear_listeners()
    work = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        task = work.create(
            title=title,
            description="impl task",
            type="task",
            project=project_key,
            flow_template="standard",
            roles={"worker": "agent-1", "reviewer": "agent-2"},
            priority="normal",
        )
        work.queue(task.task_id, "pm")
        return task.task_id
    finally:
        work.close()


def _approve_review_task(project_path: Path, task_id: str) -> None:
    """Walk a review-state task forward via reviewer approve so it leaves review.

    Uses ``skip_gates=True`` because the in-memory test environment has
    no real branches / merges to validate against the safelist gates.
    The point of the test is the alert-lifecycle behaviour, not the
    transition manager's gate logic.
    """
    db_path = project_path / ".pollypm" / "state.db"
    work = SQLiteWorkService(db_path=db_path, project_path=project_path)
    try:
        work.approve(task_id, "reviewer", skip_gates=True)
    finally:
        work.close()


# ---------------------------------------------------------------------------
# Decode helper — small unit test
# ---------------------------------------------------------------------------


class TestDecodeReviewPendingSessionName:
    def test_simple_project_decodes(self):
        assert _decode_review_pending_session_name("review-smoketest-1") == (
            "smoketest", 1,
        )

    def test_dashed_project_key_decodes(self):
        assert _decode_review_pending_session_name(
            "review-multi-word-proj-7",
        ) == ("multi-word-proj", 7)

    def test_unrelated_session_name_returns_none(self):
        assert _decode_review_pending_session_name("plan_gate-foo") is None
        assert _decode_review_pending_session_name("worker-foo") is None

    def test_missing_task_number_returns_none(self):
        assert _decode_review_pending_session_name("review-foo") is None
        assert _decode_review_pending_session_name("review-foo-") is None
        assert _decode_review_pending_session_name("review-foo-abc") is None


# ---------------------------------------------------------------------------
# Sweep-handler integration tests
# ---------------------------------------------------------------------------


class TestReviewPendingAlertEmission:
    def test_review_state_task_emits_review_pending_alert(
        self, tmp_path, monkeypatch,
    ):
        """A task parked in ``review`` produces a review_pending alert."""
        proj = tmp_path / "proj"
        proj.mkdir()
        task_id = _create_review_state_task(proj, project_key="proj")

        store = StateStore(tmp_path / "workspace_state.db")
        session_svc = FakeSessionService(handles=[FakeHandle("reviewer")])
        _install_fake_loader(monkeypatch, _factory_for(
            store=store, session_svc=session_svc,
            project_key="proj", project_path=proj,
        ))

        result = task_assignment_sweep_handler({})

        assert result["outcome"] == "swept"
        assert result["review_pending_alerts"] == 1

        review_alerts = [
            a for a in store.open_alerts()
            if a.alert_type == "review_pending"
        ]
        assert len(review_alerts) == 1
        alert = review_alerts[0]
        assert alert.session_name == "review-proj-1"
        assert alert.severity == "warn"
        # Spec'd copy must include the task id and the three actionable
        # CLI verbs so future edits can't silently drop them.
        assert task_id in alert.message
        assert "pm task approve" in alert.message
        assert "pm task reject" in alert.message
        assert "pm task get" in alert.message

        store.close()

    def test_non_review_task_does_not_emit_review_pending_alert(
        self, tmp_path, monkeypatch,
    ):
        """A merely-queued task must not produce a review_pending alert."""
        proj = tmp_path / "proj"
        proj.mkdir()
        _create_queued_task(proj, project_key="proj")

        store = StateStore(tmp_path / "workspace_state.db")
        session_svc = FakeSessionService(handles=[FakeHandle("worker-proj")])
        _install_fake_loader(monkeypatch, _factory_for(
            store=store, session_svc=session_svc,
            project_key="proj", project_path=proj,
        ))

        result = task_assignment_sweep_handler({})

        assert result["review_pending_alerts"] == 0
        review_alerts = [
            a for a in store.open_alerts()
            if a.alert_type == "review_pending"
        ]
        assert review_alerts == []

        store.close()

    def test_repeated_sweeps_do_not_duplicate_review_pending_alerts(
        self, tmp_path, monkeypatch,
    ):
        """Upsert semantics — two sweeps, still one open review_pending row."""
        proj = tmp_path / "proj"
        proj.mkdir()
        _create_review_state_task(proj, project_key="proj")

        store = StateStore(tmp_path / "workspace_state.db")
        session_svc = FakeSessionService(handles=[FakeHandle("reviewer")])
        _install_fake_loader(monkeypatch, _factory_for(
            store=store, session_svc=session_svc,
            project_key="proj", project_path=proj,
        ))

        task_assignment_sweep_handler({})
        first = [
            a for a in store.open_alerts()
            if a.alert_type == "review_pending"
        ]
        assert len(first) == 1

        task_assignment_sweep_handler({})
        second = [
            a for a in store.open_alerts()
            if a.alert_type == "review_pending"
        ]
        assert len(second) == 1
        assert first[0].alert_id == second[0].alert_id

        store.close()

    def test_review_pending_alert_clears_when_task_leaves_review(
        self, tmp_path, monkeypatch,
    ):
        """Approving a review-state task clears its review_pending alert."""
        proj = tmp_path / "proj"
        proj.mkdir()
        task_id = _create_review_state_task(proj, project_key="proj")

        store = StateStore(tmp_path / "workspace_state.db")
        session_svc = FakeSessionService(handles=[FakeHandle("reviewer")])
        _install_fake_loader(monkeypatch, _factory_for(
            store=store, session_svc=session_svc,
            project_key="proj", project_path=proj,
        ))

        first = task_assignment_sweep_handler({})
        assert first["review_pending_alerts"] == 1
        assert [
            a for a in store.open_alerts()
            if a.alert_type == "review_pending"
        ]

        # Walk the task forward — approve takes it out of ``review``.
        _approve_review_task(proj, task_id)

        second = task_assignment_sweep_handler({})
        assert second["review_pending_alerts"] == 0
        # The clearing pass closed the now-stale alert.
        assert second["review_pending_cleared"] >= 1
        assert [
            a for a in store.open_alerts()
            if a.alert_type == "review_pending"
        ] == []

        store.close()


class TestEmitReviewPendingAlertGuards:
    """Direct unit tests for ``_emit_review_pending_alert``."""

    def test_ghost_project_alert_dropped(self, tmp_path):
        """Project not in the registry → alert is dropped on emission."""
        store = StateStore(tmp_path / "state.db")
        services = _RuntimeServices(
            session_service=None,
            state_store=store,
            work_service=None,
            project_root=tmp_path,
            known_projects=(
                FakeKnownProject(key="real_project", path=tmp_path),
            ),
            enforce_plan=False,
            plan_dir="docs/plan",
        )

        @dataclass
        class FakeTask:
            task_number: int = 1
            title: str = "Ghost task"

        _emit_review_pending_alert(
            services, project="ghost_project", task=FakeTask(),
        )
        assert [
            a for a in store.open_alerts()
            if a.alert_type == "review_pending"
        ] == []
        store.close()

    def test_emission_with_no_store_is_noop(self, tmp_path):
        services = _RuntimeServices(
            session_service=None,
            state_store=None,
            work_service=None,
            project_root=tmp_path,
            known_projects=(),
            enforce_plan=False,
            plan_dir="docs/plan",
        )

        @dataclass
        class FakeTask:
            task_number: int = 1
            title: str = "Whatever"

        # Should not raise.
        _emit_review_pending_alert(
            services, project="proj", task=FakeTask(),
        )
