from __future__ import annotations

from pathlib import Path

import pollypm.supervisor_alerts as _supervisor_alerts
from pollypm.models import (
    AccountConfig,
    KnownProject,
    PollyPMConfig,
    PollyPMSettings,
    ProjectKind,
    ProjectSettings,
    ProviderKind,
    SessionConfig,
)
from pollypm.supervisor import Supervisor
from pollypm.tmux.client import TmuxWindow


def _config(tmp_path: Path) -> PollyPMConfig:
    return PollyPMConfig(
        project=ProjectSettings(
            root_dir=tmp_path,
            base_dir=tmp_path / ".pollypm",
            logs_dir=tmp_path / ".pollypm/logs",
            snapshots_dir=tmp_path / ".pollypm/snapshots",
            state_db=tmp_path / ".pollypm/state.db",
        ),
        pollypm=PollyPMSettings(
            controller_account="claude_controller",
            failover_enabled=True,
            failover_accounts=["codex_backup"],
        ),
        accounts={
            "claude_controller": AccountConfig(
                name="claude_controller",
                provider=ProviderKind.CLAUDE,
                email="claude@example.com",
                home=tmp_path / ".pollypm/homes/claude_controller",
            ),
            "codex_backup": AccountConfig(
                name="codex_backup",
                provider=ProviderKind.CODEX,
                email="codex@example.com",
                home=tmp_path / ".pollypm/homes/codex_backup",
            ),
        },
        sessions={
            "worker": SessionConfig(
                name="worker",
                role="worker",
                provider=ProviderKind.CODEX,
                account="codex_backup",
                cwd=tmp_path,
                project="pollypm",
                prompt="Ship the fix",
                window_name="worker-pollypm",
            ),
        },
        projects={
            "pollypm": KnownProject(
                key="pollypm",
                path=tmp_path,
                name="PollyPM",
                kind=ProjectKind.FOLDER,
            )
        },
    )


def test_supervisor_alert_helper_updates_and_nudges(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    supervisor = Supervisor(config)
    supervisor.ensure_layout()
    launch = next(item for item in supervisor.plan_launches() if item.session.name == "worker")
    window = TmuxWindow(
        session=supervisor.storage_closet_session_name(),
        index=1,
        name="worker-pollypm",
        active=False,
        pane_id="%42",
        pane_current_command="codex",
        pane_current_path=str(tmp_path),
        pane_dead=False,
    )

    for index in range(4):
        supervisor.store.record_heartbeat(
            session_name="worker",
            tmux_window=window.name,
            pane_id=window.pane_id,
            pane_command=window.pane_current_command,
            pane_dead=False,
            log_bytes=100 + index,
            snapshot_path=str(tmp_path / f"snapshot-{index}.txt"),
            snapshot_hash="same-hash",
        )
    supervisor.store.record_heartbeat(
        session_name="worker",
        tmux_window=window.name,
        pane_id=window.pane_id,
        pane_command=window.pane_current_command,
        pane_dead=False,
        log_bytes=200,
        snapshot_path=str(tmp_path / "snapshot-current.txt"),
        snapshot_hash="same-hash",
    )

    sent: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        supervisor,
        "send_input",
        lambda session_name, text, owner="pollypm", force=False, press_enter=True: sent.append(
            (session_name, text, force)
        ),
    )

    # #765 classifier gates suspected_loop on has_pending_work — simulate
    # a queued task so this stall-detection test stays focused on the
    # nudge path rather than needing a seeded work-service DB.
    monkeypatch.setattr(
        "pollypm.heartbeats.stall_classifier.has_pending_work_for_session",
        lambda config, session_name: True,
    )

    alerts = _supervisor_alerts._update_alerts(
        supervisor,
        launch,
        window,
        pane_text="Still stalled",
        previous_log_bytes=150,
        previous_snapshot_hash="same-hash",
        current_log_bytes=200,
        current_snapshot_hash="same-hash",
    )

    assert "suspected_loop" in alerts
    assert sent == [("worker", Supervisor._STALL_NUDGE_MESSAGE, False)]


def test_supervisor_wrapper_delegates_alert_helper(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    supervisor = Supervisor(config)
    supervisor.ensure_layout()
    launch = next(item for item in supervisor.plan_launches() if item.session.name == "worker")
    window = TmuxWindow(
        session=supervisor.storage_closet_session_name(),
        index=1,
        name="worker-pollypm",
        active=False,
        pane_id="%42",
        pane_current_command="codex",
        pane_current_path=str(tmp_path),
        pane_dead=False,
    )

    monkeypatch.setattr(
        _supervisor_alerts,
        "_update_alerts",
        lambda *args, **kwargs: ["delegated"],
    )

    alerts = supervisor._update_alerts(
        launch,
        window,
        pane_text="ignore",
        previous_log_bytes=None,
        previous_snapshot_hash=None,
        current_log_bytes=1,
        current_snapshot_hash="hash",
    )

    assert alerts == ["delegated"]


# ---------------------------------------------------------------------------
# #910 follow-up — record_event sites route through SignalEnvelope
# ---------------------------------------------------------------------------


class _FakeMsgStore:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def record_event(self, *, scope: str, sender: str, subject: str, payload: dict) -> int:
        self.events.append(
            {"scope": scope, "sender": sender, "subject": subject, "payload": payload}
        )
        return len(self.events)


class _FakeSupervisorBoundary:
    """Minimal stand-in for SupervisorAlertBoundary suitable for the
    record_event-routing test. Only ``msg_store`` is exercised by the
    funnel under test."""

    def __init__(self) -> None:
        self.msg_store = _FakeMsgStore()


def test_supervisor_alerts_emit_routed_event_routes_through_signal_envelope(
    monkeypatch,
) -> None:
    """#910 follow-up — every event written through
    ``_emit_routed_event`` must construct a SignalEnvelope and pass
    it through ``route_signal`` BEFORE the legacy
    ``msg_store.record_event`` write.

    Patches the funnel's ``_route_signal`` reference with a recording
    stub so the test can read back the envelope and confirm:
      * the envelope was built and routed (call count == 1),
      * the legacy persistence still ran exactly once,
      * the envelope carries OPERATIONAL actionability + OPERATOR
        audience (so the routing policy lands it on Activity only),
      * the dedupe key names the source + subject + scope, matching
        the convention used by the heartbeat-side funnel.
    """
    from pollypm.signal_routing import (
        SignalActionability,
        SignalAudience,
    )

    captured: list = []

    def _record(envelope):
        captured.append(envelope)
        return None  # route_signal return value unused by funnel

    monkeypatch.setattr(_supervisor_alerts, "_route_signal", _record)

    boundary = _FakeSupervisorBoundary()
    _supervisor_alerts._emit_routed_event(
        boundary,
        scope="worker",
        sender="worker",
        subject="heartbeat_nudge_skipped",
        payload={"message": "Skipped"},
    )

    assert len(captured) == 1
    env = captured[0]
    assert env.source == "supervisor_alerts"
    assert env.subject == "heartbeat_nudge_skipped"
    assert env.audience is SignalAudience.OPERATOR
    assert env.actionability is SignalActionability.OPERATIONAL
    assert env.dedupe_key is not None
    assert "supervisor_alerts" in env.dedupe_key
    assert "heartbeat_nudge_skipped" in env.dedupe_key
    assert "worker" in env.dedupe_key
    assert env.body == "Skipped"

    # Legacy persistence still happens — the funnel preserves the
    # event-store write so existing readers don't regress.
    assert len(boundary.msg_store.events) == 1
    persisted = boundary.msg_store.events[0]
    assert persisted["subject"] == "heartbeat_nudge_skipped"
    assert persisted["scope"] == "worker"


def test_supervisor_alerts_heartbeat_nudge_skipped_path_routes_through_funnel(
    monkeypatch, tmp_path: Path,
) -> None:
    """#910 follow-up — the human-leased worker nudge-skip site
    (formerly a raw ``msg_store.record_event`` call) now goes through
    ``_emit_routed_event``. Exercising the public
    ``_maybe_nudge_stalled_session`` entry point asserts the funnel
    runs end-to-end on the legacy code path."""
    from pollypm.signal_routing import SignalActionability

    config = _config(tmp_path)
    supervisor = Supervisor(config)
    supervisor.ensure_layout()
    launch = next(
        item for item in supervisor.plan_launches() if item.session.name == "worker"
    )

    # Force the human-leased branch in _maybe_nudge_stalled_session.
    supervisor.store.set_lease(launch.session.name, "human")

    captured: list = []

    def _record(envelope):
        captured.append(envelope)
        return None

    monkeypatch.setattr(_supervisor_alerts, "_route_signal", _record)

    _supervisor_alerts._maybe_nudge_stalled_session(supervisor, launch)

    matching = [
        env for env in captured
        if env.dedupe_key and "heartbeat_nudge_skipped" in env.dedupe_key
    ]
    assert matching, [env.dedupe_key for env in captured]
    env = matching[0]
    assert env.source == "supervisor_alerts"
    assert env.actionability is SignalActionability.OPERATIONAL
