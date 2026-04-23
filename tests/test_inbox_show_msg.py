"""Tests for ``pm inbox show msg:N`` (#760 adopter).

``pm inbox --json`` emits IDs in two forms: ``project/number`` for
work-service task rows and ``msg:N`` for unified-store notify/alert
rows. Before this fix, ``pm inbox show`` rejected the ``msg:N`` form,
so a user who copy-pasted the ID from ``--json`` hit a cryptic error.
These tests lock in the two-way parity: every ID from ``--json`` is a
valid ``show`` argument.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from typer.testing import CliRunner

from pollypm.store import SQLAlchemyStore
from pollypm.work.inbox_cli import inbox_app


runner = CliRunner()


def _seed_message(db_path: Path, **overrides) -> int:
    """Write a single message and return its ID."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLAlchemyStore(f"sqlite:///{db_path}")
    try:
        return store.enqueue_message(
            type=overrides.get("type", "notify"),
            tier=overrides.get("tier", "immediate"),
            recipient=overrides.get("recipient", "user"),
            sender=overrides.get("sender", "polly"),
            subject=overrides.get("subject", "A notify from Polly"),
            body=overrides.get("body", "Plan ready for review.\nSee task demo/1."),
            scope=overrides.get("scope", "demo"),
        )
    finally:
        store.close()


def test_inbox_show_accepts_msg_id_form(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    msg_id = _seed_message(db_path, subject="Plan ready for review")

    result = runner.invoke(inbox_app, ["show", f"msg:{msg_id}", "--db", str(db_path)])
    assert result.exit_code == 0, result.output
    # The key fields a user wants at a glance.
    assert f"msg:{msg_id}" in result.output
    assert "Plan ready for review" in result.output
    assert "sender:" in result.output
    assert "body:" in result.output


def test_inbox_show_msg_json_roundtrip(tmp_path: Path) -> None:
    """JSON output should be parseable and contain the same fields."""
    db_path = tmp_path / "state.db"
    msg_id = _seed_message(db_path, subject="JSON subject")

    result = runner.invoke(
        inbox_app, ["show", f"msg:{msg_id}", "--db", str(db_path), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["id"] == msg_id
    # enqueue_message decorates subjects with a ``[Action]`` / ``[Info]``
    # prefix depending on type+tier; keep the assertion tolerant rather
    # than re-encoding that policy here.
    assert "JSON subject" in payload["subject"]


def test_inbox_show_msg_missing_id_exits_nonzero(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    # Create a DB with at least one row so the store isn't empty.
    _seed_message(db_path)

    result = runner.invoke(
        inbox_app, ["show", "msg:9999999", "--db", str(db_path)],
    )
    assert result.exit_code != 0
    assert "no message" in (result.output + (result.stderr or "")).lower()


def test_inbox_show_msg_invalid_form_exits_nonzero(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    _seed_message(db_path)

    result = runner.invoke(
        inbox_app, ["show", "msg:not-a-number", "--db", str(db_path)],
    )
    assert result.exit_code != 0
    assert "invalid message id" in (result.output + (result.stderr or "")).lower()
