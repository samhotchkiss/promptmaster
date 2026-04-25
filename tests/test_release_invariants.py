"""Focused tests for the release-invariants user_prompt contract check.

The release-invariants script is a v1 burn-in harness rather than
product code, but the user_prompt-contract check is a forward-pressure
signal for the dashboard contract migration — losing it silently
would let regressions slip through. Test the predicates directly so
the contract semantics are pinned down even when no live workspace
is available.
"""

from __future__ import annotations

import json

from scripts.release_invariants import (
    _message_action_requires_user_prompt,
    _user_prompt_complete,
)


def _row(**fields):
    """sqlite3.Row supports __getitem__; dict matches that interface."""
    payload = fields.pop("payload", None)
    if payload is not None:
        fields["payload_json"] = json.dumps(payload)
    fields.setdefault("recipient", "user")
    fields.setdefault("type", "notify")
    fields.setdefault("tier", "immediate")
    fields.setdefault("payload_json", "{}")
    return fields


def test_user_prompt_complete_requires_at_least_one_field() -> None:
    """``_user_prompt_complete`` is the contract-quality gate: a payload
    that has *some* user-facing copy passes; an empty/missing prompt
    does not."""
    assert _user_prompt_complete({"user_prompt": {"summary": "Plan ready"}})
    assert _user_prompt_complete(
        {"user_prompt": {"steps": ["Open the plan"]}}
    )
    assert _user_prompt_complete(
        {"user_prompt": {"question": "Approve now or wait?"}}
    )
    assert _user_prompt_complete(
        {"user_prompt": {"required_actions": ["Provision Fly.io"]}}
    )

    assert not _user_prompt_complete({})
    assert not _user_prompt_complete({"user_prompt": None})
    assert not _user_prompt_complete({"user_prompt": "not a dict"})
    assert not _user_prompt_complete({"user_prompt": {}})
    assert not _user_prompt_complete(
        {"user_prompt": {"summary": "", "steps": [], "question": ""}}
    )


def test_user_action_predicate_targets_immediate_user_notify() -> None:
    """Only open immediate-priority notify/alert messages routed to
    the user count as user-blocking action calls. Digest tier, agent
    recipients, and non-action types fall outside the contract."""
    assert _message_action_requires_user_prompt(
        _row(recipient="user", type="notify", tier="immediate")
    )
    assert _message_action_requires_user_prompt(
        _row(recipient="user", type="alert", tier="immediate")
    )

    # Digest priority is routine progress, not a call to action.
    assert not _message_action_requires_user_prompt(
        _row(recipient="user", type="notify", tier="digest")
    )
    # Notifications routed to an agent (e.g. Polly) aren't user-facing.
    assert not _message_action_requires_user_prompt(
        _row(recipient="polly", type="notify", tier="immediate")
    )
    # inbox_task routes through the work-service surface, not the
    # dashboard contract.
    assert not _message_action_requires_user_prompt(
        _row(recipient="user", type="inbox_task", tier="immediate")
    )


def test_user_action_predicate_excludes_blocker_summary_events() -> None:
    """Project blocker summaries already carry their structured copy
    via ``required_actions`` and are rendered by the dashboard's
    blocker-summary path — they shouldn't trip the user_prompt
    contract warning."""
    assert not _message_action_requires_user_prompt(
        _row(
            recipient="user",
            type="notify",
            tier="immediate",
            payload={"event_type": "project_blocker_summary"},
        )
    )
