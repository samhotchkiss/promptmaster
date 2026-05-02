"""Unit tests for the :class:`Store` writer surface added in issue #340.

Covers:

* :func:`apply_title_contract` — every tier/type branch + existing-tag
  passthrough.
* :meth:`SQLAlchemyStore.upsert_message` — insert-then-update dedupe,
  custom ``dedupe_key`` contract, unknown-key rejection.
* :meth:`SQLAlchemyStore.upsert_alert` / :meth:`clear_alert` — the alert
  wrappers over ``upsert_message`` / ``close_message``.
* :meth:`SQLAlchemyStore.execute` — raw-statement escape hatch.

Run with ``HOME=/tmp/pytest-storage-d uv run pytest -x
tests/test_store_writers.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert, select

from pollypm.store import SQLAlchemyStore, apply_title_contract
from pollypm.store.schema import messages
from pollypm.store.title_contract import has_bracket_tag


# ---------------------------------------------------------------------------
# Title contract
# ---------------------------------------------------------------------------


class TestApplyTitleContract:
    def test_immediate_notify_gets_action_tag(self):
        out = apply_title_contract("Deploy blocked", tier="immediate", type="notify")
        assert out == "[Action] Deploy blocked"

    def test_digest_notify_gets_fyi_tag(self):
        out = apply_title_contract("task shipped", tier="digest", type="notify")
        assert out == "[FYI] task shipped"

    def test_silent_tier_gets_audit_tag(self):
        # silent wins regardless of type.
        out = apply_title_contract("recorded trace", tier="silent", type="notify")
        assert out == "[Audit] recorded trace"
        # Also for alert with silent tier — still [Audit].
        out = apply_title_contract("x", tier="silent", type="alert")
        assert out == "[Audit] x"

    def test_alert_type_gets_alert_tag(self):
        out = apply_title_contract("pane dead", tier="immediate", type="alert")
        assert out == "[Alert] pane dead"

    def test_inbox_task_type_gets_task_tag(self):
        out = apply_title_contract("Assigned task 12", type="inbox_task")
        assert out == "[Task] Assigned task 12"

    def test_existing_bracket_tag_left_alone(self):
        # Caller knew what they were doing — no double prefix.
        out = apply_title_contract(
            "[Done] milestone 02", tier="digest", type="notify"
        )
        assert out == "[Done] milestone 02"

    def test_has_bracket_tag_rejects_plain(self):
        assert not has_bracket_tag("plain subject")
        assert has_bracket_tag("[Ready] x")
        # Leading whitespace before the bracket still counts.
        assert has_bracket_tag("  [FYI] x")

    def test_empty_subject_returns_tag_alone(self):
        # Defensive — empty input should still get stamped (caller's
        # validation ought to have caught this, but not our problem).
        out = apply_title_contract("", tier="immediate", type="notify")
        assert out == "[Action]"

    def test_unknown_tier_type_falls_back_to_note(self):
        out = apply_title_contract("mystery", tier="weird", type="weird")
        assert out == "[Note] mystery"


# ---------------------------------------------------------------------------
# upsert_message dedupe contract
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SQLAlchemyStore:
    store = SQLAlchemyStore(f"sqlite:///{tmp_path}/state.db")
    yield store
    store.close()


class TestUpsertMessage:
    def test_first_upsert_inserts_new_row(self, store: SQLAlchemyStore):
        row_id = store.upsert_message(
            type="alert",
            tier="immediate",
            recipient="user",
            sender="pane_dead",
            subject="Pane is dead",
            body="",
            scope="worker-foo",
        )
        assert row_id > 0

        rows = store.query_messages(type="alert", scope="worker-foo")
        assert len(rows) == 1
        assert rows[0]["subject"].startswith("[Alert]")
        assert rows[0]["state"] == "open"

    def test_second_upsert_refreshes_existing_open_row(
        self, store: SQLAlchemyStore
    ):
        first = store.upsert_message(
            type="alert",
            tier="immediate",
            recipient="user",
            sender="pane_dead",
            subject="Pane is dead",
            body="",
            scope="worker-foo",
        )
        second = store.upsert_message(
            type="alert",
            tier="immediate",
            recipient="user",
            sender="pane_dead",
            subject="Pane is dead (retrying)",
            body="Retry #2",
            scope="worker-foo",
        )
        # Same row id — no duplicate inserted.
        assert first == second

        rows = store.query_messages(type="alert", scope="worker-foo")
        assert len(rows) == 1
        assert "retrying" in rows[0]["subject"].lower()
        assert rows[0]["body"] == "Retry #2"

    def test_closed_row_does_not_dedupe(self, store: SQLAlchemyStore):
        # After close, a new upsert should insert a fresh row.
        first = store.upsert_message(
            type="alert",
            tier="immediate",
            recipient="user",
            sender="pane_dead",
            subject="Pane is dead",
            body="",
            scope="worker-foo",
        )
        store.close_message(first)
        second = store.upsert_message(
            type="alert",
            tier="immediate",
            recipient="user",
            sender="pane_dead",
            subject="Pane is dead again",
            body="",
            scope="worker-foo",
        )
        assert first != second

    def test_different_sender_inserts_new_row(self, store: SQLAlchemyStore):
        store.upsert_message(
            type="alert",
            tier="immediate",
            recipient="user",
            sender="pane_dead",
            subject="Pane is dead",
            body="",
            scope="worker-foo",
        )
        store.upsert_message(
            type="alert",
            tier="immediate",
            recipient="user",
            sender="shell_returned",
            subject="Shell returned",
            body="",
            scope="worker-foo",
        )
        rows = store.query_messages(type="alert", scope="worker-foo")
        assert len(rows) == 2

    def test_unknown_dedupe_key_field_rejected(self, store: SQLAlchemyStore):
        with pytest.raises(ValueError, match="dedupe_key"):
            store.upsert_message(
                type="alert",
                tier="immediate",
                recipient="user",
                sender="x",
                subject="x",
                body="",
                scope="s",
                dedupe_key=("scope", "totally_fake_field"),
            )


# ---------------------------------------------------------------------------
# Alert wrappers
# ---------------------------------------------------------------------------


class TestAlertWrappers:
    def test_upsert_alert_then_clear(self, store: SQLAlchemyStore):
        store.upsert_alert(
            session_name="worker-foo",
            alert_type="pane_dead",
            severity="warn",
            message="Pane is dead",
        )
        rows = store.query_messages(type="alert", scope="worker-foo")
        assert len(rows) == 1
        assert rows[0]["state"] == "open"
        assert rows[0]["sender"] == "pane_dead"
        # severity rides along in payload.
        assert rows[0]["payload"]["severity"] == "warn"

        store.clear_alert("worker-foo", "pane_dead")
        rows = store.query_messages(type="alert", scope="worker-foo")
        assert len(rows) == 1
        assert rows[0]["state"] == "closed"

    def test_upsert_alert_is_idempotent(self, store: SQLAlchemyStore):
        for _ in range(3):
            store.upsert_alert(
                session_name="s",
                alert_type="t",
                severity="warn",
                message="m",
            )
        rows = store.query_messages(type="alert", scope="s")
        # Dedupe — only one open row per (session, alert_type).
        assert len(rows) == 1

    def test_clear_alert_when_none_open_is_noop(self, store: SQLAlchemyStore):
        # Must not raise.
        store.clear_alert("nothing", "never_opened")
        rows = store.query_messages(type="alert", scope="nothing")
        assert rows == []
        # No-op clears must NOT spam an alert.cleared event into the
        # activity stream — the event log would fill with phantom clears
        # from every heartbeat sweep otherwise.
        events = store.query_messages(type="event", scope="nothing")
        assert events == []

    def test_clear_alert_emits_cleared_event(self, store: SQLAlchemyStore):
        # #1033: every successful clear writes an ``alert.cleared`` event
        # into the activity stream so the feed shows both ends of the
        # alert lifecycle.
        store.upsert_alert(
            session_name="worker-foo",
            alert_type="pane_dead",
            severity="warn",
            message="Pane is dead",
        )
        store.clear_alert("worker-foo", "pane_dead", who_cleared="auto:test")
        events = store.query_messages(
            type="event", scope="worker-foo",
        )
        cleared = [e for e in events if e["subject"] == "alert.cleared"]
        assert len(cleared) == 1
        payload = cleared[0]["payload"]
        assert payload["event_type"] == "alert.cleared"
        assert payload["alert_type"] == "pane_dead"
        assert payload["who_cleared"] == "auto:test"
        assert payload["severity"] == "warn"
        assert "Cleared pane_dead" in payload["summary"]

    def test_clear_alert_default_who_cleared_is_system(
        self, store: SQLAlchemyStore,
    ):
        # Existing call sites that don't pass ``who_cleared`` get the
        # neutral ``system`` attribution — protocol back-compat for #1033.
        store.upsert_alert(
            session_name="s",
            alert_type="t",
            severity="warn",
            message="m",
        )
        store.clear_alert("s", "t")
        events = store.query_messages(type="event", scope="s")
        cleared = [e for e in events if e["subject"] == "alert.cleared"]
        assert len(cleared) == 1
        assert cleared[0]["payload"]["who_cleared"] == "system"


# ---------------------------------------------------------------------------
# #1044 — alert-emit idempotency contract
# ---------------------------------------------------------------------------


class TestAlertIdempotency1044:
    """The alert-emit path must produce exactly one open row per
    ``(scope, sender)`` even under concurrent emitters. The contract has
    three parts: the upsert path bumps an ``occurrences`` counter on the
    surviving row, the partial unique index rejects manual duplicate
    inserts, and the bootstrap backfill collapses pre-existing dupes
    inherited from older builds.
    """

    def test_emit_twice_keeps_one_row_and_bumps_occurrences(
        self, store: SQLAlchemyStore
    ) -> None:
        store.upsert_alert(
            session_name="plan_gate-pollypm",
            alert_type="plan_missing",
            severity="warn",
            message="Project 'pollypm' has no approved plan yet",
        )
        store.upsert_alert(
            session_name="plan_gate-pollypm",
            alert_type="plan_missing",
            severity="warn",
            message="Project 'pollypm' has no approved plan yet",
        )

        rows = store.query_messages(
            type="alert", scope="plan_gate-pollypm", state="open",
        )
        assert len(rows) == 1, (
            "second upsert_alert must not insert a second open row — "
            f"got {len(rows)} open rows for plan_gate-pollypm/plan_missing"
        )
        assert rows[0]["payload"]["occurrences"] == 2

    def test_partial_unique_index_rejects_manual_duplicate(
        self, store: SQLAlchemyStore
    ) -> None:
        from sqlalchemy.exc import IntegrityError as _IntegrityError

        store.upsert_alert(
            session_name="plan_gate-polly_remote",
            alert_type="plan_missing",
            severity="warn",
            message="seed",
        )
        # A direct INSERT (the exact shape that bypassed dedupe pre-#1044
        # — see the test_activity_feed_panel ``_seed_alert`` helper) must
        # fail loudly. The partial unique index converts the silent
        # double-row bug into a hard error the upsert path can catch.
        with pytest.raises(_IntegrityError):
            with store.transaction() as conn:
                conn.execute(
                    insert(messages),
                    {
                        "scope": "plan_gate-polly_remote",
                        "type": "alert",
                        "tier": "immediate",
                        "recipient": "user",
                        "sender": "plan_missing",
                        "state": "open",
                        "subject": "[Alert] dup",
                        "body": "",
                        "payload_json": "{}",
                        "labels": "[]",
                    },
                )

    def test_partial_index_does_not_constrain_closed_rows(
        self, store: SQLAlchemyStore
    ) -> None:
        # Closed rows are part of the audit trail and are intentionally
        # not deduped — only ``state='open'`` rows compete for the
        # uniqueness slot. Two closed rows for the same alert-key must
        # coexist so historical replays survive.
        for _ in range(3):
            with store.transaction() as conn:
                conn.execute(
                    insert(messages),
                    {
                        "scope": "plan_gate-history",
                        "type": "alert",
                        "tier": "immediate",
                        "recipient": "user",
                        "sender": "plan_missing",
                        "state": "closed",
                        "subject": "[Alert] historical",
                        "body": "",
                        "payload_json": "{}",
                        "labels": "[]",
                    },
                )
        rows = store.query_messages(type="alert", scope="plan_gate-history")
        assert len(rows) == 3

    def test_backfill_collapses_existing_open_dupes_on_bootstrap(
        self, tmp_path: Path,
    ) -> None:
        # Simulate the workspace-in-the-wild state from issue #1044:
        # two open ``plan_gate-pollypm/plan_missing`` rows already exist
        # before the migration runs. We have to construct that state
        # without the partial index, so we open the DB raw, create the
        # ``messages`` table without the index, seed the dupes, then let
        # SQLAlchemyStore bootstrap on the pre-populated DB. Bootstrap
        # must close all but the oldest id and only then install the
        # partial index — otherwise the index DDL itself would fail.
        import sqlite3

        db_path = tmp_path / "preexisting.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    type TEXT NOT NULL,
                    tier TEXT NOT NULL DEFAULT 'immediate',
                    recipient TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'open',
                    parent_id INTEGER,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    labels TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT
                )
                """
            )
            for _ in range(3):
                conn.execute(
                    """
                    INSERT INTO messages (
                        scope, type, tier, recipient, sender, state,
                        subject, body, payload_json, labels
                    )
                    VALUES (?, 'alert', 'immediate', 'user', ?, 'open', ?, '', '{}', '[]')
                    """,
                    (
                        "plan_gate-pollypm",
                        "plan_missing",
                        "[Alert] Project 'pollypm' has no approved plan yet",
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        # Open through SQLAlchemyStore — this triggers the bootstrap
        # backfill + index install.
        store = SQLAlchemyStore(f"sqlite:///{db_path}")
        try:
            open_rows = store.query_messages(
                type="alert", scope="plan_gate-pollypm", state="open",
            )
            assert len(open_rows) == 1, (
                "backfill must collapse pre-existing duplicate open alerts "
                f"down to one — saw {len(open_rows)}"
            )
            # The kept row is the oldest id.
            kept = open_rows[0]
            with store.read_engine.connect() as conn:
                rows = conn.execute(
                    select(messages.c.id, messages.c.state)
                    .where(messages.c.scope == "plan_gate-pollypm")
                    .order_by(messages.c.id.asc())
                ).all()
            assert kept["id"] == rows[0][0]
            # Two of the three are now closed.
            closed_count = sum(1 for _, state in rows if state == "closed")
            assert closed_count == 2
        finally:
            store.close()


# ---------------------------------------------------------------------------
# execute() escape hatch
# ---------------------------------------------------------------------------


class TestExecute:
    def test_execute_runs_core_statement_and_returns_cursor(
        self, store: SQLAlchemyStore
    ):
        result = store.execute(
            insert(messages).values(
                scope="x",
                type="event",
                tier="immediate",
                recipient="*",
                sender="unit-test",
                state="open",
                subject="hello",
                body="",
                payload_json="{}",
                labels="[]",
            )
        )
        assert result.inserted_primary_key is not None

        # Read it back through the Store's read engine using a Core select.
        with store.read_engine.connect() as conn:
            rows = conn.execute(
                select(messages.c.subject).where(messages.c.sender == "unit-test")
            ).all()
        assert rows[0][0] == "hello"


# ---------------------------------------------------------------------------
# Title contract integration via enqueue_message
# ---------------------------------------------------------------------------


class TestEnqueueStampsSubject:
    def test_enqueue_applies_title_contract_automatically(
        self, store: SQLAlchemyStore
    ):
        row_id = store.enqueue_message(
            type="notify",
            tier="immediate",
            recipient="user",
            sender="polly",
            subject="plain subject",
            body="body",
            scope="demo",
        )
        rows = store.query_messages(type="notify", scope="demo")
        assert rows[0]["subject"] == "[Action] plain subject"
        assert rows[0]["id"] == row_id

    def test_enqueue_respects_pre_tagged_subject(self, store: SQLAlchemyStore):
        store.enqueue_message(
            type="notify",
            tier="immediate",
            recipient="user",
            sender="polly",
            subject="[Hot] custom tag",
            body="",
            scope="demo",
        )
        rows = store.query_messages(type="notify", scope="demo")
        assert rows[0]["subject"] == "[Hot] custom tag"
