"""``pm inbox`` shows user-assigned task rows created by ``pm notify``.

Actionable inbox items are tasks assigned to the user. Store messages remain
audit/activity infrastructure and may link back to those tasks, but they are
not the source of truth for user action.

Post-#1013 the ``notify``-labelled chat-flow tasks that ``pm notify``
materialises are hidden from ``pm inbox`` by default — they're stubs that
the cockpit inbox pane handles via its own structured-action affordances,
and the plain CLI listing buried genuinely actionable rows under them.
Pass ``--include-inbox`` to restore the pre-#1013 behaviour for
debugging.

Run with ``HOME=/tmp/pytest-storage-e uv run pytest -x
tests/test_inbox_messages_reader.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pollypm.cli import app as root_app
from pollypm.work.inbox_cli import inbox_app


runner = CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "state.db")


def _invoke_notify(db_path: str, *args: str):
    return runner.invoke(
        root_app,
        ["notify", *args, "--db", db_path],
    )


class TestNotifyVisibleInInbox:
    def test_immediate_notify_surfaces_in_inbox_json(self, db_path):
        result = _invoke_notify(
            db_path, "Deploy blocked", "Needs verification email click.",
        )
        assert result.exit_code == 0, result.output

        # Default ``pm inbox`` hides notify-backed stub tasks (#1013).
        # Resurface them with ``--include-inbox``.
        inbox = runner.invoke(
            inbox_app, ["--db", db_path, "--json", "--include-inbox"],
        )
        assert inbox.exit_code == 0, inbox.output
        payload = json.loads(inbox.output)
        assert payload["assigned_count"] >= 1
        assert payload["messages"] == []
        tasks = payload["tasks"]
        assert len(tasks) == 1
        task = tasks[0]
        assert task["task_id"] == "inbox/1"
        assert "Deploy blocked" in task["title"]
        assert "notify" in task["labels"]

    def test_immediate_notify_shows_in_text_inbox(self, db_path):
        result = _invoke_notify(
            db_path, "Ready to review", "Check the latest build.",
        )
        assert result.exit_code == 0, result.output
        # ``--include-inbox`` resurfaces the stub for debugging.
        inbox = runner.invoke(
            inbox_app, ["--db", db_path, "--include-inbox"],
        )
        assert inbox.exit_code == 0, inbox.output
        assert "Inbox:" in inbox.output
        assert "Ready to review" in inbox.output

    def test_digest_notify_does_not_show_until_flushed(self, db_path):
        # Digest-priority notifies land with state='staged' and must not
        # surface in the inbox until the milestone-flush sweep runs.
        result = _invoke_notify(
            db_path,
            "status update",
            "task shipped",
            "--priority", "digest",
        )
        assert result.exit_code == 0, result.output
        inbox = runner.invoke(inbox_app, ["--db", db_path, "--json"])
        assert inbox.exit_code == 0, inbox.output
        payload = json.loads(inbox.output)
        assert payload["messages"] == []

    def test_silent_notify_does_not_show(self, db_path):
        # Silent tier is closed on write — never visible in the inbox.
        result = _invoke_notify(
            db_path,
            "audit entry",
            "background housekeeping",
            "--priority", "silent",
        )
        assert result.exit_code == 0, result.output
        inbox = runner.invoke(inbox_app, ["--db", db_path, "--json"])
        assert inbox.exit_code == 0, inbox.output
        payload = json.loads(inbox.output)
        assert payload["messages"] == []

    def test_project_filter_narrows_messages(self, db_path):
        runner.invoke(
            root_app,
            [
                "notify", "Subject A", "Body A",
                "--db", db_path, "--project", "alpha",
            ],
        )
        runner.invoke(
            root_app,
            [
                "notify", "Subject B", "Body B",
                "--db", db_path, "--project", "beta",
            ],
        )
        # ``--include-inbox`` is required to see the notify-backed
        # stub tasks (#1013); this test verifies the project filter
        # still narrows correctly when they're surfaced.
        inbox = runner.invoke(
            inbox_app,
            [
                "--db", db_path, "--project", "alpha",
                "--json", "--include-inbox",
            ],
        )
        payload = json.loads(inbox.output)
        assert payload["messages"] == []
        assert len(payload["tasks"]) == 1
        assert "Subject A" in payload["tasks"][0]["title"]


class TestNotifyHiddenWithoutOptIn:
    """#1013 — ``pm notify`` task rows are stubs hidden by default.

    The architect's stage-7 plan_review handoff lands as a chat-flow
    task carrying the ``notify`` label so the cockpit inbox pane can
    render it with structured action affordances. Those rows have no
    node-level transition affordance the CLI listing can act on, so
    listing them by default just buries genuinely actionable items
    (the original sin behind issue #1013, which had 12 of these stubs
    in a 59-item inbox).
    """

    def test_default_inbox_hides_notify_stub_tasks(self, db_path):
        """``pm inbox`` (no flags) hides ``pm notify``-derived task rows."""
        result = _invoke_notify(
            db_path, "Plan ready for review: bikepath",
            "The architect produced a plan; review and approve.",
            "--label", "plan_review",
            "--label", "plan_task:bikepath/1",
        )
        assert result.exit_code == 0, result.output

        inbox = runner.invoke(inbox_app, ["--db", db_path, "--json"])
        assert inbox.exit_code == 0, inbox.output
        payload = json.loads(inbox.output)
        # Stub task is hidden by default.
        assert payload["tasks"] == []

    def test_default_inbox_text_hides_notify_stub_tasks(self, db_path):
        """Text rendering also hides notify-backed stubs by default."""
        result = _invoke_notify(
            db_path, "Plan ready for review: smoketest",
            "The architect produced a plan; review and approve.",
            "--label", "plan_review",
        )
        assert result.exit_code == 0, result.output

        inbox = runner.invoke(inbox_app, ["--db", db_path])
        assert inbox.exit_code == 0, inbox.output
        # The stub title must NOT appear in the default text listing.
        assert "Plan ready for review" not in inbox.output

    def test_include_inbox_resurfaces_notify_stubs(self, db_path):
        """``--include-inbox`` opt-in restores the pre-#1013 behaviour."""
        result = _invoke_notify(
            db_path, "Plan ready for review: bikepath",
            "The architect produced a plan; review and approve.",
            "--label", "plan_review",
        )
        assert result.exit_code == 0, result.output

        inbox = runner.invoke(
            inbox_app, ["--db", db_path, "--json", "--include-inbox"],
        )
        assert inbox.exit_code == 0, inbox.output
        payload = json.loads(inbox.output)
        assert len(payload["tasks"]) == 1
        assert "Plan ready for review" in payload["tasks"][0]["title"]
        assert "notify" in payload["tasks"][0]["labels"]

    def test_real_work_tasks_remain_visible_with_filter(self, db_path):
        """A genuine user-facing chat task is NOT filtered by the
        notify-stub predicate — the filter is keyed on the ``notify``
        label specifically. A real chat-flow conversation (no ``notify``
        label) stays visible in ``pm inbox`` by default."""
        from pollypm.work.sqlite_service import SQLiteWorkService
        from pathlib import Path as _P

        svc = SQLiteWorkService(db_path=_P(db_path))
        try:
            svc.create(
                title="Real chat task",
                description="user-facing question",
                type="task",
                project="proj",
                flow_template="chat",
                roles={"requester": "user", "operator": "polly"},
                priority="normal",
                created_by="polly",
                # No ``notify`` label — this is a real conversation,
                # not an architect handoff stub.
            )
        finally:
            svc.close()

        # Default listing — no ``--include-inbox`` — shows the real task.
        inbox = runner.invoke(inbox_app, ["--db", db_path, "--json"])
        assert inbox.exit_code == 0, inbox.output
        payload = json.loads(inbox.output)
        titles = [t["title"] for t in payload["tasks"]]
        assert "Real chat task" in titles
