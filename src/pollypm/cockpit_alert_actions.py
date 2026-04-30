"""Per-alert-type recovery action map for the cockpit rail.

Contract (#989):
- Inputs: an alert (from ``supervisor.store.open_alerts()``) plus the
  alert's resolved session name.
- Outputs: a list of :class:`AlertActionPlan` describing what the user
  can do about the alert without dropping to the CLI. Plans carry a
  human-readable label, a stable ``kind`` discriminator, and any payload
  the cockpit needs to execute (target route key, target session, etc.).
- Side effects: none. Action execution lives in the cockpit Textual
  app; this module is a pure descriptor builder so it is trivial to
  test and reuse from both the rail and Worker-roster surfaces.
- Invariants: the same map drives the alert-detail modal's button row
  AND the rail key-binding map. Adding a new alert type means editing
  one place, not two.

Why a structured map instead of an ad-hoc switch in the modal:
- The follow-up comment on #989 was explicit that the See-it / Recover-it
  pair has to be one pattern, not two. A registry makes it impossible
  for the modal to render a recovery button that the keyboard handler
  doesn't know about, and vice versa.
- Tests can iterate the registry to assert every alert type the
  supervisor raises has at least one plan — bare ``raise`` paths used
  to ship and only get caught when a user hit them in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AlertActionKind = Literal[
    "restart_session",       # tear down + relaunch the alerted session
    "resume_recovery",       # clear recovery_limit + reset attempt counter
    "view_pane",             # focus the alert's pane (rail "session" route)
    "route_inbox",           # jump to inbox (with optional task filter)
    "route_chat_pm",         # jump to project PM chat (existing `c` flow)
    "route_settings_accounts",  # Settings → Accounts for credential repair
    "acknowledge",           # explicitly clear the alert
]


@dataclass(frozen=True, slots=True)
class AlertActionPlan:
    """One recovery action surfaced for an alerted rail row."""

    kind: AlertActionKind
    label: str
    # Optional payload — interpreted per ``kind``. Examples:
    # - ``session_name``: the supervisor session to act on for
    #   ``restart_session`` / ``resume_recovery`` / ``view_pane``.
    # - ``project_key``: the project to route to for ``route_chat_pm``
    #   and ``route_inbox`` flows.
    # - ``task_id``: optional ``project/N`` filter for ``route_inbox``.
    session_name: str | None = None
    project_key: str | None = None
    task_id: str | None = None
    # Free-form hint that the cockpit can surface alongside the label
    # (e.g. "clears the pause flag and respawns the session"). Pure
    # presentation — the button label stays short.
    hint: str | None = None


def recovery_actions_for(
    alert_type: str,
    *,
    session_name: str | None = None,
    project_key: str | None = None,
    task_id: str | None = None,
    severity: str | None = None,
) -> list[AlertActionPlan]:
    """Return the canonical recovery plan list for a single alert.

    Returns an empty list for alerts that have no in-cockpit action
    (the modal still surfaces the message + the explicit Acknowledge
    fallback). The supervisor session name is required for restart-
    style actions; project key drives chat-PM / inbox forwards.
    """
    plans: list[AlertActionPlan] = []
    family = _alert_family(alert_type)

    if family == "recovery_limit" and session_name:
        plans.append(
            AlertActionPlan(
                kind="resume_recovery",
                label="Resume auto-recovery",
                session_name=session_name,
                hint="Clears the pause flag and resets the recovery counter.",
            )
        )
        plans.append(
            AlertActionPlan(
                kind="restart_session",
                label="Restart now",
                session_name=session_name,
                hint="Tears down the tmux window and relaunches the session.",
            )
        )
        # ``recovery_limit`` is sometimes a credential-failure surface —
        # forward the user to Settings → Accounts when restarting alone
        # won't fix it. Listed last so it doesn't crowd the common path.
        plans.append(
            AlertActionPlan(
                kind="route_settings_accounts",
                label="Open Accounts",
                hint="Use this when the worker can't authenticate.",
            )
        )
    elif family == "pane:permission_prompt":
        if session_name:
            plans.append(
                AlertActionPlan(
                    kind="view_pane",
                    label="View pane",
                    session_name=session_name,
                    hint="Opens the agent pane so you can answer the prompt.",
                )
            )
        plans.append(
            AlertActionPlan(
                kind="acknowledge",
                label="Acknowledge",
                session_name=session_name,
                hint="Clear the alert; the agent stays at the prompt.",
            )
        )
    elif family == "pane:stuck_on_error":
        if session_name:
            plans.append(
                AlertActionPlan(
                    kind="view_pane",
                    label="View pane",
                    session_name=session_name,
                    hint="Opens the agent pane so you can read the error.",
                )
            )
            plans.append(
                AlertActionPlan(
                    kind="restart_session",
                    label="Restart",
                    session_name=session_name,
                    hint="Stop the stuck pane and relaunch.",
                )
            )
    elif family == "no_session_for_assignment":
        plans.append(
            AlertActionPlan(
                kind="route_inbox",
                label="Open Inbox",
                project_key=project_key,
                task_id=task_id,
                hint="Approve / reject the task to clear the assignment.",
            )
        )
    elif family == "plan_missing":
        if project_key:
            plans.append(
                AlertActionPlan(
                    kind="route_chat_pm",
                    label="Ask PM to plan",
                    project_key=project_key,
                    hint="Same as pressing c on the project dashboard.",
                )
            )
    elif family == "no_session":
        plans.append(
            AlertActionPlan(
                kind="route_inbox",
                label="Open Inbox",
                project_key=project_key,
                hint="Approve / reject from the inbox to unstick the task.",
            )
        )
    elif family == "stuck_on_task":
        plans.append(
            AlertActionPlan(
                kind="route_inbox",
                label="Open Inbox",
                project_key=project_key,
                task_id=task_id,
                hint="The task is waiting on you.",
            )
        )

    # Every alert gets an explicit Acknowledge fallback at the end so
    # users always have a one-keystroke escape hatch. We skip it for
    # the permission-prompt family because Acknowledge is already in
    # the primary list above.
    if not any(plan.kind == "acknowledge" for plan in plans):
        plans.append(
            AlertActionPlan(
                kind="acknowledge",
                label="Acknowledge",
                session_name=session_name,
                hint="Clear this alert.",
            )
        )

    del severity  # reserved for future severity-conditional plans
    return plans


def _alert_family(alert_type: str) -> str:
    """Return the family key used to drive the recovery action map.

    The supervisor sometimes encodes a payload inside the alert type
    (``no_session_for_assignment:wordgame/3``,
    ``stuck_on_task:demo/9``). The family is the prefix; the payload
    feeds back into the action plan as ``task_id`` / ``project_key``.
    """
    if not alert_type:
        return ""
    if ":" not in alert_type:
        return alert_type
    head, _, _ = alert_type.partition(":")
    # ``pane:stuck_on_error`` and ``pane:permission_prompt`` are real
    # families in their own right, not payloads — preserve the second
    # segment when the head is ``pane``.
    if head == "pane":
        return alert_type
    return head


def task_id_from_alert_type(alert_type: str) -> str | None:
    """Extract the ``project/N`` task id encoded in the alert type, if any."""
    if not alert_type or ":" not in alert_type:
        return None
    family = _alert_family(alert_type)
    if family in {"no_session_for_assignment", "stuck_on_task"}:
        _, _, payload = alert_type.partition(":")
        payload = payload.strip()
        return payload or None
    return None


__all__ = [
    "AlertActionKind",
    "AlertActionPlan",
    "recovery_actions_for",
    "task_id_from_alert_type",
]
