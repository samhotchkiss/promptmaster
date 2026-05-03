"""Tests for the ``blocked_chain.sweep`` cadence handler (#1073).

Verifies the dead-end detector emits ``blocked_dead_end`` alerts on
recursively-blocked tasks whose chain has no in-flight work, and stays
silent when at least one blocker is being worked on or when the task
hasn't been blocked long enough.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pollypm.plugins_builtin.core_recurring.blocked_chain import (
    BLOCKED_DEAD_END_ALERT_TYPE,
    DEFAULT_STALE_THRESHOLD_SECONDS,
    blocked_dead_end_session_name,
    is_dead_end_chain,
    sweep_blocked_chains,
)
from pollypm.work.models import WorkStatus
from pollypm.work.sqlite_service import SQLiteWorkService


# ---------------------------------------------------------------------------
# Recording store double
# ---------------------------------------------------------------------------


class _RecordingStore:
    """Captures upsert_alert / clear_alert calls for assertion."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str, str, str]] = []
        self.cleared: list[tuple[str, str]] = []

    def upsert_alert(
        self, scope: str, alert_type: str, severity: str, message: str,
    ) -> None:
        self.alerts.append((scope, alert_type, severity, message))

    def clear_alert(self, scope: str, alert_type: str) -> None:
        self.cleared.append((scope, alert_type))


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def svc(tmp_path: Path) -> SQLiteWorkService:
    return SQLiteWorkService(db_path=tmp_path / "work.db")


def _mk_task(svc: SQLiteWorkService, *, project: str = "p", title: str = "T") -> object:
    return svc.create(
        title=title,
        description=f"do {title}",
        type="task",
        project=project,
        flow_template="standard",
        roles={"worker": "agent-1", "reviewer": "agent-2"},
        priority="normal",
        created_by="tester",
    )


def _backdate_blocked_transition(
    svc: SQLiteWorkService, *, project: str, task_number: int, when: datetime,
) -> None:
    """Rewrite the most-recent ``to_state='blocked'`` transition timestamp.

    Lets tests force a task to look "stuck for N hours" without sleeping.
    """
    svc._conn.execute(
        "UPDATE work_transitions SET created_at = ? "
        "WHERE id = ("
        "  SELECT id FROM work_transitions "
        "  WHERE task_project = ? AND task_number = ? AND to_state = ? "
        "  ORDER BY id DESC LIMIT 1"
        ")",
        (when.isoformat(), project, task_number, WorkStatus.BLOCKED.value),
    )
    svc._conn.commit()


# ---------------------------------------------------------------------------
# is_dead_end_chain pure logic
# ---------------------------------------------------------------------------


class TestIsDeadEndChain:
    def test_empty_chain_is_not_dead_end(self) -> None:
        assert is_dead_end_chain({}) is False

    def test_chain_with_in_progress_blocker_is_alive(self) -> None:
        chain = {
            ("p", 1): "blocked",
            ("p", 2): "in_progress",
        }
        assert is_dead_end_chain(chain) is False

    def test_chain_with_review_blocker_is_alive(self) -> None:
        assert is_dead_end_chain({("p", 1): "review"}) is False

    def test_chain_with_rework_blocker_is_alive(self) -> None:
        assert is_dead_end_chain({("p", 1): "rework"}) is False

    def test_chain_of_blocked_only_is_dead_end(self) -> None:
        chain = {
            ("p", 1): "blocked",
            ("p", 2): "blocked",
            ("p", 3): "queued",
        }
        assert is_dead_end_chain(chain) is True

    def test_chain_of_drafts_is_dead_end(self) -> None:
        # Draft blockers count as no-work-in-flight.
        assert is_dead_end_chain({("p", 1): "draft"}) is True


# ---------------------------------------------------------------------------
# sweep_blocked_chains end-to-end against a real SQLite work service
# ---------------------------------------------------------------------------


class TestSweepBlockedChains:
    def test_emits_dead_end_alert_for_recursively_blocked_chain(
        self, svc: SQLiteWorkService,
    ) -> None:
        """The polly_remote/18 scenario: A blocks B blocks C, all blocked,
        nothing in flight. C should escalate."""
        a = _mk_task(svc, title="A — earliest prerequisite")
        b = _mk_task(svc, title="B — middle blocker")
        c = _mk_task(svc, title="C — leaf task")

        # A blocks B; B blocks C. So C.blocked_by = {B}, B.blocked_by = {A}.
        svc.link(a.task_id, b.task_id, "blocks")
        svc.link(b.task_id, c.task_id, "blocks")

        # Queue + then explicitly block all three so the sweep sees
        # work_status='blocked' rows. (maybe_block only fires on queue
        # when there's an unresolved blocker — A has none, so we have
        # to drive its state directly via the transition machinery.)
        svc.queue(a.task_id, "pm")
        svc.queue(b.task_id, "pm")  # auto-blocks because A is unresolved
        svc.queue(c.task_id, "pm")  # auto-blocks because B is unresolved

        # A doesn't have a blocker — push it to ``blocked`` manually
        # via cancel-then-resume? Simpler: hand-stamp a blocked status
        # so the chain has three blocked rows. Direct UPDATE keeps the
        # test focused on the sweep, not the transition manager.
        now = datetime.now(UTC)
        svc._conn.execute(
            "UPDATE work_tasks SET work_status = ? WHERE project = ? AND task_number = ?",
            (WorkStatus.BLOCKED.value, a.project, a.task_number),
        )
        svc._conn.execute(
            "INSERT INTO work_transitions "
            "(task_project, task_number, from_state, to_state, actor, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                a.project, a.task_number,
                WorkStatus.QUEUED.value, WorkStatus.BLOCKED.value,
                "test", "manual", now.isoformat(),
            ),
        )
        svc._conn.commit()

        # Backdate every blocked transition so the sweep treats them
        # as old enough to escalate.
        long_ago = now - timedelta(hours=2)
        for task_obj in (a, b, c):
            _backdate_blocked_transition(
                svc,
                project=task_obj.project,
                task_number=task_obj.task_number,
                when=long_ago,
            )

        store = _RecordingStore()
        result = sweep_blocked_chains(
            work=svc,
            msg_store=store,
            state_store=None,
            now=now,
        )

        # Only C has recursive blockers (B → A); B has A as a blocker;
        # A has none. So we expect alerts for B and C, not A.
        assert result["dead_end_detected"] == 2
        assert result["alerts_raised"] == 2
        assert result["skipped_no_blockers"] == 1  # A — no blockers in chain.

        emitted = {(scope, alert_type) for scope, alert_type, _, _ in store.alerts}
        assert (
            blocked_dead_end_session_name(b.project, b.task_number),
            BLOCKED_DEAD_END_ALERT_TYPE,
        ) in emitted
        assert (
            blocked_dead_end_session_name(c.project, c.task_number),
            BLOCKED_DEAD_END_ALERT_TYPE,
        ) in emitted

        # Severity is "warn" — not an error; the architect can resolve
        # it without paging anyone.
        for _, _, severity, message in store.alerts:
            assert severity == "warn"
            # The message must mention the task and the dead-end framing
            # so the architect knows what to do.
            assert "dead-end" in message.lower() or "stuck on a dead-end" in message

    def test_does_not_alert_when_chain_has_in_flight_work(
        self, svc: SQLiteWorkService,
    ) -> None:
        """If any recursive blocker is in_progress / review / rework, the
        chain isn't dead — leave it alone."""
        a = _mk_task(svc, title="A")
        b = _mk_task(svc, title="B")
        svc.link(a.task_id, b.task_id, "blocks")
        svc.queue(a.task_id, "pm")
        svc.queue(b.task_id, "pm")  # auto-blocks on A

        # Drive A to in_progress.
        svc.claim(a.task_id, "agent-1")

        # B is now blocked on an in-flight A.
        b_now = svc.get(b.task_id)
        assert b_now.work_status == WorkStatus.BLOCKED

        now = datetime.now(UTC)
        _backdate_blocked_transition(
            svc, project=b.project, task_number=b.task_number,
            when=now - timedelta(hours=2),
        )

        store = _RecordingStore()
        result = sweep_blocked_chains(
            work=svc, msg_store=store, state_store=None, now=now,
        )
        assert result["dead_end_detected"] == 0
        assert result["alerts_raised"] == 0
        assert result["skipped_in_flight_chain"] == 1
        assert store.alerts == []

    def test_skips_recently_blocked_tasks(self, svc: SQLiteWorkService) -> None:
        """A task that just transitioned to blocked shouldn't escalate
        immediately — give the auto-unblocker a chance to catch up."""
        a = _mk_task(svc, title="A")
        b = _mk_task(svc, title="B")
        svc.link(a.task_id, b.task_id, "blocks")
        svc.queue(a.task_id, "pm")
        svc.queue(b.task_id, "pm")

        # Manually push A to blocked so both ends of the chain are blocked.
        now = datetime.now(UTC)
        svc._conn.execute(
            "UPDATE work_tasks SET work_status = ? WHERE project = ? AND task_number = ?",
            (WorkStatus.BLOCKED.value, a.project, a.task_number),
        )
        svc._conn.execute(
            "INSERT INTO work_transitions "
            "(task_project, task_number, from_state, to_state, actor, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a.project, a.task_number, "queued", "blocked", "test", "manual", now.isoformat()),
        )
        svc._conn.commit()

        # Don't backdate — the transitions just happened, so they're
        # within the threshold window.
        store = _RecordingStore()
        result = sweep_blocked_chains(
            work=svc, msg_store=store, state_store=None, now=now,
            stale_threshold_seconds=DEFAULT_STALE_THRESHOLD_SECONDS,
        )
        assert result["alerts_raised"] == 0
        assert result["skipped_recent"] >= 1
        assert store.alerts == []

    def test_no_alert_when_no_blocked_tasks(self, svc: SQLiteWorkService) -> None:
        """Empty work service — sweep should be a clean no-op."""
        store = _RecordingStore()
        result = sweep_blocked_chains(
            work=svc, msg_store=store, state_store=None,
            now=datetime.now(UTC),
        )
        assert result == {
            "blocked_considered": 0,
            "dead_end_detected": 0,
            "alerts_raised": 0,
            "skipped_recent": 0,
            "skipped_in_flight_chain": 0,
            "skipped_no_blockers": 0,
        }
        assert store.alerts == []

    def test_alert_dedupes_via_upsert_on_repeat_ticks(
        self, svc: SQLiteWorkService,
    ) -> None:
        """Two sweep ticks against the same dead-end produce one alert
        per task (upsert_alert is idempotent when the store dedupes by
        (scope, alert_type, status='open')). The recording store here
        simply records both calls — production stores collapse them."""
        a = _mk_task(svc, title="A")
        b = _mk_task(svc, title="B")
        svc.link(a.task_id, b.task_id, "blocks")
        svc.queue(a.task_id, "pm")
        svc.queue(b.task_id, "pm")

        now = datetime.now(UTC)
        svc._conn.execute(
            "UPDATE work_tasks SET work_status = ? WHERE project = ? AND task_number = ?",
            (WorkStatus.BLOCKED.value, a.project, a.task_number),
        )
        svc._conn.execute(
            "INSERT INTO work_transitions "
            "(task_project, task_number, from_state, to_state, actor, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a.project, a.task_number, "queued", "blocked", "test", "m", now.isoformat()),
        )
        svc._conn.commit()
        long_ago = now - timedelta(hours=2)
        for task_obj in (a, b):
            _backdate_blocked_transition(
                svc, project=task_obj.project, task_number=task_obj.task_number,
                when=long_ago,
            )

        store = _RecordingStore()
        sweep_blocked_chains(work=svc, msg_store=store, state_store=None, now=now)
        sweep_blocked_chains(work=svc, msg_store=store, state_store=None, now=now)

        # Two ticks each emit an alert per task; dedupe is the store's
        # job. The handler always issues the upsert so the row stays
        # warm.
        assert len(store.alerts) == 2  # 1 alert (B) per tick × 2 ticks.
        scopes = {scope for scope, *_ in store.alerts}
        assert scopes == {blocked_dead_end_session_name(b.project, b.task_number)}
