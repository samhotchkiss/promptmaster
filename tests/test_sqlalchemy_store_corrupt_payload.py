"""Defend ``SQLAlchemyStore.query_messages`` against corrupt JSON shapes.

Cycle 107: ``query_messages`` decoded ``payload_json`` and ``labels``
JSON columns but didn't validate the parsed shape. Producers always
write dict payloads / list labels, but a hand-edited or legacy DB
could surface e.g. a JSON-encoded list in ``payload_json``. Downstream
code does ``payload.get(...)`` which would AttributeError on a list,
crashing the cockpit inbox load. Coerce parsed-but-wrong-type values
back to their expected empty form so a corrupt row degrades
gracefully.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from pollypm.store import SQLAlchemyStore


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'store.db'}"


def _insert_raw(store: SQLAlchemyStore, **fields) -> int:
    """Insert a messages row with raw JSON in payload_json/labels.

    Bypasses ``upsert_message`` so we can simulate a corrupt persisted
    shape (list/null/string instead of dict/list).
    """
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    with store.write_engine.begin() as conn:
        result = conn.execute(
            text(f"INSERT INTO messages ({columns}) VALUES ({placeholders})"),
            fields,
        )
        return int(result.lastrowid)


def test_query_messages_coerces_non_dict_payload_to_empty_dict(tmp_path: Path) -> None:
    store = SQLAlchemyStore(_db_url(tmp_path))
    try:
        _insert_raw(
            store,
            scope="demo",
            type="notify",
            tier="immediate",
            recipient="user",
            sender="pollypm",
            state="open",
            subject="hello",
            body="",
            payload_json='[1, 2, 3]',  # not a dict — corruption shape
            labels="[]",
            created_at="2026-04-25T20:00:00+00:00",
            updated_at="2026-04-25T20:00:00+00:00",
        )
        rows = store.query_messages(recipient="user")
        assert rows, "expected the row to be returned"
        payload = rows[0]["payload"]
        # The fix coerces non-dict shapes to {} so downstream
        # ``payload.get(...)`` calls don't AttributeError.
        assert payload == {}
        assert isinstance(payload, dict)
    finally:
        store.close()


def test_query_messages_coerces_non_list_labels_to_empty_list(tmp_path: Path) -> None:
    store = SQLAlchemyStore(_db_url(tmp_path))
    try:
        _insert_raw(
            store,
            scope="demo",
            type="notify",
            tier="immediate",
            recipient="user",
            sender="pollypm",
            state="open",
            subject="hello",
            body="",
            payload_json="{}",
            labels='"oops-a-string"',  # not a list — corruption shape
            created_at="2026-04-25T20:00:00+00:00",
            updated_at="2026-04-25T20:00:00+00:00",
        )
        rows = store.query_messages(recipient="user")
        assert rows
        labels = rows[0]["labels"]
        # Without the fix, callers iterating ``labels`` would iterate
        # the string's characters — a subtle data-corruption bug.
        assert labels == []
        assert isinstance(labels, list)
    finally:
        store.close()


def test_query_messages_passes_through_well_formed_shapes(tmp_path: Path) -> None:
    """The fix must not regress the normal path."""
    store = SQLAlchemyStore(_db_url(tmp_path))
    try:
        _insert_raw(
            store,
            scope="demo",
            type="notify",
            tier="immediate",
            recipient="user",
            sender="pollypm",
            state="open",
            subject="hello",
            body="",
            payload_json='{"project": "demo", "ok": true}',
            labels='["plan_review", "urgent"]',
            created_at="2026-04-25T20:00:00+00:00",
            updated_at="2026-04-25T20:00:00+00:00",
        )
        rows = store.query_messages(recipient="user")
        assert rows[0]["payload"] == {"project": "demo", "ok": True}
        assert rows[0]["labels"] == ["plan_review", "urgent"]
    finally:
        store.close()
