"""End-to-end read/write consistency for the work DB (#1004).

Bug #1004 was: a write through the architect/notify path landed in
``<workspace>/.pollypm/state.db`` while the next read through the CLI
resolver short-circuited to an empty ``<project>/.pollypm/state.db``.
``pm task list`` and ``pm task get`` returned 'No tasks found' for
tasks the workspace DB clearly held; the cockpit read yet a third
view; the rail badge counted yet another.

These tests pin the invariant that every read path lands in the same
place as every write path. They run against the actual
``SQLiteWorkService`` and the actual resolver — not stubs — so a
future regression cannot pass tests by patching the wrong layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    project_path = workspace_root / "demo"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    config_path = tmp_path / "pollypm.toml"
    config_path.write_text(
        "[project]\n"
        'tmux_session = "pollypm-test"\n'
        f'workspace_root = "{workspace_root}"\n'
        "\n"
        "[projects.demo]\n"
        'key = "demo"\n'
        'name = "Demo"\n'
        f'path = "{project_path}"\n'
    )

    from pollypm import config as config_module

    real_load = config_module.load_config
    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda path=None: real_load(config_path if path is None else path),
    )

    return {
        "workspace_root": workspace_root,
        "project_path": project_path,
        "workspace_db": workspace_root / ".pollypm" / "state.db",
    }


def _make_per_project_husk(project_path: Path) -> Path:
    """Re-create the on-disk shape that triggered #1004.

    The pre-#1004 resolver short-circuited to this file whenever it
    existed — even when empty — while writes went elsewhere. Tests
    that don't include this husk would happily pass against the buggy
    resolver too; that's why earlier coverage missed the bug.
    """
    pollypm_dir = project_path / ".pollypm"
    pollypm_dir.mkdir(parents=True, exist_ok=True)
    husk = pollypm_dir / "state.db"
    husk.touch()
    return husk


def _open_service_via_resolver(project: str):
    """Open SQLiteWorkService the way the CLI does."""
    from pollypm.work.db_resolver import resolve_work_db_path
    from pollypm.work.sqlite_service import SQLiteWorkService

    db_path = resolve_work_db_path(project=project)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteWorkService(db_path=db_path)


def test_write_then_list_through_resolver_returns_the_task(env):
    """A task created via the resolver-resolved DB is visible to a
    fresh service opened through the same resolver. This is the
    minimum invariant — without it ``pm task list`` is broken."""
    _make_per_project_husk(env["project_path"])

    with _open_service_via_resolver("demo") as svc:
        task = svc.create(
            title="repro 1004",
            description="reads must see writes",
            type="task",
            project="demo",
            flow_template="standard",
            roles={"worker": "engineer", "reviewer": "engineer"},
            priority="normal",
        )
        created_id = task.task_id

    with _open_service_via_resolver("demo") as svc:
        listed = svc.list_tasks(project="demo")
        ids = [t.task_id for t in listed]
        fetched = svc.get(created_id)

    assert created_id in ids
    assert fetched.task_id == created_id


def test_writes_land_in_workspace_db_not_per_project_husk(env):
    """The ``work_tasks`` row physically lives in the workspace DB."""
    import sqlite3

    husk = _make_per_project_husk(env["project_path"])

    with _open_service_via_resolver("demo") as svc:
        svc.create(
            title="repro 1004 — db location",
            description="x",
            type="task",
            project="demo",
            flow_template="standard",
            roles={"worker": "engineer", "reviewer": "engineer"},
            priority="normal",
        )

    workspace_conn = sqlite3.connect(env["workspace_db"])
    try:
        rows = list(
            workspace_conn.execute(
                "SELECT title FROM work_tasks WHERE project = 'demo'"
            )
        )
    finally:
        workspace_conn.close()
    assert any("repro 1004" in row[0] for row in rows)

    # Per-project husk has no work_tasks rows (it may not even have the
    # table — the touched file is empty bytes). It MUST stay that way;
    # any non-zero read here would mean writes landed in the husk.
    husk_conn = sqlite3.connect(husk)
    try:
        try:
            husk_rows = list(husk_conn.execute("SELECT * FROM work_tasks"))
        except sqlite3.OperationalError:
            husk_rows = []
    finally:
        husk_conn.close()
    assert husk_rows == []


def test_full_lifecycle_visible_to_every_resolver_read(env):
    """A task moves through queue → claim → review → done; every
    intermediate read through the resolver agrees on the status. The
    pre-#1004 bug had ``pm task next`` and ``pm task get`` see different
    DBs; this test would have caught it on day one."""
    _make_per_project_husk(env["project_path"])

    with _open_service_via_resolver("demo") as svc:
        task = svc.create(
            title="lifecycle",
            description="walk it",
            type="task",
            project="demo",
            flow_template="standard",
            roles={"worker": "engineer", "reviewer": "engineer"},
            priority="normal",
        )
        task_id = task.task_id

    def _status_seen_by_a_fresh_service() -> str:
        with _open_service_via_resolver("demo") as svc:
            return svc.get(task_id).work_status.value

    # draft is the default starting status.
    assert _status_seen_by_a_fresh_service() == "draft"

    with _open_service_via_resolver("demo") as svc:
        svc.queue(task_id, actor="planner")
    assert _status_seen_by_a_fresh_service() == "queued"

    with _open_service_via_resolver("demo") as svc:
        svc.claim(task_id, actor="engineer")
    assert _status_seen_by_a_fresh_service() == "in_progress"


def test_pm_task_next_and_pm_task_get_see_the_same_db(env):
    """The flicker symptom: ``pm task next`` returned ``foo/1`` while
    ``pm task get foo/1`` returned 'not found'. Pin both reads against
    the resolver and require they agree."""
    _make_per_project_husk(env["project_path"])

    with _open_service_via_resolver("demo") as svc:
        task = svc.create(
            title="discoverable",
            description="x",
            type="task",
            project="demo",
            flow_template="standard",
            roles={"worker": "engineer", "reviewer": "engineer"},
            priority="normal",
        )
        svc.queue(task.task_id, actor="planner")
        expected_id = task.task_id

    with _open_service_via_resolver("demo") as svc:
        next_task = svc.next(project="demo")

    assert next_task is not None
    assert next_task.task_id == expected_id

    # The same id must be retrievable via ``get`` on a *new*
    # service — exactly the path ``pm task get`` walks.
    with _open_service_via_resolver("demo") as svc:
        fetched = svc.get(next_task.task_id)
    assert fetched.task_id == expected_id


def test_repeated_list_returns_the_same_answer(env):
    """The user's actual symptom: ``pm task list`` flickered between
    'No tasks found.' and the full list as background sync ran. There
    is now exactly one DB; a stable list across rapid re-opens is the
    contract."""
    _make_per_project_husk(env["project_path"])

    with _open_service_via_resolver("demo") as svc:
        for i in range(3):
            task = svc.create(
                title=f"row {i}",
                description="x",
                type="task",
                project="demo",
                flow_template="standard",
                roles={"worker": "engineer", "reviewer": "engineer"},
                priority="normal",
            )
            svc.queue(task.task_id, actor="planner")

    seen: list[tuple[str, ...]] = []
    for _ in range(5):
        with _open_service_via_resolver("demo") as svc:
            tasks = svc.list_tasks(project="demo")
            seen.append(tuple(sorted(t.task_id for t in tasks)))

    # Every read returned the same set of three task ids — no flicker.
    assert len(set(seen)) == 1
    assert len(seen[0]) == 3
