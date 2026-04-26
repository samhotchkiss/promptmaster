"""Plural agreement on SQLAlchemyStore validation error messages.

Cycle 122: ``upsert_message`` and ``query_messages`` raised
``ValueError`` with a literal ``(s)`` parenthetical. Match the noun
to the count.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pollypm.store import SQLAlchemyStore


def _store(tmp_path: Path) -> SQLAlchemyStore:
    return SQLAlchemyStore(f"sqlite:///{tmp_path / 'store.db'}")


def test_upsert_message_singular_field_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError) as excinfo:
            store.upsert_message(
                scope="demo",
                type="notify",
                tier="immediate",
                recipient="user",
                sender="pollypm",
                subject="x",
                body="",
                dedupe_key=("body",),
            )
        msg = str(excinfo.value)
        assert "unsupported dedupe_key field" in msg
        assert "field(s)" not in msg
    finally:
        store.close()


def test_upsert_message_plural_fields_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError) as excinfo:
            store.upsert_message(
                scope="demo",
                type="notify",
                tier="immediate",
                recipient="user",
                sender="pollypm",
                subject="x",
                body="",
                dedupe_key=("body", "subject"),
            )
        msg = str(excinfo.value)
        assert "unsupported dedupe_key fields" in msg
    finally:
        store.close()


def test_query_messages_singular_filter_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError) as excinfo:
            store.query_messages(bogus="oops")
        msg = str(excinfo.value)
        assert "unsupported filter" in msg
        assert "filter(s)" not in msg
    finally:
        store.close()


def test_query_messages_plural_filters_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError) as excinfo:
            store.query_messages(bogus="oops", another="bad")
        msg = str(excinfo.value)
        assert "unsupported filters" in msg
    finally:
        store.close()
