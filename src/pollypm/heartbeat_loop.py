"""End-to-end heartbeat supervision loop.

Wires together signal collection, health classification, intervention
escalation, checkpoint recording, and capacity management into a
single supervision cycle.

The classification + escalation decision-making lives in
:mod:`pollypm.recovery` — this module re-exports the canonical types for
back-compat and owns the per-cycle plumbing (``run_heartbeat_cycle``,
``assess_project_state``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pollypm.capacity import (
    can_failover_session,
    probe_capacity,
)
from pollypm.models import PollyPMConfig
from pollypm.recovery.base import (
    INTERVENTION_LADDER,
    InterventionAction,
    InterventionHistoryEntry,
    SessionHealth,
    SessionSignals,
)
from pollypm.recovery.default import DefaultRecoveryPolicy
from pollypm.storage.state import StateStore

logger = logging.getLogger(__name__)


# Re-exports kept for back-compat. New code should import from
# ``pollypm.recovery``.
__all__ = [
    "HeartbeatCycleResult",
    "INTERVENTION_LADDER",
    "InterventionAction",
    "SessionHealth",
    "SessionSignals",
    "assess_project_state",
    "classify_session_health",
    "run_heartbeat_cycle",
    "select_intervention",
]


@dataclass(slots=True)
class HeartbeatCycleResult:
    """Result of a single heartbeat supervision cycle."""

    timestamp: str
    sessions_checked: int = 0
    classifications: dict[str, SessionHealth] = field(default_factory=dict)
    interventions: list[InterventionAction] = field(default_factory=list)
    checkpoints_recorded: int = 0
    alerts_raised: int = 0
    capacity_probes: int = 0


# ---------------------------------------------------------------------------
# Back-compat function facade over the default policy
# ---------------------------------------------------------------------------


_DEFAULT_POLICY = DefaultRecoveryPolicy()


def classify_session_health(signals: SessionSignals) -> SessionHealth:
    """Classify session health from collected signals.

    Thin wrapper over :meth:`DefaultRecoveryPolicy.classify` kept for
    back-compat with call sites that predate the plugin. New code should
    resolve a :class:`RecoveryPolicy` via the plugin host.
    """
    return _DEFAULT_POLICY.classify(signals)


def select_intervention(
    health: SessionHealth,
    signals: SessionSignals,
    *,
    previous_interventions: int = 0,
) -> InterventionAction | None:
    """Select the appropriate intervention based on health and history.

    Thin wrapper over :meth:`DefaultRecoveryPolicy.select_intervention`
    that accepts the legacy ``previous_interventions`` count. New code
    should pass a full :class:`InterventionHistoryEntry` list to the
    policy directly.
    """
    history = [
        InterventionHistoryEntry(action="") for _ in range(previous_interventions)
    ]
    return _DEFAULT_POLICY.select_intervention(health, signals, history)


# ---------------------------------------------------------------------------
# Heartbeat cycle
# ---------------------------------------------------------------------------


def run_heartbeat_cycle(
    config: PollyPMConfig,
    store: StateStore,
    *,
    session_signals: list[SessionSignals],
) -> HeartbeatCycleResult:
    """Run a single heartbeat supervision cycle.

    This is the core loop that:
    1. Classifies health for each session
    2. Checks capacity for affected accounts
    3. Selects interventions
    4. Records results

    Actual intervention execution (nudge, reset, relaunch, failover)
    is handled by the supervisor — this function determines WHAT to do.
    """
    result = HeartbeatCycleResult(
        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        sessions_checked=len(session_signals),
    )

    for signals in session_signals:
        # Classify health
        health = classify_session_health(signals)
        result.classifications[signals.session_name] = health

        # Check if intervention is needed
        runtime = store.get_session_runtime(signals.session_name)
        previous_interventions = runtime.recovery_attempts if runtime else 0

        intervention = select_intervention(
            health, signals,
            previous_interventions=previous_interventions,
        )

        if intervention is not None:
            # Check lease before failover
            if intervention.action == "failover":
                can_fail, lease_reason = can_failover_session(store, signals.session_name)
                if not can_fail:
                    intervention = InterventionAction(
                        session_name=signals.session_name,
                        action="escalate",
                        reason=f"Failover blocked: {lease_reason}",
                    )

            result.interventions.append(intervention)
            result.alerts_raised += 1

        # Record checkpoint count
        result.checkpoints_recorded += 1

    return result


# ---------------------------------------------------------------------------
# Quick project state assessment
# ---------------------------------------------------------------------------


def assess_project_state(
    config: PollyPMConfig,
    store: StateStore,
    project_key: str,
) -> dict[str, Any]:
    """Quick project state assessment using checkpoint + issue tracker + overview."""
    state: dict[str, Any] = {
        "project_key": project_key,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sessions": {},
        "open_alerts": [],
        "capacity": {},
    }

    # Session states
    for session_name, session_config in config.sessions.items():
        if session_config.project != project_key and project_key != "pollypm":
            continue
        runtime = store.get_session_runtime(session_name)
        state["sessions"][session_name] = {
            "role": session_config.role,
            "status": runtime.status if runtime else "unknown",
            "account": session_config.account,
        }

    # Open alerts
    for alert in store.open_alerts():
        state["open_alerts"].append({
            "session": alert.session_name,
            "type": alert.alert_type,
            "severity": alert.severity,
            "message": alert.message,
        })

    # Capacity state
    for account_name in config.accounts:
        probe = probe_capacity(config, store, account_name)
        state["capacity"][account_name] = {
            "state": probe.state.value,
            "remaining_pct": probe.remaining_pct,
            "reason": probe.reason,
        }
        result = HeartbeatCycleResult(
            timestamp=state["timestamp"],
        )

    return state
