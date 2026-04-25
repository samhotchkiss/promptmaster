"""Rail-side mirror of the ``stuck_on_task`` dedup applied in cycle 53.

The project rollup (``cockpit_project_state``) already filters
``stuck_on_task:<id>`` alerts whose underlying task is in a
user-waiting state — the session sat idle because the user hadn't
responded, which is the system doing what it should, not a fault.
This test locks in the same filter at the rail's per-session glyph
layer so the ⚠ glyph next to a project doesn't fight the 🟡 dot.
"""

from __future__ import annotations

from pollypm.cockpit_rail import _stuck_alert_already_user_waiting


def test_stuck_alert_already_user_waiting_filters_when_task_is_user_waiting() -> None:
    user_waiting = frozenset({"polly_remote/12"})
    assert _stuck_alert_already_user_waiting(
        "stuck_on_task:polly_remote/12", user_waiting
    )


def test_stuck_alert_already_user_waiting_keeps_alert_for_other_tasks() -> None:
    user_waiting = frozenset({"polly_remote/12"})
    assert not _stuck_alert_already_user_waiting(
        "stuck_on_task:polly_remote/9", user_waiting
    )


def test_stuck_alert_already_user_waiting_only_acts_on_stuck_prefix() -> None:
    user_waiting = frozenset({"polly_remote/12"})
    # Other surfaceable operational alerts must keep firing on the rail
    # — the cycle 53 dedup is intentionally narrow to ``stuck_on_task:``.
    assert not _stuck_alert_already_user_waiting(
        "no_session_for_assignment:polly_remote/12", user_waiting
    )
    assert not _stuck_alert_already_user_waiting("", user_waiting)


def test_stuck_alert_already_user_waiting_handles_malformed_alert() -> None:
    user_waiting = frozenset({"polly_remote/12"})
    # Empty body or whitespace must not collapse to "covered".
    assert not _stuck_alert_already_user_waiting(
        "stuck_on_task:", user_waiting
    )
    assert not _stuck_alert_already_user_waiting(
        "stuck_on_task:   ", user_waiting
    )
