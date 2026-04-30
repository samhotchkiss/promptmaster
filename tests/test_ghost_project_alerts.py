"""Regression tests for issue #1001 — alerts re-fire for ghost projects.

Background:
- ``pm alerts`` repeatedly raised ``no_session_for_assignment:<proj>/<n>``
  and ``worker-<proj>/no_session`` alerts for projects that no longer
  exist in ``pm projects`` (deregistered, or never registered to begin
  with). Each heartbeat tick the task_assignment sweep re-fired the
  alerts because the per-project DB still held a row referencing a
  project key whose registry entry had been removed.
- The supervisor's ``_sweep_stale_alerts`` (#919) intentionally skips
  ``no_session*`` alert types — the task_assignment sweep is the sole
  owner. So the cleanup has to live inside that sweep itself.

Two-part fix exercised here:
  Part A — fire-path guard. ``_escalate_no_session`` and the sweep's
  ``_emit_no_session_alert`` skip raising when ``event.project`` is not
  in ``services.known_projects`` (when the registry is non-empty —
  empty means no config / test mode and we keep the legacy unrestricted
  behaviour).
  Part B — auto-clear sweep. Each ``task_assignment_sweep_handler``
  tick walks open ``no_session_for_assignment:<proj>/<n>`` and project-
  level ``no_session`` alerts and clears any whose project key isn't
  in the current registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pollypm.plugins_builtin.task_assignment_notify.handlers.sweep import (
    task_assignment_sweep_handler,
)
from pollypm.plugins_builtin.task_assignment_notify.resolver import (
    _RuntimeServices,
    _escalate_no_session,
)
from pollypm.storage.state import StateStore
from pollypm.work import task_assignment as bus
from pollypm.work.models import ActorType
from pollypm.work.task_assignment import TaskAssignmentEvent
from datetime import datetime, timezone


@dataclass
class _FakeKnownProject:
    """Minimal stand-in for ``pollypm.config.KnownProject``."""
    key: str
    path: Path
    auto_claim: bool | None = None
    enforce_plan: bool | None = None


@dataclass
class _FakeHandle:
    name: str


@dataclass
class _FakeSessionService:
    handles: list[_FakeHandle] = field(default_factory=list)
    sent: list[tuple[str, str]] = field(default_factory=list)
    busy: set[str] = field(default_factory=set)

    def list(self) -> list[_FakeHandle]:
        return list(self.handles)

    def send(self, name: str, text: str, *, press_enter: bool = True) -> None:
        self.sent.append((name, text))

    def is_turn_active(self, name: str) -> bool:
        return name in self.busy


def _open_alerts(store: StateStore) -> list[tuple[str, str]]:
    return [
        (alert.session_name, alert.alert_type) for alert in store.open_alerts()
    ]


def _make_event(project: str, task_number: int = 1) -> TaskAssignmentEvent:
    return TaskAssignmentEvent(
        task_id=f"{project}/{task_number}",
        project=project,
        task_number=task_number,
        title="Ghost work",
        current_node="work",
        current_node_kind="work",
        actor_type=ActorType.ROLE,
        actor_name="worker",
        work_status="queued",
        priority="normal",
        transitioned_at=datetime.now(timezone.utc),
        transitioned_by="tester",
    )


def _install_sweep_loader(monkeypatch, services: _RuntimeServices) -> None:
    monkeypatch.setattr(
        "pollypm.plugins_builtin.task_assignment_notify.handlers.sweep.load_runtime_services",
        lambda *, config_path=None: services,
    )


# ---------------------------------------------------------------------------
# Part A — fire-path guard
# ---------------------------------------------------------------------------


class TestEscalateSkipsGhostProjects:
    def test_escalate_no_session_drops_when_project_missing_from_registry(
        self, tmp_path,
    ):
        """``_escalate_no_session`` must NOT raise when ``event.project``
        isn't in ``services.known_projects`` — the project was
        deregistered or never existed.
        """
        bus.clear_listeners()
        store = StateStore(tmp_path / "state.db")
        registered = _FakeKnownProject(key="real-project", path=tmp_path)
        services = _RuntimeServices(
            session_service=None, state_store=store,
            work_service=None, project_root=tmp_path, msg_store=store,
            known_projects=(registered,),
        )

        event = _make_event("ghost-project")
        _escalate_no_session(event, services.msg_store, services=services)

        # No alert should have been raised — the project is a ghost.
        assert _open_alerts(store) == [], (
            "fire-path must drop no_session_for_assignment for projects "
            "missing from the registry"
        )

    def test_escalate_no_session_still_fires_for_registered_projects(
        self, tmp_path,
    ):
        """The guard is project-existence based — registered projects
        keep the existing escalate behaviour.
        """
        bus.clear_listeners()
        store = StateStore(tmp_path / "state.db")
        registered = _FakeKnownProject(key="real-project", path=tmp_path)
        services = _RuntimeServices(
            session_service=None, state_store=store,
            work_service=None, project_root=tmp_path, msg_store=store,
            known_projects=(registered,),
        )

        event = _make_event("real-project")
        _escalate_no_session(event, services.msg_store, services=services)

        open_keys = _open_alerts(store)
        assert (
            "task_assignment", "no_session_for_assignment:real-project/1",
        ) in open_keys

    def test_escalate_no_session_unrestricted_when_registry_empty(
        self, tmp_path,
    ):
        """Empty ``known_projects`` (no config / test mode) preserves
        the legacy unrestricted behaviour — we only filter when there's
        an explicit registry to filter against.
        """
        bus.clear_listeners()
        store = StateStore(tmp_path / "state.db")
        services = _RuntimeServices(
            session_service=None, state_store=store,
            work_service=None, project_root=tmp_path, msg_store=store,
            known_projects=(),
        )

        event = _make_event("any-project")
        _escalate_no_session(event, services.msg_store, services=services)

        open_keys = _open_alerts(store)
        assert (
            "task_assignment", "no_session_for_assignment:any-project/1",
        ) in open_keys


# ---------------------------------------------------------------------------
# Part B — auto-clear sweep on the heartbeat tick
# ---------------------------------------------------------------------------


class TestSweepClearsGhostProjectAlerts:
    def test_sweep_clears_open_alerts_for_unregistered_projects(
        self, tmp_path, monkeypatch,
    ):
        """An open ``no_session_for_assignment:<ghost>/<n>`` alert plus a
        project-level ``worker-<ghost>/no_session`` alert — both must be
        cleared by the sweep tick when ``<ghost>`` isn't in the registry.
        """
        bus.clear_listeners()
        store = StateStore(tmp_path / "state.db")

        # Pre-seed both alert families against a project the registry
        # doesn't know about.
        store.upsert_alert(
            "task_assignment",
            "no_session_for_assignment:wordgame/1",
            "warning",
            "Task wordgame/1 was routed to the worker role but no matching session is running.",
        )
        store.upsert_alert(
            "worker-wordgame",
            "no_session",
            "warn",
            "No worker is running for the worker role on 'wordgame'.",
        )

        # Registry knows about a different project — wordgame is the ghost.
        registered = _FakeKnownProject(key="real-project", path=tmp_path)
        services = _RuntimeServices(
            session_service=_FakeSessionService(),
            state_store=store,
            work_service=None,  # no work_service → workspace-root sweep skipped
            project_root=tmp_path, msg_store=store,
            known_projects=(registered,),
        )
        _install_sweep_loader(monkeypatch, services)

        task_assignment_sweep_handler({})

        open_keys = _open_alerts(store)
        assert (
            "task_assignment", "no_session_for_assignment:wordgame/1",
        ) not in open_keys, (
            f"sweep must clear per-task ghost alert; saw {open_keys!r}"
        )
        assert (
            "worker-wordgame", "no_session",
        ) not in open_keys, (
            f"sweep must clear project-level ghost alert; saw {open_keys!r}"
        )

    def test_sweep_keeps_alerts_for_registered_projects(
        self, tmp_path, monkeypatch,
    ):
        """The sweep ghost-clear pass must not touch alerts whose project
        is still in the registry — those are live signal.
        """
        bus.clear_listeners()
        store = StateStore(tmp_path / "state.db")

        store.upsert_alert(
            "task_assignment",
            "no_session_for_assignment:real-project/3",
            "warning", "Task real-project/3 ...",
        )
        store.upsert_alert(
            "worker-real-project", "no_session", "warn",
            "No worker is running ...",
        )

        registered = _FakeKnownProject(key="real-project", path=tmp_path)
        services = _RuntimeServices(
            session_service=_FakeSessionService(),
            state_store=store, work_service=None,
            project_root=tmp_path, msg_store=store,
            known_projects=(registered,),
        )
        _install_sweep_loader(monkeypatch, services)

        task_assignment_sweep_handler({})

        open_keys = _open_alerts(store)
        assert (
            "task_assignment", "no_session_for_assignment:real-project/3",
        ) in open_keys
        assert (
            "worker-real-project", "no_session",
        ) in open_keys

    def test_sweep_no_op_when_registry_empty(self, tmp_path, monkeypatch):
        """Empty registry preserves the legacy behaviour — we don't
        clear alerts when we have no list to match against.
        """
        bus.clear_listeners()
        store = StateStore(tmp_path / "state.db")
        store.upsert_alert(
            "task_assignment",
            "no_session_for_assignment:any-project/1",
            "warning", "msg",
        )
        services = _RuntimeServices(
            session_service=_FakeSessionService(),
            state_store=store, work_service=None,
            project_root=tmp_path, msg_store=store,
            known_projects=(),
        )
        _install_sweep_loader(monkeypatch, services)

        task_assignment_sweep_handler({})

        open_keys = _open_alerts(store)
        assert (
            "task_assignment", "no_session_for_assignment:any-project/1",
        ) in open_keys
