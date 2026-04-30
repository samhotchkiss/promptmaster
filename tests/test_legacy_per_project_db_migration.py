"""Migration of legacy ``<project>/.pollypm/state.db`` files (#1004).

The resolver collapse to workspace-root requires a one-shot helper that
imports any rows from leftover per-project state.db files into the
workspace DB and archives the source so the next install does not get
re-bitten by the resolver short-circuit.

These tests cover:

1. Idempotency — re-running on an already-migrated workspace is a no-op.
2. Row-level dedup — rows already present in the workspace DB are
   skipped (matched by ``(project, task_number)``).
3. Archive on success — the per-project file is renamed to
   ``state.db.legacy-1004`` only when every row was either copied or
   matched.
4. End-to-end through the resolver: post-migration, ``resolve_work_db_path``
   plus ``pm task list`` see the merged data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pollypm.work.legacy_per_project_db import (
    LEGACY_DB_SUFFIX,
    PerProjectMigrationReport,
    migrate_legacy_per_project_dbs,
    migrate_one,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_work_tasks_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE work_tasks (
            project TEXT NOT NULL,
            task_number INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT 'task',
            work_status TEXT NOT NULL DEFAULT 'draft',
            flow_template_id TEXT NOT NULL DEFAULT 'standard',
            PRIMARY KEY (project, task_number)
        )
        """
    )


def _create_work_transitions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE work_transitions (
            project TEXT NOT NULL,
            task_number INTEGER NOT NULL,
            ts TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL
        )
        """
    )


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    workspace_db = workspace_root / ".pollypm" / "state.db"
    workspace_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(workspace_db)
    _create_work_tasks_table(conn)
    _create_work_transitions_table(conn)
    conn.commit()
    conn.close()

    project_path = workspace_root / "demo"
    project_path.mkdir()
    (project_path / ".pollypm").mkdir()

    return {
        "workspace_root": workspace_root,
        "workspace_db": workspace_db,
        "project_path": project_path,
        "per_project_db": project_path / ".pollypm" / "state.db",
    }


def _seed_per_project_db(
    db_path: Path,
    *,
    rows: list[tuple[str, int, str]] | None = None,
    transitions: list[tuple[str, int, str, str | None, str]] | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    _create_work_tasks_table(conn)
    _create_work_transitions_table(conn)
    for project, task_number, title in rows or []:
        conn.execute(
            "INSERT INTO work_tasks (project, task_number, title) "
            "VALUES (?, ?, ?)",
            (project, task_number, title),
        )
    for row in transitions or []:
        conn.execute(
            "INSERT INTO work_transitions "
            "(project, task_number, ts, from_status, to_status) "
            "VALUES (?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Single-project migration
# ---------------------------------------------------------------------------


def test_migrate_one_copies_missing_rows_and_archives(workspace):
    _seed_per_project_db(
        workspace["per_project_db"],
        rows=[("demo", 1, "From per-project DB")],
        transitions=[("demo", 1, "2026-04-30T00:00:00", None, "draft")],
    )

    report = migrate_one(
        project_key="demo",
        project_path=workspace["project_path"],
        workspace_db=workspace["workspace_db"],
    )

    assert report.errors == []
    assert report.rows_copied["work_tasks"] == 1
    assert report.rows_copied["work_transitions"] == 1
    assert report.rows_skipped["work_tasks"] == 0

    # Workspace DB now carries the row.
    conn = sqlite3.connect(workspace["workspace_db"])
    titles = {
        row[0]
        for row in conn.execute(
            "SELECT title FROM work_tasks WHERE project = 'demo'"
        )
    }
    transitions = list(
        conn.execute(
            "SELECT to_status FROM work_transitions WHERE project = 'demo'"
        )
    )
    conn.close()
    assert titles == {"From per-project DB"}
    assert len(transitions) == 1

    # Source file archived.
    assert not workspace["per_project_db"].exists()
    assert report.archived_to is not None
    assert report.archived_to.exists()
    assert report.archived_to.suffix == LEGACY_DB_SUFFIX


def test_migrate_one_skips_rows_already_present_in_workspace(workspace):
    # Workspace DB already carries the same task — migration must NOT
    # overwrite it. The dedup matches on (project, task_number).
    conn = sqlite3.connect(workspace["workspace_db"])
    conn.execute(
        "INSERT INTO work_tasks (project, task_number, title) "
        "VALUES (?, ?, ?)",
        ("demo", 1, "Live workspace title — keep"),
    )
    conn.commit()
    conn.close()

    _seed_per_project_db(
        workspace["per_project_db"],
        rows=[("demo", 1, "Stale per-project title — drop")],
    )

    report = migrate_one(
        project_key="demo",
        project_path=workspace["project_path"],
        workspace_db=workspace["workspace_db"],
    )

    assert report.rows_skipped["work_tasks"] == 1
    assert report.rows_copied["work_tasks"] == 0

    conn = sqlite3.connect(workspace["workspace_db"])
    titles = {
        row[0]
        for row in conn.execute(
            "SELECT title FROM work_tasks WHERE project = 'demo'"
        )
    }
    conn.close()
    # Live row preserved, no clobber.
    assert titles == {"Live workspace title — keep"}


def test_migrate_one_idempotent_on_rerun(workspace):
    _seed_per_project_db(
        workspace["per_project_db"],
        rows=[("demo", 1, "Round 1")],
    )

    first = migrate_one(
        project_key="demo",
        project_path=workspace["project_path"],
        workspace_db=workspace["workspace_db"],
    )
    assert first.errors == []
    assert first.rows_copied["work_tasks"] == 1

    # Re-running with no per-project DB present is a clean skip.
    second = migrate_one(
        project_key="demo",
        project_path=workspace["project_path"],
        workspace_db=workspace["workspace_db"],
    )
    assert second.skipped_reason == "no_per_project_db"
    assert second.rows_copied == {}


def test_migrate_one_skips_when_project_path_is_workspace_root(tmp_path):
    # A project registered at workspace_root itself → its
    # ``.pollypm/state.db`` IS the workspace DB. Migrating must not
    # touch the file or claim copies; it's a no-op skip.
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    workspace_db = workspace_root / ".pollypm" / "state.db"
    workspace_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(workspace_db)
    _create_work_tasks_table(conn)
    conn.commit()
    conn.close()

    report = migrate_one(
        project_key="self",
        project_path=workspace_root,
        workspace_db=workspace_db,
    )

    assert report.skipped_reason == "is_workspace_db"
    assert report.archived_to is None
    assert workspace_db.exists()


def test_migrate_one_only_copies_children_of_copied_tasks(workspace):
    # Workspace DB already has demo/1 — we MUST NOT pull a stale
    # demo/1 transition from the per-project DB (would corrupt the
    # live task's history).
    conn = sqlite3.connect(workspace["workspace_db"])
    conn.execute(
        "INSERT INTO work_tasks (project, task_number, title) "
        "VALUES (?, ?, ?)",
        ("demo", 1, "Live"),
    )
    conn.commit()
    conn.close()

    _seed_per_project_db(
        workspace["per_project_db"],
        rows=[("demo", 1, "Stale"), ("demo", 2, "New")],
        transitions=[
            ("demo", 1, "2026-04-29T12:00:00", None, "draft"),
            ("demo", 2, "2026-04-30T12:00:00", None, "draft"),
        ],
    )

    report = migrate_one(
        project_key="demo",
        project_path=workspace["project_path"],
        workspace_db=workspace["workspace_db"],
    )

    assert report.rows_copied["work_tasks"] == 1  # only demo/2 copied
    assert report.rows_copied["work_transitions"] == 1  # only demo/2's

    conn = sqlite3.connect(workspace["workspace_db"])
    transitions = list(
        conn.execute(
            "SELECT project, task_number FROM work_transitions"
        )
    )
    conn.close()
    # demo/1 transitions did NOT move (parent task wasn't copied).
    assert ("demo", 1) not in transitions
    assert ("demo", 2) in transitions


# ---------------------------------------------------------------------------
# Whole-config migration via migrate_legacy_per_project_dbs
# ---------------------------------------------------------------------------


def test_migrate_legacy_per_project_dbs_walks_known_projects(
    tmp_path, monkeypatch
):
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    workspace_db = workspace_root / ".pollypm" / "state.db"
    workspace_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(workspace_db)
    _create_work_tasks_table(conn)
    _create_work_transitions_table(conn)
    conn.commit()
    conn.close()

    # Two projects, both with leftover per-project DBs carrying rows.
    proj_a = workspace_root / "proj_a"
    proj_a.mkdir()
    (proj_a / ".pollypm").mkdir()
    _seed_per_project_db(
        proj_a / ".pollypm" / "state.db",
        rows=[("proj_a", 1, "A1")],
    )

    proj_b = workspace_root / "proj_b"
    proj_b.mkdir()
    (proj_b / ".pollypm").mkdir()
    _seed_per_project_db(
        proj_b / ".pollypm" / "state.db",
        rows=[("proj_b", 1, "B1"), ("proj_b", 2, "B2")],
    )

    class _Project:
        def __init__(self, path: Path) -> None:
            self.path = path

    class _ProjectRoot:
        pass

    _root = _ProjectRoot()
    _root.workspace_root = workspace_root

    class _Config:
        pass

    cfg = _Config()
    cfg.project = _root
    cfg.projects = {
        "proj_a": _Project(proj_a),
        "proj_b": _Project(proj_b),
    }

    reports = migrate_legacy_per_project_dbs(config=cfg)

    by_key: dict[str, PerProjectMigrationReport] = {
        r.project_key: r for r in reports
    }
    assert set(by_key) == {"proj_a", "proj_b"}
    assert by_key["proj_a"].rows_copied["work_tasks"] == 1
    assert by_key["proj_b"].rows_copied["work_tasks"] == 2

    conn = sqlite3.connect(workspace_db)
    counts = dict(
        conn.execute(
            "SELECT project, count(*) FROM work_tasks GROUP BY project"
        )
    )
    conn.close()
    assert counts == {"proj_a": 1, "proj_b": 2}


# ---------------------------------------------------------------------------
# End-to-end: post-migration the resolver+CLI see the merged data
# ---------------------------------------------------------------------------


def test_post_migration_resolver_returns_workspace_with_merged_rows(
    workspace, monkeypatch
):
    """End-to-end: pre-migration the resolver would have routed reads
    to the per-project DB; post-migration the per-project DB is gone
    and the workspace DB carries the data. The resolver returns the
    workspace DB throughout, so reads stay consistent."""
    from pollypm.work.db_resolver import resolve_work_db_path

    _seed_per_project_db(
        workspace["per_project_db"],
        rows=[("demo", 7, "Plan ready for review")],
    )

    class _Project:
        def __init__(self, path: Path) -> None:
            self.path = path

    class _ProjectRoot:
        pass

    _root = _ProjectRoot()
    _root.workspace_root = workspace["workspace_root"]

    class _Config:
        pass

    cfg = _Config()
    cfg.project = _root
    cfg.projects = {"demo": _Project(workspace["project_path"])}

    # Resolver returns workspace DB even with per-project husk present.
    resolved_pre = resolve_work_db_path(project="demo", config=cfg)
    assert resolved_pre == workspace["workspace_db"]

    # Run migration; per-project rows land in workspace DB.
    reports = migrate_legacy_per_project_dbs(config=cfg)
    assert any(r.rows_copied.get("work_tasks", 0) for r in reports)

    # Resolver still returns workspace DB; per-project file is archived.
    resolved_post = resolve_work_db_path(project="demo", config=cfg)
    assert resolved_post == workspace["workspace_db"]
    assert not workspace["per_project_db"].exists()

    conn = sqlite3.connect(workspace["workspace_db"])
    titles = {
        row[0]
        for row in conn.execute(
            "SELECT title FROM work_tasks WHERE project = 'demo'"
        )
    }
    conn.close()
    assert "Plan ready for review" in titles
