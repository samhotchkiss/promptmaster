"""Regression tests for issue #922 — kickoff push race vs worker bootstrap.

Background:
- ``pm task claim`` mutates the work task to ``in_progress`` and emits a
  TaskAssignmentEvent. The in-process listener fires ``notify()`` BEFORE
  ``provision_worker`` spawns the per-task ``task-<project>-<N>`` tmux
  window. At that moment the per-task pane doesn't yet exist, so
  ``notify()`` returns ``no_session`` (no ``record_notification`` row),
  and the canonical "Resume work: …" kickoff is never delivered.
- After #921 the heartbeat sweep correctly resolves
  ``task-<project>-<N>`` as the worker session for in_progress tasks,
  but the standard sweep path is gated on:
  (a) ``_target_session_is_idle`` — claude's ``⏺ ready`` prompt trips
      the heuristic and falsely reports the worker as busy;
  (b) the 5-minute notify dedupe — a poisoned record_notification
      entry from any earlier failed attempt blocks future sends.
  Both gates kept the worker stuck at "ready" with no kickoff in the
  live blackjack-trainer/4 reproduction.

Fix (option 2 — sweep-pushable kickoff with idempotent log stamp):
- Add ``work_node_executions.kickoff_sent_at`` (TEXT, nullable).
- ``notify()`` stamps the column on a successful worker kickoff send.
- The heartbeat sweep treats ``kickoff_sent_at IS NULL`` as
  "delivery still required" and force-pushes the kickoff: bypasses the
  idle gate and uses ``throttle_seconds=0`` so the dedupe table can't
  suppress the first push. Once stamped, future ticks fall back to the
  normal idle-gated + throttled path — re-pushes are idempotent.

The tests below exercise the three behaviours called out in the bug:
1. After ``pm task claim`` returns, a single sweep tick delivers the
   canonical ``format_ping_for_role(event)`` payload to the per-task
   session — the kickoff_pending gate force-pushes past idle/dedupe.
2. A second sweep tick after the kickoff stamp lands does NOT re-push.
3. If the first sweep tick can't deliver (no live session yet), the
   row stays kickoff_sent_at=NULL so the next tick re-tries; once a
   send succeeds the stamp lands and the cycle settles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pollypm.plugins_builtin.task_assignment_notify.handlers.sweep import (
    task_assignment_sweep_handler,
)
from pollypm.plugins_builtin.task_assignment_notify.resolver import (
    _RuntimeServices,
    notify,
)
from pollypm.storage.state import StateStore
from pollypm.work import task_assignment as bus
from pollypm.work.models import ActorType
from pollypm.work.sqlite_service import SQLiteWorkService
from pollypm.work.task_assignment import TaskAssignmentEvent


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeHandle:
    name: str


@dataclass
class _FakeSessionService:
    """Mirrors ``test_task_assignment_notify.FakeSessionService`` but kept
    local so this regression file is self-contained."""

    handles: list[_FakeHandle]
    sent: list[tuple[str, str]] = field(default_factory=list)
    busy: set[str] = field(default_factory=set)

    def list(self) -> list[_FakeHandle]:
        return list(self.handles)

    def send(self, name: str, text: str, *, press_enter: bool = True) -> None:
        self.sent.append((name, text))

    def is_turn_active(self, name: str) -> bool:
        # The bug: claude's ``⏺ ready`` idle prompt trips the busy gate
        # because ``is_turn_active`` matches the ``⏺`` bullet whether
        # the worker is actively turning or just sitting at the prompt.
        # The harness models that by reporting EVERY live session as
        # busy — the sweep's force-kickoff branch must still deliver.
        return name in self.busy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_environment(tmp_path: Path):
    """Build a real SQLiteWorkService + StateStore for sweep integration."""
    bus.clear_listeners()
    work = SQLiteWorkService(db_path=tmp_path / "work.db")
    store = StateStore(tmp_path / "state.db")
    return work, store


def _claim_worker_task(work: SQLiteWorkService, *, project: str, title: str):
    task = work.create(
        title=title,
        description="Implement the thing",
        type="task",
        project=project,
        flow_template="standard",
        roles={"worker": "worker", "reviewer": "reviewer"},
        priority="normal",
    )
    work.queue(task.task_id, "pm")
    work.claim(task.task_id, "worker")
    return work.get(task.task_id)


def _install_sweep_loader(monkeypatch, services):
    monkeypatch.setattr(
        "pollypm.plugins_builtin.task_assignment_notify.handlers.sweep.load_runtime_services",
        lambda *, config_path=None: services,
    )


def _reopen_work(tmp_path: Path) -> SQLiteWorkService:
    """Re-open the work DB after the sweep closes its connection.

    ``task_assignment_sweep_handler`` calls ``_close_quietly`` on the
    work service it pulled from the resolved runtime services. Tests
    that need to inspect the resulting state open a fresh connection
    against the same on-disk file.
    """
    return SQLiteWorkService(db_path=tmp_path / "work.db")


# ---------------------------------------------------------------------------
# (1) The kickoff payload reaches the per-task session within one sweep tick
# ---------------------------------------------------------------------------


class TestSweepDeliversKickoffPastBootstrapRace:
    def test_kickoff_force_pushes_past_idle_gate_and_dedupe(
        self, tmp_path, monkeypatch,
    ):
        """A claimed worker task with no kickoff_sent_at stamp gets the
        canonical ``Resume work`` payload on the next sweep tick — even
        when the session pretends to be busy (⏺ heuristic) and a stale
        notification row exists in the dedupe table.
        """
        work, store = _make_environment(tmp_path)
        task = _claim_worker_task(
            work, project="blackjack-trainer", title="Add charts",
        )
        live_window = f"task-{task.project}-{task.task_number}"
        # The session reports busy — modelling claude's ``⏺ ready``
        # idle-prompt false-positive that broke the live #922 repro.
        svc = _FakeSessionService(
            handles=[_FakeHandle(live_window)],
            busy={live_window},
        )
        # Poison the dedupe table. The bootstrap-race scenario is
        # exactly this: an earlier notify() attempt logged a row but
        # the kickoff was either dropped or sent into a still-loading
        # pane. The sweep must force past it.
        store.record_notification(
            session_name=live_window,
            task_id=task.task_id,
            project=task.project,
            message="prior attempt that was lost in bootstrap",
            delivery_status="sent",
            execution_version=work.current_node_visit(
                task.project, task.task_number, task.current_node_id,
            ),
        )

        services = _RuntimeServices(
            session_service=svc, state_store=store,
            work_service=work, project_root=tmp_path,
            msg_store=store,
        )
        _install_sweep_loader(monkeypatch, services)

        result = task_assignment_sweep_handler({})

        # The sweep classifies this as a forced kickoff, not a normal
        # idle-gated send.
        assert result["by_outcome"].get("forced_kickoff", 0) == 1
        # The per-task session received exactly the canonical payload.
        assert len(svc.sent) == 1
        target, message = svc.sent[0]
        assert target == live_window
        assert "Resume work" in message
        assert f"[{task.task_id}]" in message
        assert "pm task get" in message
        # And the stamp landed so future sweeps know delivery happened.
        # Re-open the DB — the sweep handler closes its work-service
        # connection on completion.
        reopened = _reopen_work(tmp_path)
        try:
            stamp = reopened.kickoff_sent_at(
                task.project, task.task_number, task.current_node_id,
            )
        finally:
            reopened.close()
        assert stamp, f"expected kickoff_sent_at to be stamped, got {stamp!r}"


# ---------------------------------------------------------------------------
# (2) Repeated sweep ticks do NOT re-push once kickoff_sent_at is stamped
# ---------------------------------------------------------------------------


class TestRepeatedSweepsDoNotDoublePush:
    def test_second_sweep_after_kickoff_stamp_is_idempotent(
        self, tmp_path, monkeypatch,
    ):
        """Two consecutive sweep ticks deliver the kickoff exactly once.

        Tick 1 force-pushes (kickoff_sent_at IS NULL) and stamps the
        execution row. Tick 2 finds kickoff_sent_at populated, falls
        back to the standard idle-gated + 5-min throttle path, which
        skips because the worker is busy and the dedupe row is hot.
        The worker must NOT receive a duplicate "Resume work" message.
        """
        work, store = _make_environment(tmp_path)
        task = _claim_worker_task(
            work, project="blackjack-trainer", title="Add charts",
        )
        live_window = f"task-{task.project}-{task.task_number}"
        svc = _FakeSessionService(
            handles=[_FakeHandle(live_window)],
            busy={live_window},
        )

        # The sweep loader re-resolves runtime services on each tick;
        # we hand it a fresh work-service connection per call so the
        # post-tick close doesn't strand the next tick.
        ticks: list[SQLiteWorkService] = []

        def _loader(*, config_path=None):
            fresh = SQLiteWorkService(db_path=tmp_path / "work.db")
            ticks.append(fresh)
            return _RuntimeServices(
                session_service=svc, state_store=store,
                work_service=fresh, project_root=tmp_path,
                msg_store=store,
            )

        monkeypatch.setattr(
            "pollypm.plugins_builtin.task_assignment_notify.handlers.sweep.load_runtime_services",
            _loader,
        )

        # Tick 1 — force-push delivers the kickoff.
        result1 = task_assignment_sweep_handler({})
        assert result1["by_outcome"].get("forced_kickoff", 0) == 1
        assert len(svc.sent) == 1

        # Tick 2 — kickoff_sent_at is now stamped. The sweep must NOT
        # force-push again. The busy gate (or the dedupe) suppresses
        # the resend.
        result2 = task_assignment_sweep_handler({})
        assert result2["by_outcome"].get("forced_kickoff", 0) == 0
        # No duplicate payload reached the worker.
        assert len(svc.sent) == 1, (
            f"expected exactly one kickoff payload, got {svc.sent!r}"
        )


# ---------------------------------------------------------------------------
# (3) A spawn that didn't reach ready is recoverable on the next sweep tick
# ---------------------------------------------------------------------------


class TestUndeliveredKickoffIsRecoverable:
    def test_no_session_then_session_appears_kickoff_lands_on_next_tick(
        self, tmp_path, monkeypatch,
    ):
        """The first sweep tick fires before the per-task pane exists
        (or before ``provision_worker`` finished spawning it). The
        second tick — once the session is live — delivers the kickoff.

        Models the option-1 timeout-fallback handoff to option 2.
        """
        work, store = _make_environment(tmp_path)
        task = _claim_worker_task(
            work, project="blackjack-trainer", title="Add charts",
        )
        live_window = f"task-{task.project}-{task.task_number}"
        # Tick 1: no live session. Sweep escalates no_session, no stamp.
        svc = _FakeSessionService(handles=[])

        def _loader(*, config_path=None):
            fresh = SQLiteWorkService(db_path=tmp_path / "work.db")
            return _RuntimeServices(
                session_service=svc, state_store=store,
                work_service=fresh, project_root=tmp_path,
                msg_store=store,
            )

        monkeypatch.setattr(
            "pollypm.plugins_builtin.task_assignment_notify.handlers.sweep.load_runtime_services",
            _loader,
        )

        result1 = task_assignment_sweep_handler({})
        assert result1["by_outcome"].get("forced_kickoff", 0) == 0
        assert result1["by_outcome"].get("no_session", 0) >= 1

        # The execution row is still un-stamped → the next tick must
        # treat this as a delivery-required state.
        post1 = _reopen_work(tmp_path)
        try:
            assert post1.kickoff_sent_at(
                task.project, task.task_number, task.current_node_id,
            ) is None
        finally:
            post1.close()

        # Tick 2: pane exists now. Even when the session is "busy"
        # (claude bullet still tripping the heuristic), the force-push
        # delivers the kickoff.
        svc.handles = [_FakeHandle(live_window)]
        svc.busy = {live_window}

        result2 = task_assignment_sweep_handler({})
        assert result2["by_outcome"].get("forced_kickoff", 0) == 1
        assert len(svc.sent) == 1
        target, message = svc.sent[0]
        assert target == live_window
        assert "Resume work" in message
        assert f"[{task.task_id}]" in message
        # Subsequent ticks now have a stamp and won't re-push.
        post2 = _reopen_work(tmp_path)
        try:
            stamp = post2.kickoff_sent_at(
                task.project, task.task_number, task.current_node_id,
            )
        finally:
            post2.close()
        assert stamp


# ---------------------------------------------------------------------------
# (4) notify() stamps kickoff_sent_at directly when the in-process listener
#     succeeds (e.g. legacy long-lived worker session is already running).
# ---------------------------------------------------------------------------


class TestNotifyStampsKickoffOnSuccess:
    def test_successful_worker_kickoff_marks_execution_row(
        self, tmp_path,
    ):
        work, store = _make_environment(tmp_path)
        task = _claim_worker_task(work, project="proj", title="Implement")
        # Long-lived worker session is already running — the in-process
        # listener path that succeeds should also stamp kickoff_sent_at
        # so a later sweep tick doesn't force-push past dedupe.
        svc = _FakeSessionService(handles=[_FakeHandle("worker-proj")])

        event = TaskAssignmentEvent(
            task_id=task.task_id,
            project=task.project,
            task_number=task.task_number,
            title=task.title,
            current_node=task.current_node_id or "implement",
            current_node_kind="work",
            actor_type=ActorType.ROLE,
            actor_name="worker",
            work_status="in_progress",
            priority="normal",
            transitioned_at=task.updated_at,
            transitioned_by="tester",
            execution_version=work.current_node_visit(
                task.project, task.task_number, task.current_node_id,
            ),
        )
        services = _RuntimeServices(
            session_service=svc, state_store=store,
            work_service=work, project_root=tmp_path,
            msg_store=store,
        )

        outcome = notify(event, services=services)
        assert outcome["outcome"] == "sent"
        stamp = work.kickoff_sent_at(
            task.project, task.task_number, task.current_node_id,
        )
        assert stamp, (
            "notify() must stamp kickoff_sent_at after a successful "
            "worker kickoff so the sweep stops force-pushing"
        )

    def test_reviewer_kickoff_does_not_stamp_worker_marker(
        self, tmp_path,
    ):
        """Only worker-role kickoffs use the marker — reviewers / agents
        ride the existing dedupe path."""
        work, store = _make_environment(tmp_path)
        task = _claim_worker_task(work, project="proj", title="Implement")
        svc = _FakeSessionService(handles=[_FakeHandle("pm-reviewer")])

        event = TaskAssignmentEvent(
            task_id=task.task_id,
            project=task.project,
            task_number=task.task_number,
            title=task.title,
            current_node="review",
            current_node_kind="review",
            actor_type=ActorType.ROLE,
            actor_name="reviewer",
            work_status="review",
            priority="normal",
            transitioned_at=task.updated_at,
            transitioned_by="tester",
            execution_version=0,
        )
        services = _RuntimeServices(
            session_service=svc, state_store=store,
            work_service=work, project_root=tmp_path,
            msg_store=store,
        )

        outcome = notify(event, services=services)
        assert outcome["outcome"] == "sent"
        # The worker's execution row is untouched — the reviewer's
        # send doesn't move the worker kickoff marker.
        assert work.kickoff_sent_at(
            task.project, task.task_number, task.current_node_id,
        ) is None
