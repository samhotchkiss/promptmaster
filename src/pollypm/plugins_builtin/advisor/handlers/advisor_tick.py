"""Half-hourly advisor tick.

Registered as ``advisor.tick`` on the job-handler registry and fired by
the roster on the configured cadence (default ``@every 30m``). The
handler itself is cheap: it walks tracked projects, checks the enabled
flag + pause marker, calls ``detect_changes`` (stubbed in ad01, real in
ad02), and for projects with meaningful activity enqueues an
``advisor_review`` work task — unless an earlier advisor task is still
in flight, in which case the project is skipped to prevent pile-up.

Out of scope for ad01:

* Real change detection (ad02 replaces the stub in ``detect_changes.py``).
* Context packing + session launch (ad03).
* History log write (ad04).
* Inbox emission on emit (ad05).

The tick handler is intentionally non-raising — any per-project failure
is logged and the next project still gets its turn.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pollypm.plugins_builtin.advisor.settings import (
    AdvisorSettings,
    load_advisor_settings,
)
from pollypm.plugins_builtin.advisor.state import (
    AdvisorState,
    ProjectAdvisorState,
    is_paused,
    iso_utc_now,
    load_state,
    save_state,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stubs for downstream issues. ad02 replaces detect_changes; ad03 replaces
# run_advisor_session. Wired through a module-level callable so tests can
# monkeypatch them independently.
# ---------------------------------------------------------------------------


def detect_changes(
    project_path: Path,
    since: datetime | None,
    *,
    project_key: str | None = None,
    work_service: Any | None = None,
) -> bool:
    """Return True when the project has a commit or task transition since ``since``.

    Thin adapter over :func:`pollypm.plugins_builtin.advisor.handlers.
    detect_changes.detect_changes` — unit tests of the tick handler
    monkeypatch this symbol to force particular outcomes; ad03's
    context-packing imports the full :class:`ChangeReport` directly
    from ``detect_changes.py``.
    """
    from pollypm.plugins_builtin.advisor.handlers.detect_changes import (
        detect_changes as _detect,
    )
    report = _detect(
        project_path,
        since,
        project_key=project_key,
        work_service=work_service,
    )
    return report.has_changes


def enqueue_advisor_review(
    *,
    project_key: str,
    project_path: Path,
    config: Any,
) -> dict[str, Any]:
    """Create an advisor_review work-service task for a project.

    Stubbed in ad01 — returns ``{"enqueued": False, "reason": "stub"}``.
    ad03 wires the real work-service ``create()`` call that spawns the
    short-lived advisor session.

    Kept as a module-level callable so unit tests can monkeypatch the
    enqueue path without touching the work service.
    """
    return {"enqueued": False, "reason": "stub", "project": project_key}


def has_in_progress_advisor_task(
    *,
    project_key: str,
    work_service: Any,
) -> bool:
    """Return True if any advisor task is currently active for the project.

    An advisor task is "active" when its ``work_status`` is one of
    ``in_progress`` / ``queued`` / ``review`` *and* its labels list
    contains ``"advisor"``. This is the pile-up throttle: if a session
    is still running from a previous tick we skip instead of stacking
    work.

    Non-raising. A work-service lookup failure is logged and treated
    as "no advisor task active" so the tick can proceed.
    """
    if work_service is None:
        return False
    try:
        # Ask for everything non-terminal on the project, then filter
        # by the advisor label client-side — the service protocol
        # doesn't expose label filtering, and projects have few tasks
        # at any given time so the cost is negligible.
        candidates = []
        for status in ("in_progress", "queued", "review"):
            try:
                tasks = work_service.list_tasks(
                    project=project_key,
                    work_status=status,
                )
            except Exception:  # noqa: BLE001
                tasks = []
            for t in tasks:
                labels = getattr(t, "labels", []) or []
                if "advisor" in labels:
                    candidates.append(t)
        return bool(candidates)
    except Exception as exc:  # noqa: BLE001
        logger.debug("advisor: throttle lookup failed for %s: %s", project_key, exc)
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _iter_tracked_projects(config: Any) -> list[tuple[str, Path]]:
    """Return ``(project_key, project_path)`` for each tracked project.

    Tracked means one of:

    * ``config.projects[key].tracked == True``, OR
    * The ambient project (``config.project.root_dir``), if present —
      single-project installs without explicit ``[projects.*]`` entries
      still get advisor coverage.

    Entries whose on-disk path doesn't exist are filtered out (a stale
    config should not crash the tick).
    """
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    projects = getattr(config, "projects", {}) or {}
    for key, known in projects.items():
        if not getattr(known, "tracked", False):
            continue
        path = getattr(known, "path", None)
        if path is None or not Path(path).exists():
            continue
        resolved = Path(path).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((key, Path(path)))

    ambient = getattr(getattr(config, "project", None), "root_dir", None)
    if ambient is not None:
        ambient_path = Path(ambient)
        if ambient_path.exists():
            resolved = ambient_path.resolve()
            if resolved not in seen:
                key = getattr(getattr(config, "project", None), "name", None) or "pollypm"
                out.append((str(key), ambient_path))

    return out


# ---------------------------------------------------------------------------
# Per-project decision
# ---------------------------------------------------------------------------


def _should_review(
    *,
    project_key: str,
    project_path: Path,
    project_state: ProjectAdvisorState,
    settings: AdvisorSettings,
    now_utc: datetime,
    work_service: Any,
) -> tuple[bool, str]:
    """Return (review?, reason). Reason is a short machine-parseable tag."""
    if not settings.enabled:
        return False, "plugin-disabled"
    if not project_state.enabled:
        return False, "project-disabled"
    if is_paused(project_state, now_utc=now_utc):
        return False, "paused"

    since = _parse_iso(project_state.last_run)

    try:
        # Call via module attribute so tests can monkeypatch detect_changes.
        from pollypm.plugins_builtin.advisor.handlers import advisor_tick as _self
        # detect_changes takes project_key + work_service kwargs in the ad02
        # signature; older tests that monkeypatch it with a simpler
        # positional-only signature still work because we route through
        # the adapter below.
        try:
            changed = bool(
                _self.detect_changes(
                    project_path, since,
                    project_key=project_key,
                    work_service=work_service,
                )
            )
        except TypeError:
            # Backwards-compat: some tests patch a two-arg stub.
            changed = bool(_self.detect_changes(project_path, since))
    except Exception as exc:  # noqa: BLE001
        logger.debug("advisor: detect_changes failed for %s: %s", project_key, exc)
        return False, "detect-error"

    if not changed:
        return False, "no-changes"

    if has_in_progress_advisor_task(project_key=project_key, work_service=work_service):
        return False, "in-progress"

    return True, "ok"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


def advisor_tick_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Half-hourly advisor tick.

    Payload keys (all optional):

    * ``config_path`` — explicit ``pollypm.toml``.
    * ``now_utc`` — ISO-8601 UTC override (tests only).
    * ``work_service`` — explicit work-service handle (tests only).
      Production lookup uses the default factory.
    """
    from pollypm.config import DEFAULT_CONFIG_PATH, load_config, resolve_config_path

    config_path_override = payload.get("config_path") if isinstance(payload, dict) else None
    config_path = (
        Path(config_path_override) if config_path_override
        else resolve_config_path(DEFAULT_CONFIG_PATH)
    )
    if not config_path.exists():
        return {
            "fired": False,
            "reason": "no-config",
            "config_path": str(config_path),
        }

    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("advisor: failed to load config %s: %s", config_path, exc)
        return {"fired": False, "reason": "config-error", "error": str(exc)}

    settings = load_advisor_settings(config_path)

    if not settings.enabled:
        return {"fired": False, "reason": "plugin-disabled"}

    override_now = payload.get("now_utc") if isinstance(payload, dict) else None
    if isinstance(override_now, datetime):
        now_utc = override_now if override_now.tzinfo else override_now.replace(tzinfo=UTC)
    elif isinstance(override_now, str) and override_now:
        parsed = _parse_iso(override_now)
        now_utc = parsed or datetime.now(UTC)
    else:
        now_utc = datetime.now(UTC)

    base_dir: Path = Path(config.project.base_dir)

    # Explicit override so tests can inject a fake work service. Production
    # callers hand in ``None`` and the enqueue stub silently no-ops — the
    # real enqueue path lands in ad03.
    work_service = payload.get("work_service") if isinstance(payload, dict) else None

    state = load_state(base_dir)

    # Drop the per-tick change-detection cache so each tick starts fresh.
    # Cache is in-process only; it exists to deduplicate the work when
    # assess.py (ad03) re-reads a ChangeReport inside the same tick.
    try:
        from pollypm.plugins_builtin.advisor.handlers.detect_changes import clear_cache
        clear_cache()
    except Exception:  # noqa: BLE001
        pass

    projects = _iter_tracked_projects(config)
    results: list[dict[str, Any]] = []
    enqueued_projects: list[str] = []

    for project_key, project_path in projects:
        proj_state = state.get(project_key)
        proj_state.last_tick_at = now_utc.isoformat()

        review, reason = _should_review(
            project_key=project_key,
            project_path=project_path,
            project_state=proj_state,
            settings=settings,
            now_utc=now_utc,
            work_service=work_service,
        )

        entry: dict[str, Any] = {
            "project": project_key,
            "scheduled": False,
            "reason": reason,
        }

        if review:
            try:
                from pollypm.plugins_builtin.advisor.handlers import advisor_tick as _self
                outcome = _self.enqueue_advisor_review(
                    project_key=project_key,
                    project_path=project_path,
                    config=config,
                )
                enqueued = bool(outcome.get("enqueued")) if isinstance(outcome, dict) else False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "advisor: enqueue failed for %s: %s", project_key, exc,
                )
                enqueued = False
                outcome = {"enqueued": False, "error": str(exc)}
            entry["scheduled"] = enqueued
            entry["reason"] = "ok" if enqueued else entry["reason"]
            entry["enqueue"] = outcome
            if enqueued:
                enqueued_projects.append(project_key)

        results.append(entry)

    # Persist the state (last_tick_at, plus anything mutated per project).
    save_state(base_dir, state)

    return {
        "fired": True,
        "reason": "ok",
        "now_utc": now_utc.isoformat(),
        "tracked": [p for p, _ in projects],
        "enqueued": enqueued_projects,
        "results": results,
    }


# ---------------------------------------------------------------------------
# last_run bookkeeping — ad02 calls this after a session completes.
# ---------------------------------------------------------------------------


def mark_last_run(base_dir: Path, project_key: str, *, at: str | None = None) -> None:
    """Stamp ``last_run`` on the project after a completed advisor session.

    Called by the assess/history path — NOT by the tick handler — so a
    crashed mid-run session doesn't swallow the delta it was about to
    review. The next tick will re-detect the same signals and try again.
    """
    state = load_state(base_dir)
    proj = state.get(project_key)
    proj.last_run = at or iso_utc_now()
    save_state(base_dir, state)
