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
    _dashboard_body_has_action_copy,
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


def test_dashboard_body_action_copy_canonical_lead() -> None:
    """The canonical 'To move this project forward' lead counts as action copy."""
    body = (
        "[#f85149][b]to move this project forward[/b][/]\n"
        "  ◆ A full project plan is ready for your review.\n"
    )
    assert _dashboard_body_has_action_copy(body.lower())


def test_dashboard_body_action_copy_on_hold_decision() -> None:
    """The on-hold inbox treatment + decision question counts as action copy.

    Media (2026-04-26): on_hold task with hold reason ``Awaiting user
    Phase A approval...`` rendered as ``On hold`` + ``Decide whether
    to approve…``. The strict ``to move this project forward`` rule
    flagged this as missing copy, but the user-facing decision is
    present and clear — this shape is valid action copy.
    """
    body = (
        "[#f0c45a][b]On hold[/b][/]\n"
        "  These are the root holds keeping downstream work waiting.\n"
        "  [#f0c45a]◆[/#f0c45a] [b]#1 Library-wide cleanup[/b]\n"
        "  Decide whether to approve the scoped code delivery, split "
        "operational acceptance, or provide the missing access/credentials.\n"
    )
    assert _dashboard_body_has_action_copy(body.lower())


def test_dashboard_body_action_copy_missing_when_no_signal() -> None:
    """A body with no lead, no on-hold treatment, no diagnostic still fails."""
    body = (
        "[b]Inbox[/b]\n"
        "  No project inbox items are open.\n"
        "  Recent: worker shipped task #1.\n"
    )
    assert not _dashboard_body_has_action_copy(body.lower())


def test_dashboard_body_action_copy_on_hold_alone_is_not_enough() -> None:
    """``On hold`` framing without a decision question is NOT action copy.

    The user needs to know what to do next. ``On hold`` alone — without
    ``Decide whether``, ``Decision:``, or ``needs your`` — is a status
    label, not a call to action.
    """
    body = (
        "[#f0c45a][b]On hold[/b][/]\n"
        "  These are the root holds keeping downstream work waiting.\n"
        "  ◆ #1 Library-wide cleanup\n"
        "  paused: ran out of budget for tonight, will resume tomorrow.\n"
    )
    assert not _dashboard_body_has_action_copy(body.lower())


def test_blocked_with_incoming_dependency_does_not_warn(tmp_path) -> None:
    """A blocked task that has an INCOMING dependency row (something
    blocks it) must NOT trip ``blocked_without_context``.

    Booktalk live (2026-04-26): tasks #9, #11, #14 were genuinely
    blocked by prerequisites (e.g. ``from 7 → to 9``) but the
    invariant queried the OUTGOING direction (``from = task``,
    "this task is a prerequisite for someone else") and warned
    even though the dependency context was right there in the
    schema. Reverse the query direction; the warning now only
    fires when there's truly no schema-level explanation.
    """
    import sqlite3
    from scripts.release_invariants import check_project_tasks

    proj_dir = tmp_path / "demo"
    (proj_dir / ".pollypm").mkdir(parents=True)
    db = proj_dir / ".pollypm" / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE work_tasks (
            project TEXT, task_number INTEGER, title TEXT, type TEXT,
            labels TEXT, work_status TEXT, flow_template_id TEXT,
            current_node_id TEXT, assignee TEXT, priority TEXT,
            roles TEXT, created_at TEXT, created_by TEXT, updated_at TEXT
        );
        CREATE TABLE work_task_dependencies (
            from_project TEXT, from_task_number INTEGER,
            to_project TEXT, to_task_number INTEGER,
            kind TEXT, created_at TEXT
        );
        CREATE TABLE work_context_entries (
            task_project TEXT, task_number INTEGER, entry_type TEXT,
            actor TEXT, text TEXT, created_at TEXT
        );
        CREATE TABLE work_node_executions (
            task_project TEXT, task_number INTEGER, node_id TEXT,
            output_json TEXT
        );
        INSERT INTO work_tasks (project, task_number, work_status, labels, roles)
            VALUES ('demo', 1, 'done', '[]', '{}');
        INSERT INTO work_tasks (project, task_number, work_status, labels, roles)
            VALUES ('demo', 2, 'blocked', '[]', '{}');
        -- Task 2 depends on task 1 (1 must finish first).
        INSERT INTO work_task_dependencies VALUES
            ('demo', 1, 'demo', 2, 'blocks', '2026-04-26T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    # Build a minimal project shim that check_project_tasks accepts.
    class _FakeProject:
        key = "demo"
        path = proj_dir

    findings = check_project_tasks(
        config_path=tmp_path / "pollypm.toml",
        project=_FakeProject(),
        project_key="demo",
    )
    blocked_warns = [f for f in findings if f.code == "blocked_without_context"]
    assert blocked_warns == [], (
        f"unexpected blocked_without_context warnings: {blocked_warns!r}"
    )


def test_blocked_with_no_dependency_or_context_still_warns(tmp_path) -> None:
    """The other direction: a blocked task with neither incoming deps
    nor a context entry is genuinely orphaned and must still warn.
    """
    import sqlite3
    from scripts.release_invariants import check_project_tasks

    proj_dir = tmp_path / "demo"
    (proj_dir / ".pollypm").mkdir(parents=True)
    db = proj_dir / ".pollypm" / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE work_tasks (
            project TEXT, task_number INTEGER, title TEXT, type TEXT,
            labels TEXT, work_status TEXT, flow_template_id TEXT,
            current_node_id TEXT, assignee TEXT, priority TEXT,
            roles TEXT, created_at TEXT, created_by TEXT, updated_at TEXT
        );
        CREATE TABLE work_task_dependencies (
            from_project TEXT, from_task_number INTEGER,
            to_project TEXT, to_task_number INTEGER,
            kind TEXT, created_at TEXT
        );
        CREATE TABLE work_context_entries (
            task_project TEXT, task_number INTEGER, entry_type TEXT,
            actor TEXT, text TEXT, created_at TEXT
        );
        CREATE TABLE work_node_executions (
            task_project TEXT, task_number INTEGER, node_id TEXT,
            output_json TEXT
        );
        INSERT INTO work_tasks (project, task_number, work_status, labels, roles)
            VALUES ('demo', 1, 'blocked', '[]', '{}');
        """
    )
    conn.commit()
    conn.close()

    class _FakeProject:
        key = "demo"
        path = proj_dir

    findings = check_project_tasks(
        config_path=tmp_path / "pollypm.toml",
        project=_FakeProject(),
        project_key="demo",
    )
    blocked_warns = [f for f in findings if f.code == "blocked_without_context"]
    assert len(blocked_warns) == 1
    assert "demo/1" in blocked_warns[0].detail
