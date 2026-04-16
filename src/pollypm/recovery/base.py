"""Recovery policy protocol and shared data types.

A ``RecoveryPolicy`` classifies session health from raw signals and
selects an intervention (or none) based on the health plus a history of
previous interventions. Policies make decisions only — the Supervisor
applies them (sending nudges, relaunching sessions, clearing alerts).

This module intentionally keeps the surface tiny:

  * :class:`SessionSignals` — the inputs a policy sees.
  * :class:`SessionHealth` — the classification output.
  * :class:`InterventionAction` — the decision output.
  * :class:`InterventionHistoryEntry` — per-session intervention history.
  * :class:`RecoveryPolicy` — the protocol plugins implement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pollypm.capacity import CapacityState


# ---------------------------------------------------------------------------
# Health classifications
# ---------------------------------------------------------------------------


class SessionHealth(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    STUCK = "stuck"
    LOOPING = "looping"
    EXITED = "exited"
    ERROR = "error"
    BLOCKED_NO_CAPACITY = "blocked_no_capacity"
    AUTH_BROKEN = "auth_broken"
    WAITING_ON_USER = "waiting_on_user"
    HEALTHY = "healthy"


# Intervention escalation ladder
INTERVENTION_LADDER = (
    "nudge",       # Send a reminder message to idle sessions
    "reset",       # Reset the session state
    "relaunch",    # Kill and relaunch the session
    "failover",    # Switch to a different account
    "escalate",    # Alert the operator for manual intervention
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SessionSignals:
    """Collected signals for a single session."""

    session_name: str
    window_present: bool = True
    pane_dead: bool = False
    output_stale: bool = False
    snapshot_repeated: int = 0  # Consecutive identical snapshots
    auth_failure: bool = False
    has_transcript_delta: bool = False
    capacity_state: CapacityState = CapacityState.UNKNOWN
    last_verdict: str = ""
    idle_cycles: int = 0


@dataclass(slots=True)
class InterventionAction:
    """An intervention to take for a session."""

    session_name: str
    action: str  # One of INTERVENTION_LADDER
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InterventionHistoryEntry:
    """One prior intervention attempt for a session.

    The history is an ordered list (oldest first) of attempts the
    Supervisor has already made. Policies use this to walk the
    escalation ladder — e.g. nudge first, then reset, then relaunch.
    """

    action: str
    reason: str = ""
    at: str | None = None  # ISO-8601 timestamp (optional)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class RecoveryPolicy(Protocol):
    """Protocol for pluggable session-recovery decision makers.

    Implementations are pure — no state is owned by the policy, no side
    effects. The heartbeat loop / Supervisor calls ``classify`` then
    ``select_intervention`` and applies the returned action.
    """

    name: str

    def classify(self, signals: SessionSignals) -> SessionHealth:
        """Classify a session's health from collected signals."""
        ...

    def select_intervention(
        self,
        health: SessionHealth,
        signals: SessionSignals,
        history: list[InterventionHistoryEntry],
    ) -> InterventionAction | None:
        """Select the appropriate intervention, or ``None`` if nothing to do.

        ``history`` is the ordered list of prior interventions already
        attempted for this session. Policies walk the intervention ladder
        as the history grows.
        """
        ...
