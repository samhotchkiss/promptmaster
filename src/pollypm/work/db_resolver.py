"""Resolve the work-service SQLite database path for a project.

Public, non-CLI module so callers outside the Typer CLI (notably
``supervisor_alerts``) can find a project's work DB without importing
``pollypm.work.cli`` and reaching into ``_resolve_db_path`` (#804).

Layout (post-#339):

* ``<workspace_root>/.pollypm/state.db`` is the workspace-scoped DB
  used by ``pm notify``, ``pm inbox``, and every workspace-scoped
  task command. Project isolation lives in the ``scope`` column.
* ``<project>/.pollypm/state.db`` exists when a project's architect
  emits tasks via the per-project flow. When a ``project`` hint is
  supplied and the per-project DB exists, route there.

The function never raises — config-load failures fall through to the
workspace-root default (or, in the absolute worst case, a cwd-relative
default). Callers that genuinely care about a missing DB should check
``Path.exists()`` on the returned path.
"""

from __future__ import annotations

from pathlib import Path

WORKSPACE_DEFAULT_DB_PATH = ".pollypm/state.db"


def resolve_work_db_path(db: str = WORKSPACE_DEFAULT_DB_PATH, project: str | None = None) -> Path:
    """Resolve the work-service DB path for ``db`` / optional ``project``.

    Behaviour:

    1. If ``db`` is anything other than the canonical default
       (``.pollypm/state.db``), it's an explicit override — return it
       verbatim. Tests and CI rely on this escape hatch.
    2. If a registered ``project`` is supplied AND that project has a
       per-project state.db on disk, return that path.
    3. Otherwise return ``<workspace_root>/.pollypm/state.db`` from the
       loaded config.
    4. As a last resort (no config), return the cwd-relative default.
    """
    is_default = db == WORKSPACE_DEFAULT_DB_PATH

    # Explicit override wins.
    if not is_default:
        db_path = Path(db)
        if db_path.exists():
            return db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return db_path

    # Per-project DB when one exists for the named project.
    if project:
        try:
            from pollypm.config import load_config

            config = load_config()
            known = getattr(config, "projects", {}) or {}
            project_cfg = known.get(project)
            if project_cfg is not None:
                project_db = Path(project_cfg.path) / ".pollypm" / "state.db"
                if project_db.exists():
                    return project_db
        except Exception:  # noqa: BLE001
            pass

    # Workspace-root default — every ``pm notify`` / ``pm inbox`` call
    # without an explicit ``--db`` lands here so items stay visible
    # regardless of cwd.
    try:
        from pollypm.config import load_config

        config = load_config()
        workspace_root = getattr(config.project, "workspace_root", None)
        if workspace_root is not None:
            candidate = Path(workspace_root) / ".pollypm" / "state.db"
            candidate.parent.mkdir(parents=True, exist_ok=True)
            return candidate
    except Exception:  # noqa: BLE001
        pass

    # Fallback: cwd-relative default when no config is loadable.
    db_path = Path(db)
    if db_path.exists():
        return db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


__all__ = ["WORKSPACE_DEFAULT_DB_PATH", "resolve_work_db_path"]
