from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pollypm.config import load_config
from pollypm.projects import ensure_project_scaffold
from pollypm.service_api import PollyPMService
from pollypm.task_backends import get_task_backend
from pollypm.worktrees import list_worktrees

# ---------------------------------------------------------------------------
# Dashboard section helpers (#403). The heavy rendering lives in
# ``pollypm.cockpit_sections``; we re-export the legacy names here so
# external callers + the test suite keep their existing import paths.
# ---------------------------------------------------------------------------
from pollypm.cockpit_sections import (  # noqa: F401  (re-exported for callers)
    _DASHBOARD_BULLET,
    _DASHBOARD_DIVIDER_WIDTH,
    _DASHBOARD_PROJECT_CACHE,
    _STATUS_ICONS,
    _age_from_dt,
    _aggregate_project_tokens,
    _build_dashboard,
    _dashboard_divider,
    _dashboard_project_tasks,
    _find_commit_sha,
    _format_clock,
    _format_tokens,
    _iso_to_dt,
    _render_project_dashboard,
    _section_activity,
    _section_downtime,
    _section_header,
    _section_in_flight,
    _section_insights,
    _section_quick_actions,
    _section_recent,
    _section_summary,
    _section_velocity,
    _section_you_need_to,
    _spark_bar,
    _task_cycle_minutes,
    _worker_presence,
)


def _render_work_service_issues(project: object) -> str:
    """Render tasks from the work service for a project."""
    from pollypm.work.sqlite_service import SQLiteWorkService

    db_path = project.path / ".pollypm" / "state.db"
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with SQLiteWorkService(db_path=db_path, project_path=project.path) as svc:
        counts = svc.state_counts(project=getattr(project, "key", None))
        tasks = svc.list_tasks(project=getattr(project, "key", None))

    name = getattr(project, "name", None) or getattr(project, "key", "Project")
    lines = [f"{name} · Tasks", ""]

    # Summary bar
    count_parts = []
    for status in ("queued", "in_progress", "review", "blocked", "on_hold", "draft"):
        n = counts.get(status, 0)
        if n:
            icon = _STATUS_ICONS.get(status, "·")
            count_parts.append(f"{icon} {n} {status.replace('_', ' ')}")
    if count_parts:
        lines.append(" · ".join(count_parts))
        lines.append("")

    # Active tasks (non-terminal) — sorted by status priority
    _so = {"in_progress": 0, "review": 1, "queued": 2, "blocked": 3, "on_hold": 4, "draft": 5}
    active = [t for t in tasks if t.work_status.value not in ("done", "cancelled")]
    active.sort(key=lambda t: _so.get(t.work_status.value, 9))
    if active:
        for t in active:
            icon = _STATUS_ICONS.get(t.work_status.value, "·")
            assignee = f" [{t.assignee}]" if t.assignee else ""
            lines.append(f"  {icon} #{t.task_number} {t.title}{assignee}")
        lines.append("")

    # Recently completed (last 10)
    completed = [t for t in tasks if t.work_status.value in ("done", "cancelled")]
    completed.sort(key=lambda t: t.updated_at or "", reverse=True)
    if completed:
        lines.append(f"─── completed ({len(completed)}) ───")
        for t in completed[:10]:
            icon = _STATUS_ICONS.get(t.work_status.value, "·")
            lines.append(f"  {icon} #{t.task_number} {t.title}")
        lines.append("")

    if not tasks:
        lines.append("No tasks found.")

    return "\n".join(lines)


def _inbox_db_sources(config) -> list[tuple[str | None, Path, Path]]:
    """Return ``(project_key, db_path, project_path)`` for every inbox source.

    Includes the per-project ``.pollypm/state.db`` for every registered
    project **and** the workspace-root ``<workspace_root>/.pollypm/state.db``
    — the latter is where ``pm notify`` (with defaults) lands items that
    don't belong to any one project (#271). Duplicates are dropped so a
    project whose path happens to equal the workspace root is scanned once.

    ``project_key`` is ``None`` for the workspace-root source; callers that
    need a project filter treat ``None`` as "no filter".
    """
    sources: list[tuple[str | None, Path, Path]] = []
    seen: set[Path] = set()
    for project_key, project in getattr(config, "projects", {}).items():
        project_path = Path(project.path)
        db_path = project_path / ".pollypm" / "state.db"
        resolved = db_path.resolve() if db_path.exists() else db_path
        if resolved in seen:
            continue
        seen.add(resolved)
        sources.append((project_key, db_path, project_path))

    workspace_root = getattr(getattr(config, "project", None), "workspace_root", None)
    if workspace_root is not None:
        ws_path = Path(workspace_root)
        ws_db = ws_path / ".pollypm" / "state.db"
        resolved = ws_db.resolve() if ws_db.exists() else ws_db
        if resolved not in seen:
            seen.add(resolved)
            sources.append((None, ws_db, ws_path))
    return sources


def _count_inbox_tasks_for_label(config) -> int:
    """Sum of inbox tasks across all tracked projects + workspace-root.

    Used by the cockpit rail label and the dashboard summary line; the
    aggregate must match what :func:`_render_inbox_panel` would render.
    Dedupes tasks by ``task_id`` so a task that somehow appears in more
    than one DB counts exactly once.
    """
    try:
        from pollypm.work.inbox_view import inbox_tasks
        from pollypm.work.sqlite_service import SQLiteWorkService
    except Exception:  # noqa: BLE001
        return 0
    seen_task_ids: set[str] = set()
    for project_key, db_path, project_path in _inbox_db_sources(config):
        if not db_path.exists():
            continue
        try:
            with SQLiteWorkService(
                db_path=db_path, project_path=project_path,
            ) as svc:
                for task in inbox_tasks(svc, project=project_key):
                    seen_task_ids.add(task.task_id)
        except Exception:  # noqa: BLE001
            continue
    return len(seen_task_ids)


def render_inbox_panel(service, projects: list[object] | None = None) -> str:
    """Render the cockpit inbox panel from a WorkService.

    Groups tasks by project (when multiple project roots are tracked) and
    lists each one on two lines — subject-like header + status/when subline
    — matching the visual shape of the legacy inbox panel.

    ``projects`` is an optional list of project objects (each with ``.key``
    and ``.name``) used solely to resolve display names; when omitted the
    raw project key is shown.
    """
    from pollypm.work.inbox_view import inbox_tasks

    project_names: dict[str, str] = {}
    for proj in projects or ():
        key = getattr(proj, "key", None)
        if key:
            project_names[key] = getattr(proj, "name", None) or key

    tasks = inbox_tasks(service)
    assigned = len(tasks)

    lines = ["Inbox"]
    if not tasks:
        lines.extend(
            [
                "",
                "No tasks waiting for you.",
                "",
                "This panel shows non-terminal tasks whose current node is",
                "assigned to you (actor_type: human) or whose roles include",
                "the 'user' role.",
            ]
        )
        lines.extend(
            [
                "",
                f"Assigned: {assigned}",
                "List: pm inbox",
                "Show: pm inbox show <task_id>",
            ]
        )
        return "\n".join(lines)

    lines.extend(["", f"Assigned ({assigned}):"])
    for task in tasks[:10]:
        prio = task.priority.value
        prefix = ""
        if prio == "critical":
            prefix = "▲ "
        elif prio == "high":
            prefix = "◆ "
        subject = task.title[:60]
        lines.append(f"  {prefix}{subject}")

        updated = task.updated_at
        if updated is not None and hasattr(updated, "isoformat"):
            when = updated.isoformat()[:16]
        else:
            when = (str(updated) if updated else "")[:16]
        project_label = project_names.get(task.project, task.project)
        node_part = f" · @{task.current_node_id}" if task.current_node_id else ""
        lines.append(
            f"    {task.task_id} · {project_label} · {task.work_status.value}"
            f"{node_part} · {when}"
        )

        preview = (task.description or "").strip().split("\n", 1)[0][:70]
        if preview:
            lines.append(f"    {preview}")
        lines.append("")

    if assigned > 10:
        lines.append(f"… and {assigned - 10} more")
        lines.append("")

    lines.extend(
        [
            "List: pm inbox",
            "Show: pm inbox show <task_id>",
        ]
    )
    return "\n".join(lines)


def _render_inbox_panel(config) -> str:
    """Render the inbox panel for the active cockpit config.

    Opens every tracked project's work-service DB, queries the inbox from
    each, and renders the combined view. Projects with no DB are silently
    skipped so a fresh install stays usable.
    """
    from pollypm.work.sqlite_service import SQLiteWorkService

    # Aggregate inbox tasks across all tracked projects. Each project has its
    # own SQLite db; we open each, query, then close.
    class _AggregateService:
        """Adapter exposing the subset of WorkService used by inbox_view."""

        def __init__(self) -> None:
            self._tasks: list = []
            self._flow_cache: dict = {}
            self._flows_by_svc: list = []

        def list_tasks(self, *, project=None, **_ignored):
            if project is None:
                return list(self._tasks)
            return [t for t in self._tasks if t.project == project]

        def get_flow(self, name: str, project=None):
            key = (name, project)
            if key in self._flow_cache:
                return self._flow_cache[key]
            for svc in self._flows_by_svc:
                try:
                    flow = svc.get_flow(name, project=project)
                except Exception:  # noqa: BLE001
                    continue
                self._flow_cache[key] = flow
                return flow
            raise KeyError(f"flow {name!r} not found")

    agg = _AggregateService()
    opened: list[SQLiteWorkService] = []
    seen_task_ids: set[str] = set()
    try:
        for project_key, db_path, project_path in _inbox_db_sources(config):
            if not db_path.exists():
                continue
            try:
                svc = SQLiteWorkService(
                    db_path=db_path, project_path=project_path,
                )
            except Exception:  # noqa: BLE001
                continue
            opened.append(svc)
            agg._flows_by_svc.append(svc)
            try:
                for task in svc.list_tasks(project=project_key):
                    if task.task_id in seen_task_ids:
                        continue
                    seen_task_ids.add(task.task_id)
                    agg._tasks.append(task)
            except Exception:  # noqa: BLE001
                pass

        projects_iter = list(getattr(config, "projects", {}).values())
        return render_inbox_panel(agg, projects=projects_iter)
    finally:
        for svc in opened:
            try:
                svc.close()
            except Exception:  # noqa: BLE001
                pass



def build_cockpit_detail(config_path: Path, kind: str, target: str | None = None) -> str:
    try:
        return _build_cockpit_detail_inner(config_path, kind, target)
    except Exception as exc:  # noqa: BLE001
        return f"Error loading {kind} view: {exc}"


def _build_cockpit_detail_inner(config_path: Path, kind: str, target: str | None = None) -> str:
    supervisor = PollyPMService(config_path).load_supervisor()
    try:
        supervisor.ensure_layout()
        return _build_cockpit_detail_dispatch(supervisor, config_path, kind, target)
    finally:
        try:
            supervisor.store.close()
        except Exception:  # noqa: BLE001
            pass


def _build_cockpit_detail_dispatch(supervisor, config_path: Path, kind: str, target: str | None = None) -> str:
    config = supervisor.config
    if kind in ("polly", "dashboard"):
        return _build_dashboard(supervisor, config)

    if kind == "inbox":
        return _render_inbox_panel(config)

    if kind == "workers":
        return _render_worker_roster_panel(config_path)

    if kind == "metrics":
        return _render_metrics_panel(config_path)

    if kind == "activity":
        from pollypm.plugins_builtin.activity_feed.cockpit.feed_panel import (
            render_activity_feed_text,
        )

        return render_activity_feed_text(config)

    if kind == "settings":
        recent_usage = supervisor.store.recent_token_usage(limit=5)
        lines = [
            "Settings",
            "",
            f"Workspace root: {config.project.workspace_root}",
            f"Control account: {config.pollypm.controller_account}",
            f"Failover order: {', '.join(config.pollypm.failover_accounts) or 'none'}",
            f"Open permissions by default: {'on' if config.pollypm.open_permissions_by_default else 'off'}",
            "",
            "This pane is read-only for now.",
            "Use Polly or the legacy `pm ui` surface for deeper account/runtime changes.",
        ]
        if recent_usage:
            lines.extend(["", "Recent token usage:"])
            for row in recent_usage[:4]:
                lines.append(
                    f"- {row.project_key} · {row.account_name} · {row.model_name} · {row.tokens_used} tokens"
                )
        return "\n".join(lines)

    if kind == "project" and target:
        project = config.projects.get(target)
        if project is None:
            return f"Project '{target}' not found in config.\n\nIt may not have been saved. Try `pm add-project <path>` or check ~/.pollypm/pollypm.toml."
        ensure_project_scaffold(project.path)

        # Try work service dashboard first
        try:
            dashboard = _render_project_dashboard(project, target, config_path, supervisor)
            if dashboard:
                return dashboard
        except Exception:
            pass

        # Fallback to basic project info
        task_backend = get_task_backend(project.path)
        issues_root = task_backend.issues_root()
        state_counts = task_backend.state_counts() if task_backend.exists() else {}
        worktrees = [item for item in list_worktrees(config_path, target) if item.status == "active"]
        lines = [
            f"{project.name or project.key}",
            "",
            f"Path: {project.path}",
            f"Kind: {project.kind.value}",
            f"Tracked: {'yes' if project.tracked else 'no'}",
            f"Issue tracker: {issues_root if task_backend.exists() else 'not initialized'}",
            f"Active worktrees: {len(worktrees)}",
            "",
            "No active live lane is running for this project.",
            "Select the project in the left rail and press N to start a worker lane.",
        ]
        # Show alerts for this project's sessions
        project_alerts = [
            a for a in supervisor.store.open_alerts()
            if any(
                l.session.project == target and l.session.name == a.session_name
                for l in supervisor.plan_launches()
            ) and a.alert_type not in ("suspected_loop", "stabilize_failed", "needs_followup")
        ]
        if project_alerts:
            lines.extend(["", "⚠ Alerts:"])
            for a in project_alerts:
                lines.append(f"  {a.severity} {a.alert_type}: {a.message}")
            lines.append("")
        if state_counts:
            lines.extend(["Task states:"])
            for state, count in state_counts.items():
                if count:
                    lines.append(f"- {state}: {count}")
        return "\n".join(lines)

    if kind == "issues" and target:
        project = config.projects.get(target)
        if not project:
            return f"Project '{target}' not found."
        # Try the work service first, fall back to file-based backend
        try:
            return _render_work_service_issues(project)
        except Exception:
            pass
        task_backend = get_task_backend(project.path)
        if not task_backend.exists():
            return f"{project.name or project.key} · Issues\n\nNo issue tracker initialized.\nUse `pm init-tracker {target}` to create one."
        state_counts = task_backend.state_counts()
        lines = [f"{project.name or project.key} · Issues", ""]
        for state_name in ["01-ready", "02-in-progress", "03-needs-review", "04-in-review", "05-completed"]:
            count = state_counts.get(state_name, 0)
            if count:
                tasks = task_backend.list_tasks(states=[state_name])
                lines.append(f"─── {state_name} ({count}) ───")
                for task in tasks[:8]:
                    lines.append(f"  {task.task_id}: {task.title}")
                lines.append("")
        if not any(state_counts.values()):
            lines.append("No issues found.")
        return "\n".join(lines)

    return "PollyPM\n\nSelect Polly, Inbox, a project, or Settings from the left rail."


# ---------------------------------------------------------------------------
# Worker roster — a live mission-control view that spans every project.
# ``_gather_worker_roster`` walks the config, opens each project's
# ``.pollypm/state.db`` to find active task assignments, cross-references
# tmux windows + supervisor state, and produces one ``WorkerRosterRow``
# per worker session. The row is the stable shape every renderer + test
# consumes — keep it data-only (no Textual imports).
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WorkerRosterRow:
    """One worker session, flattened for the roster table.

    The row carries both display-ready bits (``status``, ``turn_label``)
    and the raw data the cockpit needs to route a jump (``tmux_window``,
    ``session_name``). ``status`` is one of ``working``/``idle``/``stuck``/
    ``offline`` so the renderer can pick the dot colour + sort bucket
    without re-inspecting the underlying signals.
    """

    project_key: str
    project_name: str
    session_name: str
    status: str  # "working" | "idle" | "stuck" | "offline"
    task_id: str | None
    task_number: int | None
    task_title: str
    current_node: str | None
    turn_label: str
    last_commit_label: str
    tmux_window: str | None  # e.g. "task-<project>-<number>"
    last_heartbeat: str | None
    worktree_path: str | None
    branch_name: str | None


# Order used by the renderer + sort helpers. Lower = earlier in the table.
_WORKER_ROSTER_STATUS_ORDER: dict[str, int] = {
    "stuck": 0,
    "working": 1,
    "idle": 2,
    "offline": 3,
}


def _worker_roster_sort_key(row: "WorkerRosterRow") -> tuple[int, str]:
    return (
        _WORKER_ROSTER_STATUS_ORDER.get(row.status, 9),
        (row.project_name or row.project_key or "").lower(),
    )


def _format_worker_turn_label(
    *, last_heartbeat_iso: str | None, is_turn_active: bool,
) -> str:
    """Return ``active 2m`` / ``idle 12m`` / ``—`` from heartbeat state."""
    if not last_heartbeat_iso:
        return "\u2014"
    from datetime import UTC, datetime
    try:
        dt = datetime.fromisoformat(last_heartbeat_iso)
    except (TypeError, ValueError):
        return "\u2014"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - dt
    total_s = max(0, int(delta.total_seconds()))
    mins = total_s // 60 if total_s >= 60 else 0
    if mins < 1:
        unit = "now"
    elif mins < 60:
        unit = f"{mins}m"
    elif mins < 60 * 24:
        unit = f"{mins // 60}h"
    else:
        unit = f"{mins // (60 * 24)}d"
    prefix = "active" if is_turn_active else "idle"
    if unit == "now":
        return f"{prefix} now"
    return f"{prefix} {unit}"


def _last_commit_age(project_path: Path, branch_name: str | None) -> str:
    """Return a short relative-age string for the worker's branch tip.

    Best-effort: we only run ``git log -1 --format=%ct`` so a missing
    branch / non-repo path falls through to the empty-age dash.
    """
    if not branch_name:
        return "\u2014"
    try:
        import subprocess as _sp
        result = _sp.run(
            ["git", "-C", str(project_path), "log", "-1", "--format=%ct", branch_name],
            capture_output=True, text=True, check=False, timeout=2,
        )
    except Exception:  # noqa: BLE001
        return "\u2014"
    if result.returncode != 0:
        return "\u2014"
    ts_raw = (result.stdout or "").strip()
    if not ts_raw:
        return "\u2014"
    try:
        ts = int(ts_raw)
    except ValueError:
        return "\u2014"
    from datetime import UTC, datetime
    dt = datetime.fromtimestamp(ts, tz=UTC)
    delta = datetime.now(UTC) - dt
    total_s = max(0, int(delta.total_seconds()))
    if total_s < 60:
        return "just now"
    if total_s < 3600:
        return f"{total_s // 60}m ago"
    if total_s < 3600 * 24:
        return f"{total_s // 3600}h ago"
    return f"{total_s // (3600 * 24)}d ago"


def _gather_worker_roster(config) -> list[WorkerRosterRow]:
    """Walk every tracked project's work-service + tmux state.

    Returns one ``WorkerRosterRow`` per active worker session. Offline
    sessions (worker record exists but tmux window is gone) are included
    so Sam can see workers that have crashed.

    Always best-effort: a bad DB or missing tmux session degrades one
    project's worth of rows, never the whole roster.
    """
    from pollypm.work.sqlite_service import SQLiteWorkService

    projects = getattr(config, "projects", {}) or {}
    if not projects:
        return []

    # Supervisor state is optional — the roster is valuable even without
    # a running tmux server (e.g. headless CI / tests). We degrade to
    # offline + empty turn labels when either one is unavailable.
    supervisor = _try_load_supervisor_for_config(config)

    tmux = None
    storage_windows: dict[str, object] = {}
    try:
        from pollypm.session_services import create_tmux_client
        tmux = create_tmux_client()
        storage_session = f"{config.project.tmux_session}-storage-closet"
        try:
            storage_window_list = tmux.list_windows(storage_session)
        except Exception:  # noqa: BLE001
            storage_window_list = []
        storage_windows = {w.name: w for w in storage_window_list}
    except Exception:  # noqa: BLE001
        tmux = None
        storage_windows = {}

    rows: list[WorkerRosterRow] = []

    for project_key, project in projects.items():
        project_path = getattr(project, "path", None)
        project_name = (
            project.display_label() if hasattr(project, "display_label")
            else (getattr(project, "name", None) or project_key)
        )
        if project_path is None or not isinstance(project_path, Path):
            continue
        db_path = project_path / ".pollypm" / "state.db"
        if not db_path.exists():
            continue
        try:
            svc = SQLiteWorkService(db_path=db_path, project_path=project_path)
        except Exception:  # noqa: BLE001
            continue
        try:
            try:
                worker_sessions = svc.list_worker_sessions(
                    project=project_key, active_only=True,
                )
            except Exception:  # noqa: BLE001
                worker_sessions = []
            task_lookup: dict[int, object] = {}
            try:
                for t in svc.list_tasks(project=project_key):
                    tn = getattr(t, "task_number", None)
                    if isinstance(tn, int):
                        task_lookup[tn] = t
            except Exception:  # noqa: BLE001
                task_lookup = {}
        finally:
            try:
                svc.close()
            except Exception:  # noqa: BLE001
                pass

        for ws in worker_sessions:
            task = task_lookup.get(ws.task_number)
            task_title_full = (
                getattr(task, "title", "") or "" if task is not None else ""
            )
            task_title = task_title_full[:40]
            task_id = getattr(task, "task_id", None) if task is not None else None
            current_node = (
                getattr(task, "current_node_id", None)
                if task is not None else None
            )
            window_name = f"task-{project_key}-{ws.task_number}"
            window = storage_windows.get(window_name)
            has_window = window is not None and not getattr(
                window, "pane_dead", False,
            )
            session_name = ws.agent_name or window_name

            last_heartbeat_iso: str | None = None
            is_turn_active = False
            if has_window and tmux is not None:
                try:
                    pane_text = tmux.capture_pane(window.pane_id, lines=15)
                except Exception:  # noqa: BLE001
                    pane_text = ""
                lowered = pane_text.lower()
                if "\u23fa" in pane_text or "working" in lowered:
                    is_turn_active = True
                if (
                    "working (" in lowered
                    and "esc to interrupt" in lowered
                ):
                    is_turn_active = True

            stuck = False
            if supervisor is not None:
                try:
                    drift_at = supervisor.store.last_event_at(
                        session_name, "state_drift",
                    )
                except Exception:  # noqa: BLE001
                    drift_at = None
                if drift_at:
                    from datetime import UTC, datetime, timedelta
                    try:
                        drift_dt = datetime.fromisoformat(drift_at)
                    except (TypeError, ValueError):
                        drift_dt = None
                    if drift_dt is not None:
                        if drift_dt.tzinfo is None:
                            drift_dt = drift_dt.replace(tzinfo=UTC)
                        if drift_dt >= datetime.now(UTC) - timedelta(minutes=30):
                            stuck = True
                try:
                    hb = supervisor.store.latest_heartbeat(session_name)
                except Exception:  # noqa: BLE001
                    hb = None
                if hb is not None:
                    last_heartbeat_iso = getattr(hb, "created_at", None)

            if not has_window:
                status = "offline"
            elif stuck:
                status = "stuck"
            elif is_turn_active:
                status = "working"
            else:
                status = "idle"

            turn_label = _format_worker_turn_label(
                last_heartbeat_iso=last_heartbeat_iso,
                is_turn_active=is_turn_active,
            )
            last_commit_label = _last_commit_age(project_path, ws.branch_name)

            rows.append(
                WorkerRosterRow(
                    project_key=project_key,
                    project_name=str(project_name),
                    session_name=session_name,
                    status=status,
                    task_id=task_id,
                    task_number=ws.task_number,
                    task_title=task_title,
                    current_node=current_node,
                    turn_label=turn_label,
                    last_commit_label=last_commit_label,
                    tmux_window=window_name,
                    last_heartbeat=last_heartbeat_iso,
                    worktree_path=ws.worktree_path,
                    branch_name=ws.branch_name,
                )
            )

    if supervisor is not None:
        try:
            supervisor.store.close()
        except Exception:  # noqa: BLE001
            pass

    rows.sort(key=_worker_roster_sort_key)
    return rows


def _try_load_supervisor_for_config(config):
    """Best-effort supervisor load from a ``PollyPMConfig`` — returns
    ``None`` if we can't find a canonical config file path to feed into
    :class:`PollyPMService`. The roster still renders useful data without
    a supervisor (no drift detection, no heartbeat ages), so this is not
    fatal.
    """
    try:
        cfg_path = getattr(config.project, "config_file", None) or getattr(
            config.project, "config_path", None,
        )
    except Exception:  # noqa: BLE001
        cfg_path = None
    if cfg_path is None:
        try:
            root = getattr(config.project, "root_dir", None)
            if root is not None:
                candidate = root / "pollypm.toml"
                cfg_path = candidate if candidate.exists() else None
        except Exception:  # noqa: BLE001
            cfg_path = None
    if cfg_path is None:
        return None
    try:
        from pollypm.service_api import PollyPMService
        return PollyPMService(cfg_path).load_supervisor()
    except Exception:  # noqa: BLE001
        return None


def _render_worker_roster_panel(config_path: Path) -> str:
    """Fallback text-only roster — used by ``build_cockpit_detail`` when
    the right pane hasn't launched the Textual app yet (e.g. in
    automated tests that poke the static-view plumbing).

    The production path is the ``PollyWorkerRosterApp`` Textual screen
    launched via ``pm cockpit-pane workers``.
    """
    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        return f"Workers\n\nError loading config: {exc}"
    rows = _gather_worker_roster(config)
    if not rows:
        return (
            "Workers\n\n"
            "No active worker sessions.\n\n"
            "Start one with `pm cockpit-pane project <key>` and press `w`."
        )
    lines = ["Workers", ""]
    dot_for = {
        "working": "\u25cf", "idle": "\u25cb",
        "stuck": "\u25b2", "offline": "\u25cf",
    }
    for row in rows:
        dot = dot_for.get(row.status, "\u25cb")
        task_part = (
            f"#{row.task_number} {row.task_title}"
            if row.task_number is not None else "(none)"
        )
        lines.append(
            f"  {dot} {row.status:<7}  {row.project_name:<18}  "
            f"{row.session_name:<22}  {task_part}  @{row.current_node or '-'}  "
            f"{row.turn_label}  {row.last_commit_label}"
        )
    return "\n".join(lines)


def _gather_activity_feed(
    config,
    *,
    project: str | None = None,
    limit: int = 200,
):
    """Project the live activity feed for the cockpit's full-screen view.

    Returns a list of :class:`FeedEntry` records (newest first). Filters
    by ``project`` server-side when provided so the loaded window is
    already scoped — client-side filters in the Textual app then apply
    on the in-memory rows without another DB hit.

    Best-effort: if the projector or any DB read fails, returns an
    empty list rather than propagating the error. The Textual screen
    surfaces the empty state as a friendly placeholder.
    """
    try:
        from pollypm.plugins_builtin.activity_feed.plugin import build_projector
    except Exception:  # noqa: BLE001
        return []
    projector = build_projector(config)
    if projector is None:
        return []
    try:
        return projector.project(
            limit=limit,
            projects=[project] if project else None,
        )
    except Exception:  # noqa: BLE001
        return []


def _register_worker_roster_rail_item(registry, router) -> None:
    """Add the ``top.Workers`` rail row if not already registered.

    Kept out of ``core_rail_items`` so the roster's rail row, its route
    dispatch (``route_selected("workers")``) and its Textual app land in
    one patch rather than being spread across the plugin boundary. The
    registry dedupes on ``(plugin_name, section, label)``, so calling
    this every tick is safe.
    """
    try:
        from pollypm.plugin_api.v1 import RailItemRegistration, PanelSpec
    except Exception:  # noqa: BLE001
        return

    def _label(ctx) -> str:
        extras = getattr(ctx, "extras", {}) or {}
        cfg = extras.get("config")
        try:
            count = len(_gather_worker_roster(cfg)) if cfg is not None else 0
        except Exception:  # noqa: BLE001
            count = 0
        return f"Workers ({count})" if count else "Workers"

    def _state(ctx) -> str:
        extras = getattr(ctx, "extras", {}) or {}
        cfg = extras.get("config")
        try:
            rows = _gather_worker_roster(cfg) if cfg is not None else []
        except Exception:  # noqa: BLE001
            rows = []
        if any(r.status == "stuck" for r in rows):
            return "! stuck"
        if any(r.status == "working" for r in rows):
            return "\u25c6 working"
        return "idle"

    def _handler(ctx):
        try:
            router.route_selected("workers")
        except Exception:  # noqa: BLE001
            pass
        return PanelSpec(widget=None, focus_hint="workers")

    reg = RailItemRegistration(
        plugin_name="cockpit_worker_roster",
        section="top",
        index=25,  # after Inbox (20), before Projects
        label="Workers",
        handler=_handler,
        key="workers",
        state_provider=_state,
        label_provider=_label,
    )
    try:
        registry.add(reg)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Observability metrics snapshot — the data source behind the fifth cockpit
# surface (``PollyMetricsApp``). Kept in ``cockpit.py`` alongside the worker
# roster gather so the metrics screen can reuse existing helpers
# (``_gather_worker_roster``, ``_dashboard_project_tasks``,
# ``_count_inbox_tasks_for_label``) without duplication. The snapshot is a
# plain dataclass so tests + renderer consume the same shape.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MetricsSection:
    """One of the five metrics sections the screen renders.

    ``rows`` is a list of ``(label, value, tone)`` triples. ``tone`` is
    one of ``"ok"``, ``"warn"``, ``"alert"``, ``"muted"`` — the renderer
    maps each to a Rich colour consistent with the cockpit palette.
    """

    key: str     # "fleet" | "resources" | "throughput" | "failures" | "schedulers"
    title: str
    rows: list[tuple[str, str, str]]


@dataclass(slots=True)
class MetricsSnapshot:
    """Immutable snapshot of system health numbers.

    Built by :func:`_gather_metrics_snapshot` and rendered by the Textual
    ``PollyMetricsApp`` screen. Every field is best-effort — a failing
    subsystem lands as ``"?"`` in the row rather than propagating a
    crash. The snapshot also carries the ``captured_at`` ISO timestamp so
    the renderer can show "last refreshed" info without re-reading a
    clock.
    """

    captured_at: str
    fleet: MetricsSection
    resources: MetricsSection
    throughput: MetricsSection
    failures: MetricsSection
    schedulers: MetricsSection

    def sections(self) -> list[MetricsSection]:
        return [self.fleet, self.resources, self.throughput, self.failures, self.schedulers]


def _humanize_bytes(num: int | float) -> str:
    """Render a byte count in KB/MB/GB form with one decimal.

    Lives alongside the metrics gather because every resource row
    wants the same short-and-readable width.
    """
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def _dir_size_bytes(path: Path) -> int:
    """Walk ``path`` once and return the sum of ``st_size`` for files.

    Best-effort — symlinks are not followed, read errors are silently
    skipped. Missing paths return 0 so the metrics screen can still
    display a useful row.
    """
    total = 0
    if not path.exists():
        return 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _rss_bytes_for_pid(pid: int) -> int | None:
    """Resident-set-size for a PID in bytes, or ``None`` when unavailable.

    Shells out to ``ps -p <pid> -o rss=`` — portable across macOS + Linux
    and doesn't need psutil. The ``ps`` output is in kilobytes so we
    multiply back to bytes for the consumer.
    """
    try:
        import subprocess as _sp
        result = _sp.run(
            ["ps", "-p", str(pid), "-o", "rss="],
            capture_output=True, text=True, check=False, timeout=2,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        return int(raw) * 1024
    except ValueError:
        return None


def _metrics_24h_events(store, now=None) -> list:
    """Return the subset of ``recent_events`` within the last 24 hours.

    Reads up to 2 000 rows via ``recent_events`` and filters client-side.
    That's a bounded read and keeps the snapshot self-contained without
    adding a new StateStore method. Returns an empty list if the store
    read fails.
    """
    from datetime import UTC, datetime, timedelta
    if now is None:
        now = datetime.now(UTC)
    cutoff_iso = (now - timedelta(hours=24)).isoformat()
    try:
        recent = store.recent_events(limit=2000)
    except Exception:  # noqa: BLE001
        return []
    return [e for e in recent if getattr(e, "created_at", "") >= cutoff_iso]


def _fleet_section(config, roster_rows: list, task_counts: dict[str, int],
                   inbox_breakdown: dict[str, int]) -> MetricsSection:
    """Section 1 — Workers / Tasks in flight / Inbox rollup."""
    n_working = sum(1 for r in roster_rows if r.status == "working")
    n_idle = sum(1 for r in roster_rows if r.status == "idle")
    n_stuck = sum(1 for r in roster_rows if r.status == "stuck")
    n_offline = sum(1 for r in roster_rows if r.status == "offline")

    rows: list[tuple[str, str, str]] = []
    worker_tone = "alert" if n_stuck else ("ok" if n_working else "muted")
    rows.append(
        ("Workers",
         f"{n_working} working · {n_idle} idle · {n_stuck} stuck · {n_offline} offline",
         worker_tone),
    )

    queued = int(task_counts.get("queued", 0))
    in_progress = int(task_counts.get("in_progress", 0))
    review = int(task_counts.get("review", 0))
    blocked = int(task_counts.get("blocked", 0))
    flight_tone = "alert" if blocked else ("ok" if in_progress else "muted")
    rows.append(
        ("Tasks in flight",
         f"{queued} queued · {in_progress} in_progress · {review} review · {blocked} blocked",
         flight_tone),
    )

    unread = inbox_breakdown.get("unread", 0)
    plan_review = inbox_breakdown.get("plan_review", 0)
    blocking = inbox_breakdown.get("blocking_question", 0)
    inbox_tone = "warn" if (unread or plan_review or blocking) else "ok"
    rows.append(
        ("Inbox",
         f"{unread} unread · {plan_review} plan_review · {blocking} blocking_question",
         inbox_tone),
    )
    return MetricsSection(key="fleet", title="Fleet", rows=rows)


def _inbox_breakdown(config) -> dict[str, int]:
    """Return ``{"unread": N, "plan_review": N, "blocking_question": N}``.

    Uses the same scan as :func:`_count_inbox_tasks_for_label` so the
    numbers line up with the rail badge. Best-effort: returns zero-value
    dict on any error.
    """
    out = {"unread": 0, "plan_review": 0, "blocking_question": 0}
    try:
        from pollypm.work.inbox_view import inbox_tasks
        from pollypm.work.sqlite_service import SQLiteWorkService
    except Exception:  # noqa: BLE001
        return out
    seen: set[str] = set()
    for project_key, db_path, project_path in _inbox_db_sources(config):
        if not db_path.exists():
            continue
        try:
            with SQLiteWorkService(
                db_path=db_path, project_path=project_path,
            ) as svc:
                for task in inbox_tasks(svc, project=project_key):
                    if task.task_id in seen:
                        continue
                    seen.add(task.task_id)
                    labels = list(task.labels or [])
                    if "plan_review" in labels:
                        out["plan_review"] += 1
                    if "blocking_question" in labels:
                        out["blocking_question"] += 1
                    out["unread"] += 1
        except Exception:  # noqa: BLE001
            continue
    return out


def _resource_section(config) -> MetricsSection:
    """Section 2 — state.db size, worktrees, logs, session RSS."""
    rows: list[tuple[str, str, str]] = []

    # state.db size + freelist ratio for the workspace-root DB.
    state_db = getattr(getattr(config, "project", None), "state_db", None)
    if state_db is not None:
        state_db = Path(state_db)
    if state_db and state_db.exists():
        try:
            db_size = state_db.stat().st_size
        except OSError:
            db_size = 0
        freelist_ratio = 0.0
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
            try:
                page_size = int(
                    conn.execute("PRAGMA page_size").fetchone()[0] or 0,
                )
                freelist = int(
                    conn.execute("PRAGMA freelist_count").fetchone()[0] or 0,
                )
                if page_size and db_size:
                    freelist_ratio = (page_size * freelist) / db_size
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
        tone = "alert" if db_size > 500 * 1024 * 1024 else (
            "warn" if db_size > 100 * 1024 * 1024 else "ok"
        )
        rows.append(
            ("state.db",
             f"{_humanize_bytes(db_size)} · freelist {freelist_ratio*100:.1f}%",
             tone),
        )
    else:
        rows.append(("state.db", "(missing)", "muted"))

    # Agent worktrees — count + disk usage.
    workspace_root = getattr(getattr(config, "project", None), "workspace_root", None)
    wt_root = Path(workspace_root) / ".claude" / "worktrees" if workspace_root else None
    if wt_root and wt_root.exists():
        try:
            wt_count = sum(1 for p in wt_root.iterdir() if p.is_dir())
        except OSError:
            wt_count = 0
        wt_size = _dir_size_bytes(wt_root)
        tone = "alert" if wt_size > 10 * 1024**3 else (
            "warn" if wt_size > 2 * 1024**3 else "ok"
        )
        rows.append(
            (".claude/worktrees",
             f"{wt_count} worktree{'s' if wt_count != 1 else ''} · {_humanize_bytes(wt_size)}",
             tone),
        )
    else:
        rows.append((".claude/worktrees", "(none)", "muted"))

    # Log directory size under ~/.pollypm/logs/
    logs_dir = Path.home() / ".pollypm" / "logs"
    if logs_dir.exists():
        log_bytes = 0
        try:
            for f in logs_dir.glob("*.log*"):
                try:
                    log_bytes += f.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
        tone = "warn" if log_bytes > 500 * 1024 * 1024 else "ok"
        rows.append(("logs", _humanize_bytes(log_bytes), tone))
    else:
        rows.append(("logs", "(none)", "muted"))

    # Memory footprint per live session — sourced from tmux-tracked sessions.
    try:
        from pollypm.service_api import PollyPMService
        cfg_path = getattr(
            getattr(config, "project", None), "config_file", None,
        ) or getattr(
            getattr(config, "project", None), "config_path", None,
        )
        supervisor = None
        if cfg_path is not None:
            supervisor = PollyPMService(cfg_path).load_supervisor(readonly_state=True)
    except Exception:  # noqa: BLE001
        supervisor = None

    total_rss = 0
    live_count = 0
    if supervisor is not None:
        try:
            launches, windows, _alerts, _leases, _errors = supervisor.status()
        except Exception:  # noqa: BLE001
            launches, windows = [], []
        window_map = {w.name: w for w in windows}
        for launch in launches:
            window = window_map.get(launch.window_name)
            if window is None or getattr(window, "pane_dead", False):
                continue
            try:
                pid = int(getattr(window, "pane_pid", 0) or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid <= 0:
                continue
            rss = _rss_bytes_for_pid(pid)
            if rss is None:
                continue
            total_rss += rss
            live_count += 1
        try:
            supervisor.store.close()
        except Exception:  # noqa: BLE001
            pass
    mem_tone = "alert" if total_rss > 8 * 1024**3 else (
        "warn" if total_rss > 2 * 1024**3 else "ok"
    )
    if live_count:
        rows.append(
            ("Session RSS",
             f"{live_count} session{'s' if live_count != 1 else ''} · {_humanize_bytes(total_rss)}",
             mem_tone),
        )
    else:
        rows.append(("Session RSS", "(no live sessions)", "muted"))

    return MetricsSection(key="resources", title="Resources", rows=rows)


def _throughput_section(day_events: list) -> MetricsSection:
    """Section 3 — commits / approvals / completions in the last 24h."""
    rows: list[tuple[str, str, str]] = []

    def _count(pred) -> int:
        return sum(1 for e in day_events if pred(getattr(e, "event_type", "")))

    completed = _count(lambda k: k in ("task_done", "task.done"))
    rejected = _count(lambda k: "reject" in (k or "").lower())
    approvals = _count(
        lambda k: k in ("plan_approved", "plan.approved", "task.approved", "approve"),
    )
    commits = _count(lambda k: "commit" in (k or "").lower() or k == "ran")
    pr_reviews = _count(
        lambda k: "pr_reviewed" in (k or "") or k == "review_completed",
    )

    rows.append(("Tasks completed",
                 str(completed),
                 "ok" if completed else "muted"))
    rows.append(("Tasks rejected",
                 str(rejected),
                 "warn" if rejected else "muted"))
    rows.append(("PRs reviewed",
                 str(pr_reviews),
                 "ok" if pr_reviews else "muted"))
    rows.append(("Commits (worker events)",
                 str(commits),
                 "ok" if commits else "muted"))
    rows.append(("Plan approvals",
                 str(approvals),
                 "ok" if approvals else "muted"))
    return MetricsSection(key="throughput", title="Throughput (24h)", rows=rows)


def _failure_section(day_events: list) -> MetricsSection:
    """Section 4 — failure counts over the last 24h."""
    def _count(pred) -> int:
        return sum(1 for e in day_events if pred(getattr(e, "event_type", "")))

    state_drift = _count(lambda k: k == "state_drift")
    persona_swap = _count(lambda k: k == "persona_swap_detected" or "persona_swap" in (k or ""))
    reprompts = _count(lambda k: k in ("worker_reprompt", "reprompt", "worker_turn_end_reprompt"))
    no_session = _count(lambda k: "no_session" in (k or ""))
    probe_fail = _count(lambda k: "provider_probe" in (k or "") and "fail" in (k or ""))

    rows: list[tuple[str, str, str]] = []
    rows.append(("state_drift", str(state_drift), "alert" if state_drift else "ok"))
    rows.append(("persona_swap_detected", str(persona_swap), "alert" if persona_swap else "ok"))
    rows.append(("worker reprompts", str(reprompts), "warn" if reprompts else "ok"))
    rows.append(("no_session alerts", str(no_session), "alert" if no_session else "ok"))
    rows.append(("Provider probe failures", str(probe_fail), "warn" if probe_fail else "ok"))
    return MetricsSection(key="failures", title="Failures (24h)", rows=rows)


def _scheduler_section(store) -> MetricsSection:
    """Section 5 — last fire-at + staleness flag per scheduled handler.

    Pulls the last 500 events from the store and groups them by the
    scheduled-job "subject" (captured in the summary payload via the
    inline scheduler). We treat each distinct ``kind`` as a handler
    row. Staleness is a 2× cadence rule: if more than 2h elapsed on
    something expected hourly, flag it red. Cadence is inferred from
    the gap between the most recent two firings of the same kind.
    """
    from datetime import UTC, datetime
    rows: list[tuple[str, str, str]] = []
    try:
        events = store.recent_events(limit=500)
    except Exception:  # noqa: BLE001
        events = []

    # Bucket scheduler events by subject (kind). We read the subject
    # out of the activity-summary JSON emitted by InlineSchedulerBackend
    # when available; fall back to a regex on the message text so tests
    # and older rows still group.
    import json as _json
    import re as _re
    bucket: dict[str, list[str]] = {}
    for e in events:
        if getattr(e, "session_name", "") != "scheduler":
            continue
        if getattr(e, "event_type", "") != "ran":
            continue
        message = getattr(e, "message", "") or ""
        subject: str | None = None
        try:
            payload = _json.loads(message)
            if isinstance(payload, dict):
                subject = payload.get("subject") or payload.get("kind")
        except (ValueError, TypeError):
            pass
        if not subject:
            match = _re.search(r"Ran scheduled job ([A-Za-z0-9_.:-]+)", message)
            if match:
                subject = match.group(1)
        if not subject:
            continue
        bucket.setdefault(subject, []).append(getattr(e, "created_at", "") or "")

    if not bucket:
        rows.append(("(no scheduled runs recorded)", "—", "muted"))
        return MetricsSection(key="schedulers", title="Schedulers", rows=rows)

    now = datetime.now(UTC)
    for kind, timestamps in sorted(bucket.items()):
        timestamps = [t for t in timestamps if t]
        if not timestamps:
            continue
        timestamps.sort(reverse=True)
        latest_raw = timestamps[0]
        try:
            latest = datetime.fromisoformat(latest_raw)
            if latest.tzinfo is None:
                from datetime import UTC as _UTC
                latest = latest.replace(tzinfo=_UTC)
            age_s = max(0, int((now - latest).total_seconds()))
        except (TypeError, ValueError):
            age_s = 0
        # Cadence guess from the gap between the two most recent fires.
        cadence_s = None
        if len(timestamps) >= 2:
            try:
                prev = datetime.fromisoformat(timestamps[1])
                if prev.tzinfo is None:
                    from datetime import UTC as _UTC
                    prev = prev.replace(tzinfo=_UTC)
                cadence_s = max(0, int((latest - prev).total_seconds()))
            except (TypeError, ValueError):
                cadence_s = None
        if age_s < 60:
            age_label = "just now"
        elif age_s < 3600:
            age_label = f"{age_s // 60}m ago"
        elif age_s < 86400:
            age_label = f"{age_s // 3600}h ago"
        else:
            age_label = f"{age_s // 86400}d ago"
        # Stale if 2× cadence — or >2h idle when cadence is unknown.
        threshold = cadence_s * 2 if cadence_s else 2 * 3600
        tone = "alert" if age_s > threshold else "ok"
        rows.append((kind, age_label, tone))
    return MetricsSection(key="schedulers", title="Schedulers", rows=rows)


def _gather_metrics_snapshot(config) -> MetricsSnapshot:
    """Build a :class:`MetricsSnapshot` from the live system state.

    Best-effort everywhere — one subsystem failure leaves the other
    sections intact. Safe to call on the UI thread for small configs
    (< 50 projects); callers that render on an interval should hop to a
    background thread (the ``PollyMetricsApp`` screen does).
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    captured_at = now.isoformat()

    # Fleet — workers + task counts across every project.
    try:
        roster_rows = _gather_worker_roster(config)
    except Exception:  # noqa: BLE001
        roster_rows = []
    total_counts: dict[str, int] = {}
    for project_key, project in getattr(config, "projects", {}).items():
        try:
            _partitioned, counts = _dashboard_project_tasks(
                project_key, project.path,
            )
        except Exception:  # noqa: BLE001
            counts = {}
        for status, n in counts.items():
            total_counts[status] = total_counts.get(status, 0) + int(n)
    try:
        inbox_breakdown = _inbox_breakdown(config)
    except Exception:  # noqa: BLE001
        inbox_breakdown = {"unread": 0, "plan_review": 0, "blocking_question": 0}
    fleet = _fleet_section(config, roster_rows, total_counts, inbox_breakdown)

    # Resources.
    try:
        resources = _resource_section(config)
    except Exception:  # noqa: BLE001
        resources = MetricsSection(
            key="resources", title="Resources",
            rows=[("error", "resources unavailable", "alert")],
        )

    # Throughput + failures share a single 24h-event read.
    store = None
    try:
        state_db = getattr(getattr(config, "project", None), "state_db", None)
        if state_db is not None:
            from pollypm.storage.state import StateStore
            store = StateStore(Path(state_db), readonly=True)
    except Exception:  # noqa: BLE001
        store = None

    if store is None:
        throughput = MetricsSection(
            key="throughput", title="Throughput (24h)",
            rows=[("error", "state store unavailable", "alert")],
        )
        failures = MetricsSection(
            key="failures", title="Failures (24h)", rows=[],
        )
        schedulers = MetricsSection(
            key="schedulers", title="Schedulers", rows=[],
        )
    else:
        try:
            day_events = _metrics_24h_events(store, now=now)
        except Exception:  # noqa: BLE001
            day_events = []
        try:
            throughput = _throughput_section(day_events)
        except Exception:  # noqa: BLE001
            throughput = MetricsSection(
                key="throughput", title="Throughput (24h)", rows=[],
            )
        try:
            failures = _failure_section(day_events)
        except Exception:  # noqa: BLE001
            failures = MetricsSection(
                key="failures", title="Failures (24h)", rows=[],
            )
        try:
            schedulers = _scheduler_section(store)
        except Exception:  # noqa: BLE001
            schedulers = MetricsSection(
                key="schedulers", title="Schedulers", rows=[],
            )
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass

    return MetricsSnapshot(
        captured_at=captured_at,
        fleet=fleet,
        resources=resources,
        throughput=throughput,
        failures=failures,
        schedulers=schedulers,
    )


def _register_metrics_rail_item(registry, router) -> None:
    """Add the ``top.Metrics`` rail row if not already registered.

    Kept next to the worker-roster registration so both observability
    rows sit at the top of the rail. Safe to call repeatedly — the
    registry dedupes on ``(plugin_name, section, label)``.
    """
    try:
        from pollypm.plugin_api.v1 import RailItemRegistration, PanelSpec
    except Exception:  # noqa: BLE001
        return

    def _state(_ctx) -> str:
        return "watch"

    def _handler(ctx):
        try:
            router.route_selected("metrics")
        except Exception:  # noqa: BLE001
            pass
        return PanelSpec(widget=None, focus_hint="metrics")

    reg = RailItemRegistration(
        plugin_name="cockpit_metrics",
        section="top",
        index=28,  # after Workers (25), before Projects (30+)
        label="Metrics",
        handler=_handler,
        key="metrics",
        state_provider=_state,
    )
    try:
        registry.add(reg)
    except Exception:  # noqa: BLE001
        pass


def _render_metrics_panel(config_path: Path) -> str:
    """Fallback text rendering of the metrics snapshot — used by
    ``build_cockpit_detail`` when the right pane hasn't launched the
    Textual app yet. Mirrors the worker-roster fallback.
    """
    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        return f"Metrics\n\nError loading config: {exc}"
    try:
        snap = _gather_metrics_snapshot(config)
    except Exception as exc:  # noqa: BLE001
        return f"Metrics\n\nError gathering metrics: {exc}"
    lines: list[str] = ["Metrics", ""]
    for section in snap.sections():
        lines.append(section.title)
        if not section.rows:
            lines.append("  (no data)")
            lines.append("")
            continue
        for label, value, _tone in section.rows:
            lines.append(f"  {label}: {value}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command palette (``:``) — see issue brief 2026-04-17.
#
# A thin data layer that the Textual ``CommandPaletteModal`` in
# :mod:`pollypm.cockpit_ui` renders. Keeping the registry out of the UI
# layer lets tests exercise the command list (filtering, fuzzy search,
# per-project commands) without having to spin up a full Textual Pilot.
# Every entry is a plain dataclass — the dispatch happens via a ``tag``
# string the host App interprets. This avoids coupling the registry to
# any particular App class.
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PaletteCommand:
    """A single row in the ``:`` command palette.

    ``tag`` is a dotted string the host :class:`App` interprets in
    :meth:`dispatch_palette_command`. Commands are kept as inert data
    here so the registry can be built+filtered in tests without
    requiring a running Textual app.
    """

    title: str
    subtitle: str
    category: str
    keybind: str | None
    tag: str

    def haystack(self) -> str:
        """Lowercased search string covering title + subtitle + category."""
        return f"{self.title} {self.subtitle} {self.category}".lower()


def _fuzzy_subsequence_score(needle: str, haystack: str) -> int | None:
    """Return a match score if ``needle`` is a subsequence of ``haystack``.

    Lower score is a tighter match. Adjacent characters score better than
    scattered ones — this mirrors VS Code / Raycast's "close letters
    win" feel without any real dependency on a fuzzy library. Returns
    ``None`` when ``needle`` isn't a subsequence at all.
    """
    if not needle:
        return 0
    needle = needle.lower()
    haystack = haystack.lower()
    # Substring match always wins — the earliest exact substring gets
    # the best (lowest) score.
    idx = haystack.find(needle)
    if idx != -1:
        return idx  # closer to start = better
    # Fall back to subsequence: walk both strings in lockstep.
    score = 0
    last_pos = -1
    h_i = 0
    for ch in needle:
        while h_i < len(haystack) and haystack[h_i] != ch:
            h_i += 1
        if h_i >= len(haystack):
            return None
        gap = h_i - last_pos - 1
        score += 1000 + gap  # subsequence baseline > any substring score
        last_pos = h_i
        h_i += 1
    return score


def filter_palette_commands(
    commands: list[PaletteCommand], query: str,
) -> list[PaletteCommand]:
    """Return matching commands ordered by fuzzy score, then title."""
    query = (query or "").strip()
    if not query:
        return list(commands)
    scored: list[tuple[int, str, PaletteCommand]] = []
    for cmd in commands:
        score = _fuzzy_subsequence_score(query, cmd.haystack())
        if score is None:
            continue
        scored.append((score, cmd.title.lower(), cmd))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [cmd for _score, _title, cmd in scored]


def build_palette_commands(
    config_path: Path,
    *,
    current_project: str | None = None,
) -> list[PaletteCommand]:
    """Return the default global command set for the cockpit palette.

    ``current_project`` is the project key the caller is currently
    viewing (if any) — used to prefill project-scoped commands like
    "Create task". Projects are read from the loaded config; if the
    config can't be loaded we still return the static commands so the
    palette never shows an empty list.
    """
    commands: list[PaletteCommand] = []

    # Navigation — the six top-level cockpit views.
    commands.append(PaletteCommand(
        title="Go to Inbox",
        subtitle="Open the cockpit inbox",
        category="Navigation",
        keybind=None,
        tag="nav.inbox",
    ))
    commands.append(PaletteCommand(
        title="Go to Workers",
        subtitle="Open the worker roster",
        category="Navigation",
        keybind=None,
        tag="nav.workers",
    ))
    commands.append(PaletteCommand(
        title="Go to Activity",
        subtitle="Open the activity feed",
        category="Navigation",
        keybind=None,
        tag="nav.activity",
    ))
    commands.append(PaletteCommand(
        title="Go to Metrics",
        subtitle="Open the observability metrics screen",
        category="Navigation",
        keybind=None,
        tag="nav.metrics",
    ))
    commands.append(PaletteCommand(
        title="Go to Settings",
        subtitle="Open cockpit settings",
        category="Navigation",
        keybind="s",
        tag="nav.settings",
    ))
    commands.append(PaletteCommand(
        title="Go to Dashboard",
        subtitle="Jump to the main cockpit rail",
        category="Navigation",
        keybind=None,
        tag="nav.dashboard",
    ))

    # Per-project navigation + task commands.
    try:
        config = load_config(config_path)
        projects = getattr(config, "projects", {}) or {}
    except Exception:  # noqa: BLE001
        projects = {}
    for project_key, project in projects.items():
        name = getattr(project, "name", None) or project_key
        commands.append(PaletteCommand(
            title=f"Go to project: {name}",
            subtitle=f"Open the {name} dashboard",
            category="Navigation",
            keybind=None,
            tag=f"nav.project:{project_key}",
        ))
        commands.append(PaletteCommand(
            title=f"Create task in {name}",
            subtitle="Draft a new task in this project",
            category="Task",
            keybind=None,
            tag=f"task.create:{project_key}",
        ))
        commands.append(PaletteCommand(
            title=f"Queue next task in {name}",
            subtitle=f"Run pm task next --project {project_key}",
            category="Task",
            keybind=None,
            tag=f"task.queue_next:{project_key}",
        ))

    # Inbox commands.
    commands.append(PaletteCommand(
        title="Run pm notify",
        subtitle="Send a notification into the inbox",
        category="Inbox",
        keybind=None,
        tag="inbox.notify",
    ))
    commands.append(PaletteCommand(
        title="Archive all read inbox items",
        subtitle="Mark every already-read message done",
        category="Inbox",
        keybind=None,
        tag="inbox.archive_read",
    ))

    # Session / app-level.
    commands.append(PaletteCommand(
        title="Refresh data",
        subtitle="Re-read state and repaint the current screen",
        category="Session",
        keybind="r",
        tag="session.refresh",
    ))
    commands.append(PaletteCommand(
        title="Restart cockpit",
        subtitle="Exit and reload the cockpit app",
        category="Session",
        keybind=None,
        tag="session.restart",
    ))
    commands.append(PaletteCommand(
        title="Show keyboard shortcuts",
        subtitle="Display the current screen's keybindings",
        category="Session",
        keybind="?",
        tag="session.shortcuts",
    ))

    # System.
    commands.append(PaletteCommand(
        title="Run pm doctor",
        subtitle="Stream doctor checks into the palette",
        category="System",
        keybind=None,
        tag="system.doctor",
    ))
    commands.append(PaletteCommand(
        title="Open pollypm.toml in editor",
        subtitle=str(config_path),
        category="System",
        keybind=None,
        tag="system.edit_config",
    ))

    # Let the caller prioritise current-project commands visually. We
    # keep the list stable (no re-ordering mid-render); a current-project
    # hint just means the "Create task in <current>" entry sits above
    # its siblings.
    if current_project is not None:
        preferred: list[PaletteCommand] = []
        rest: list[PaletteCommand] = []
        for cmd in commands:
            if cmd.tag.endswith(f":{current_project}"):
                preferred.append(cmd)
            else:
                rest.append(cmd)
        commands = preferred + rest

    return commands
