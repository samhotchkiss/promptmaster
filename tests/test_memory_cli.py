"""Tests for ``pm memory`` CLI (M07 / #236)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pollypm.memory_backends import (
    FeedbackMemory,
    FileMemoryBackend,
    PatternMemory,
    ProjectMemory,
    UserMemory,
)
from pollypm.memory_cli import memory_app, set_backend_factory


runner = CliRunner()


@pytest.fixture
def seeded(tmp_path: Path) -> FileMemoryBackend:
    backend = FileMemoryBackend(tmp_path)
    backend.write_entry(
        UserMemory(
            name="Sam",
            description="Senior engineer.",
            body="Prefers small modules.",
            scope="operator",
            scope_tier="user",
            importance=5,
        )
    )
    backend.write_entry(
        ProjectMemory(
            fact="Test runner is pytest",
            why="Team convention",
            how_to_apply="Run pytest -q",
            scope="pollypm",
            importance=4,
        )
    )
    backend.write_entry(
        FeedbackMemory(
            rule="Never use --no-verify",
            why="Hooks catch regressions",
            how_to_apply="Fix the hook",
            scope="pollypm",
            importance=5,
        )
    )
    return backend


@pytest.fixture(autouse=True)
def _install_factory(seeded: FileMemoryBackend):
    """Wire the CLI's backend factory to the seeded fixture for every test."""
    set_backend_factory(lambda config_path: seeded)
    try:
        yield
    finally:
        set_backend_factory(None)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_plain_output() -> None:
    result = runner.invoke(memory_app, ["list"])
    assert result.exit_code == 0, result.stdout
    assert "Sam" in result.stdout or "pytest" in result.stdout
    assert "--no-verify" in result.stdout


def test_list_json_output() -> None:
    result = runner.invoke(memory_app, ["list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) >= 3
    types = {e["type"] for e in data}
    assert {"user", "project", "feedback"} <= types


def test_list_filters_by_type() -> None:
    result = runner.invoke(memory_app, ["list", "--type", "user", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert all(e["type"] == "user" for e in data)


def test_list_filters_by_scope() -> None:
    result = runner.invoke(memory_app, ["list", "--scope", "operator", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert all(e["scope"] == "operator" for e in data)


def test_list_rejects_out_of_range_importance() -> None:
    result = runner.invoke(memory_app, ["list", "--importance", "9"])
    assert result.exit_code != 0
    assert "1..5" in (result.stdout + (result.stderr or ""))


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_existing_entry(seeded: FileMemoryBackend) -> None:
    entries = seeded.list_entries(limit=50)
    entry_id = entries[0].entry_id
    result = runner.invoke(memory_app, ["show", str(entry_id)])
    assert result.exit_code == 0
    assert f"id         = {entry_id}" in result.stdout


def test_show_missing_entry_exits_nonzero() -> None:
    result = runner.invoke(memory_app, ["show", "99999"])
    assert result.exit_code != 0


def test_show_json_includes_supersession_chain(seeded: FileMemoryBackend) -> None:
    entry_id = seeded.list_entries(limit=50)[0].entry_id
    result = runner.invoke(memory_app, ["show", str(entry_id), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["id"] == entry_id
    assert "supersession_chain" in data
    # A solo entry's chain contains just itself.
    assert data["supersession_chain"][0]["id"] == entry_id


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


def test_edit_changes_importance(seeded: FileMemoryBackend) -> None:
    entry_id = seeded.list_entries(type="project", limit=50)[0].entry_id
    result = runner.invoke(memory_app, ["edit", str(entry_id), "--importance", "2"])
    assert result.exit_code == 0
    refreshed = seeded.read_entry(entry_id)
    assert refreshed is not None
    assert refreshed.importance == 2


def test_edit_changes_body(seeded: FileMemoryBackend) -> None:
    entry_id = seeded.list_entries(limit=50)[0].entry_id
    result = runner.invoke(memory_app, ["edit", str(entry_id), "--body", "updated body text"])
    assert result.exit_code == 0
    refreshed = seeded.read_entry(entry_id)
    assert refreshed is not None
    assert refreshed.body == "updated body text"


def test_edit_replaces_tags(seeded: FileMemoryBackend) -> None:
    entry_id = seeded.list_entries(limit=50)[0].entry_id
    result = runner.invoke(memory_app, ["edit", str(entry_id), "--tags", "one,two,three"])
    assert result.exit_code == 0
    refreshed = seeded.read_entry(entry_id)
    assert refreshed is not None
    assert set(refreshed.tags) == {"one", "two", "three"}


def test_edit_requires_one_field() -> None:
    result = runner.invoke(memory_app, ["edit", "1"])
    assert result.exit_code != 0


def test_edit_missing_entry_errors() -> None:
    result = runner.invoke(memory_app, ["edit", "99999", "--importance", "2"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_with_yes_flag(seeded: FileMemoryBackend) -> None:
    entry_id = seeded.list_entries(limit=50)[0].entry_id
    result = runner.invoke(memory_app, ["forget", str(entry_id), "--yes"])
    assert result.exit_code == 0
    assert seeded.read_entry(entry_id) is None


def test_forget_interactive_confirm_yes(seeded: FileMemoryBackend) -> None:
    entry_id = seeded.list_entries(limit=50)[0].entry_id
    result = runner.invoke(memory_app, ["forget", str(entry_id)], input="y\n")
    assert result.exit_code == 0
    assert seeded.read_entry(entry_id) is None


def test_forget_interactive_confirm_no(seeded: FileMemoryBackend) -> None:
    entry_id = seeded.list_entries(limit=50)[0].entry_id
    result = runner.invoke(memory_app, ["forget", str(entry_id)], input="n\n")
    assert result.exit_code != 0
    # Entry still present.
    assert seeded.read_entry(entry_id) is not None


def test_forget_missing_entry_errors() -> None:
    result = runner.invoke(memory_app, ["forget", "99999", "--yes"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


def test_recall_matches_api_ordering(seeded: FileMemoryBackend) -> None:
    # Programmatic recall — the CLI must match this exactly.
    api_results = seeded.recall("pytest")
    result = runner.invoke(memory_app, ["recall", "pytest", "--json"])
    assert result.exit_code == 0
    cli_results = json.loads(result.stdout)
    assert [e["id"] for e in cli_results] == [r.entry.entry_id for r in api_results]


def test_recall_respects_limit() -> None:
    result = runner.invoke(memory_app, ["recall", "a", "--limit", "1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) <= 1


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_plain_output() -> None:
    result = runner.invoke(memory_app, ["stats"])
    assert result.exit_code == 0
    assert "Total entries" in result.stdout
    assert "By type" in result.stdout


def test_stats_json_output() -> None:
    result = runner.invoke(memory_app, ["stats", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "total" in data
    assert "by_type" in data
    assert "by_scope" in data
    assert "by_importance" in data
    assert "by_tier" in data
    assert data["total"] >= 3
