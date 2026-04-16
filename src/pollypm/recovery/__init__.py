"""Recovery policy — pluggable health classification and intervention selection.

A ``RecoveryPolicy`` is the "brain" of the heartbeat supervision loop. Given
raw signals about a session, it decides:

  * :meth:`classify` — how healthy is this session right now?
  * :meth:`select_intervention` — what, if anything, should be done about it?

Policies are pure decision makers: they inspect signals and history, then
return an :class:`InterventionAction` (or ``None``). Applying that action —
sending keystrokes, relaunching panes, clearing alerts — is the
Supervisor's job. This split keeps the policy easy to test in isolation
and easy to swap out per-deployment.

The default implementation lives in :mod:`pollypm.recovery.default` and is
shipped via the built-in ``default_recovery_policy`` plugin. Third-party
plugins can register alternative policies under the ``recovery_policies``
factory kind.
"""

from __future__ import annotations

from pathlib import Path

from pollypm.recovery.base import (
    INTERVENTION_LADDER,
    InterventionAction,
    InterventionHistoryEntry,
    RecoveryPolicy,
    SessionHealth,
    SessionSignals,
)

__all__ = [
    "INTERVENTION_LADDER",
    "InterventionAction",
    "InterventionHistoryEntry",
    "RecoveryPolicy",
    "SessionHealth",
    "SessionSignals",
    "get_recovery_policy",
]


def get_recovery_policy(
    name: str = "default",
    *,
    root_dir: Path | None = None,
    **kwargs: object,
) -> RecoveryPolicy:
    """Resolve a recovery-policy implementation by name via the plugin host."""
    from pollypm.plugin_host import extension_host_for_root

    root = str((root_dir or Path.cwd()).resolve())
    return extension_host_for_root(root).get_recovery_policy(name, **kwargs)
