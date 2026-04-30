"""Regression tests for issue #1005 — auto-recovery for ``<role>/no_session``.

Two behaviours covered:

1. The bootstrap text the supervisor + tmux session-service write into
   their kickoff pane no longer carries the
   "[PollyPM bootstrap — system message, please ignore on screen]"
   framing header. The header tripped Claude's prompt-injection defense
   and the model refused to adopt its own bootstrap as instructions.
2. ``auto_recover_no_session_alerts`` walks open ``no_session`` alerts,
   honours the alert-age threshold + per-(role, project) backoff, calls
   the spawn shim, records attempt events, and escalates to
   ``<role>/no_session_spawn_failed`` once the attempt budget exhausts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pollypm.recovery.no_session_spawn import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_THRESHOLD_SECONDS,
    SPAWN_ATTEMPT_EVENT_TYPE,
    SPAWN_FAILED_ALERT_TYPE,
    auto_recover_no_session_alerts,
)


# ---------------------------------------------------------------------------
# Bug A — bootstrap framing
# ---------------------------------------------------------------------------


_REJECTED_FRAMING_LITERAL = (
    "\"[PollyPM bootstrap — system message, please ignore on screen]\""
)


def test_supervisor_bootstrap_drops_system_message_framing() -> None:
    """The supervisor no longer wraps the kickoff in the
    "[PollyPM bootstrap — system message, please ignore on screen]"
    header — that exact framing tripped Claude's injection defense
    (#1005). The ``/control-prompts/<session>.md`` substring relied on
    by ``transcript_matches_session`` (#935) is preserved.

    The test scans the source for the verbatim Python string literal
    (with quotes) so a comment that *names* the rejected framing for
    historical context doesn't trip the assertion.
    """
    text = (
        Path(__file__).resolve().parents[1]
        / "src" / "pollypm" / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert _REJECTED_FRAMING_LITERAL not in text
    # Conversational opener used in the new bootstrap.
    assert "please read" in text
    # Path substring used by the resume-attribution helper (#935).
    assert "control-prompts" in text


def test_tmux_session_service_bootstrap_drops_system_message_framing() -> None:
    text = (
        Path(__file__).resolve().parents[1]
        / "src" / "pollypm" / "session_services" / "tmux.py"
    ).read_text(encoding="utf-8")
    assert _REJECTED_FRAMING_LITERAL not in text
    assert "please read" in text
    assert "control-prompts" in text


# ---------------------------------------------------------------------------
# Bug B — no_session auto-recovery
# ---------------------------------------------------------------------------


@dataclass
class _FakeAlert:
    session_name: str
    alert_type: str
    severity: str
    message: str
    status: str
    created_at: str
    updated_at: str
    alert_id: int | None = None


@dataclass
class _FakeEvent:
    session_name: str
    event_type: str
    message: str
    created_at: str


@dataclass
class _FakeStore:
    """Minimal store double — only the methods auto-recovery touches."""

    alerts: list[_FakeAlert] = field(default_factory=list)
    events: list[_FakeEvent] = field(default_factory=list)
    upserted: list[tuple[str, str, str, str]] = field(default_factory=list)
    cleared: list[tuple[str, str]] = field(default_factory=list)

    def open_alerts(self) -> list[_FakeAlert]:
        return [a for a in self.alerts if a.status == "open"]

    def upsert_alert(
        self, session_name: str, alert_type: str, severity: str, message: str,
    ) -> None:
        self.upserted.append((session_name, alert_type, severity, message))
        for alert in self.alerts:
            if (
                alert.session_name == session_name
                and alert.alert_type == alert_type
                and alert.status == "open"
            ):
                alert.message = message
                alert.severity = severity
                return
        self.alerts.append(
            _FakeAlert(
                session_name=session_name,
                alert_type=alert_type,
                severity=severity,
                message=message,
                status="open",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        )

    def clear_alert(self, session_name: str, alert_type: str) -> None:
        self.cleared.append((session_name, alert_type))
        for alert in self.alerts:
            if (
                alert.session_name == session_name
                and alert.alert_type == alert_type
                and alert.status == "open"
            ):
                alert.status = "closed"

    def record_event(
        self, session_name: str, event_type: str, message: str,
    ) -> None:
        self.events.append(
            _FakeEvent(
                session_name=session_name,
                event_type=event_type,
                message=message,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )

    def recent_events(self, limit: int = 20) -> list[_FakeEvent]:
        return list(reversed(self.events))[:limit]


@dataclass
class _FakeProject:
    key: str


@dataclass
class _FakeServices:
    msg_store: _FakeStore
    state_store: _FakeStore | None = None
    known_projects: tuple[Any, ...] = ()
    config: Any = None


def _make_no_session_alert(
    *,
    session_name: str = "reviewer",
    role: str = "reviewer",
    project: str = "bikepath",
    age_seconds: int = 120,
) -> _FakeAlert:
    now = datetime.now(timezone.utc)
    created = (now - timedelta(seconds=age_seconds)).isoformat()
    return _FakeAlert(
        session_name=session_name,
        alert_type="no_session",
        severity="warn",
        message=(
            f"No worker is running for the {role} role on '{project}' — "
            f"task {project}/8 is stuck in the queue. "
            "Open Tasks or Inbox and use Approve or Reject."
        ),
        status="open",
        created_at=created,
        updated_at=created,
    )


def test_auto_recovery_skips_alerts_younger_than_threshold() -> None:
    """A fresh ``no_session`` alert (age < 60s) is left alone — gives
    the task-claim path a moment to win the race before we step in."""
    store = _FakeStore(
        alerts=[
            _make_no_session_alert(age_seconds=DEFAULT_THRESHOLD_SECONDS - 5)
        ],
    )
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )
    spawn_calls: list[tuple[str, str]] = []

    def fake_spawn(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        spawn_calls.append((role, project))
        return True, "stub"

    decisions = auto_recover_no_session_alerts(
        services, config_path=Path("/dev/null"), spawn=fake_spawn,
    )
    assert spawn_calls == []
    assert [d.outcome for d in decisions] == ["skipped_young"]


def test_auto_recovery_skips_worker_role() -> None:
    """worker-role ``no_session`` alerts are NOT auto-spawned — per-task
    workers come from ``pm task claim``, not ``pm worker-start``."""
    alert = _make_no_session_alert(role="worker", session_name="worker-bikepath")
    store = _FakeStore(alerts=[alert])
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )
    spawn_calls: list[tuple[str, str]] = []

    def fake_spawn(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        spawn_calls.append((role, project))
        return True, "stub"

    decisions = auto_recover_no_session_alerts(
        services, config_path=Path("/dev/null"), spawn=fake_spawn,
    )
    assert spawn_calls == []
    assert [d.outcome for d in decisions] == ["skipped_role"]


def test_auto_recovery_skips_unknown_projects() -> None:
    """Ghost projects (alert references a project not in the registry) are
    left to the existing ``_sweep_ghost_project_alerts`` cleanup."""
    store = _FakeStore(alerts=[_make_no_session_alert(project="ghostproj")])
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )
    spawn_calls: list[tuple[str, str]] = []

    def fake_spawn(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        spawn_calls.append((role, project))
        return True, "stub"

    decisions = auto_recover_no_session_alerts(
        services, config_path=Path("/dev/null"), spawn=fake_spawn,
    )
    assert spawn_calls == []
    assert [d.outcome for d in decisions] == ["skipped_unknown_project"]


def test_auto_recovery_spawns_reviewer_after_threshold() -> None:
    """The canonical case: a reviewer ``no_session`` alert open for >60s
    triggers a spawn attempt."""
    store = _FakeStore(alerts=[_make_no_session_alert()])
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )
    spawn_calls: list[tuple[str, str, Path]] = []

    def fake_spawn(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        spawn_calls.append((role, project, config_path))
        return True, "session=reviewer_bikepath"

    decisions = auto_recover_no_session_alerts(
        services, config_path=Path("/tmp/cfg.toml"), spawn=fake_spawn,
    )
    assert spawn_calls == [("reviewer", "bikepath", Path("/tmp/cfg.toml"))]
    assert [d.outcome for d in decisions] == ["spawned"]
    assert decisions[0].attempt_number == 1
    # Attempt event recorded so the next tick sees prior history.
    assert any(
        ev.event_type == SPAWN_ATTEMPT_EVENT_TYPE for ev in store.events
    )


def test_auto_recovery_records_failed_attempt_and_backs_off() -> None:
    """A failed spawn records an attempt event; the next tick (within
    backoff window) skips re-attempting."""
    store = _FakeStore(alerts=[_make_no_session_alert()])
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )

    def fake_spawn_fail(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        return False, "create_worker_session failed: account_unavailable"

    # First tick: attempts and fails.
    decisions1 = auto_recover_no_session_alerts(
        services, config_path=Path("/tmp/cfg.toml"), spawn=fake_spawn_fail,
    )
    assert [d.outcome for d in decisions1] == ["spawn_failed"]
    assert decisions1[0].attempt_number == 1

    # Second tick fired immediately — backoff still active.
    decisions2 = auto_recover_no_session_alerts(
        services, config_path=Path("/tmp/cfg.toml"), spawn=fake_spawn_fail,
    )
    assert [d.outcome for d in decisions2] == ["skipped_backoff"]


def test_auto_recovery_escalates_after_max_attempts() -> None:
    """After ``DEFAULT_MAX_ATTEMPTS`` failed spawns, escalate to the
    ``no_session_spawn_failed`` alert family and stop trying."""
    # Seed an aged alert + N attempt events recorded AFTER the alert
    # opened (so the per-episode counter actually counts them — see the
    # ``since`` parameter on ``_attempt_history``).
    alert = _make_no_session_alert(age_seconds=600)
    store = _FakeStore(alerts=[alert])
    alert_opened = datetime.fromisoformat(alert.created_at)
    for n in range(DEFAULT_MAX_ATTEMPTS):
        store.events.append(
            _FakeEvent(
                session_name="reviewer",
                event_type=SPAWN_ATTEMPT_EVENT_TYPE,
                message=f"prior attempt {n}",
                created_at=(
                    alert_opened + timedelta(seconds=10 + n)
                ).isoformat(),
            )
        )
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )

    spawn_calls: list[tuple[str, str]] = []

    def fake_spawn(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        spawn_calls.append((role, project))
        return True, "stub"

    decisions = auto_recover_no_session_alerts(
        services, config_path=Path("/tmp/cfg.toml"), spawn=fake_spawn,
    )
    assert spawn_calls == []  # didn't try
    assert [d.outcome for d in decisions] == ["escalated"]
    # Sibling alert raised so a human picks up.
    assert any(
        upsert[1] == SPAWN_FAILED_ALERT_TYPE
        for upsert in store.upserted
    )


def test_auto_recovery_clears_spawn_failed_after_success() -> None:
    """When auto-spawn finally succeeds, the ``no_session_spawn_failed``
    sibling alert is cleared so the cockpit reflects the recovery."""
    store = _FakeStore(alerts=[_make_no_session_alert()])
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )

    def fake_spawn_ok(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        return True, "session=reviewer_bikepath"

    auto_recover_no_session_alerts(
        services, config_path=Path("/tmp/cfg.toml"), spawn=fake_spawn_ok,
    )
    assert (
        ("reviewer", SPAWN_FAILED_ALERT_TYPE) in store.cleared
    )


def test_auto_recovery_resets_attempt_counter_per_alert_episode() -> None:
    """An attempt event recorded BEFORE the alert's ``created_at`` is
    treated as belonging to a previous recovery episode and does NOT
    count against the current attempt budget. This means a transient
    re-open after a successful recovery starts fresh, not pre-exhausted.
    """
    alert = _make_no_session_alert(age_seconds=600)
    store = _FakeStore(alerts=[alert])
    alert_opened = datetime.fromisoformat(alert.created_at)
    # Pre-seed N events from a *prior* episode (older than the current
    # alert's created_at).
    for n in range(DEFAULT_MAX_ATTEMPTS + 2):
        store.events.append(
            _FakeEvent(
                session_name="reviewer",
                event_type=SPAWN_ATTEMPT_EVENT_TYPE,
                message=f"prior episode attempt {n}",
                created_at=(
                    alert_opened - timedelta(hours=1, seconds=n)
                ).isoformat(),
            )
        )
    services = _FakeServices(
        msg_store=store, known_projects=(_FakeProject("bikepath"),),
    )

    spawn_calls: list[tuple[str, str]] = []

    def fake_spawn(*, config_path: Path, project: str, role: str) -> tuple[bool, str]:
        spawn_calls.append((role, project))
        return True, "stub"

    decisions = auto_recover_no_session_alerts(
        services, config_path=Path("/tmp/cfg.toml"), spawn=fake_spawn,
    )
    # Counter reset to 0 → first attempt fires, not escalation.
    assert spawn_calls == [("reviewer", "bikepath")]
    assert [d.outcome for d in decisions] == ["spawned"]
    assert decisions[0].attempt_number == 1


def test_auto_recovery_no_op_when_store_missing() -> None:
    services = _FakeServices(msg_store=None, state_store=None)  # type: ignore[arg-type]
    decisions = auto_recover_no_session_alerts(
        services, config_path=Path("/tmp/cfg.toml"),
    )
    assert decisions == []
