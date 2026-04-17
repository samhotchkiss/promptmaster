"""Persistent state for the advisor plugin.

State lives at ``<project.base_dir>/advisor-state.json`` — one file,
keyed per tracked project, tracking:

* ``last_run`` (ISO-8601 UTC) — timestamp of the most recent *completed*
  advisor session for that project. Used as the `since` for change
  detection (ad02). Updated only after a session completes so a crashed
  mid-run session doesn't swallow the signals it was about to review.
* ``pause_until`` (ISO-8601 UTC) — optional; if set and in the future,
  the tick skips this project. Set by `pm advisor pause` (ad06).
* ``enabled`` (bool, default True) — per-project opt-out.
* ``recent_dismissals`` — list of ``{"topic": str, "at": ISO}`` records
  from the user rejecting an advisor insight with
  ``--reason topic_cooldown``. Kept capped at the last 10 per project so
  the prompt-pack stays small.

No rotation. The file stays tiny because there's one key per project
and the dismissals list is capped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pollypm.atomic_io import atomic_write_text


STATE_FILENAME = "advisor-state.json"
MAX_DISMISSALS_PER_PROJECT = 10


@dataclass(slots=True)
class Dismissal:
    """One user dismissal record.

    The advisor persona sees this list in its trajectory context and is
    expected to respect it (honor "not this, not now" on the topic).
    """
    topic: str
    at: str  # ISO-8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "at": self.at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Dismissal":
        return cls(
            topic=str(data.get("topic") or ""),
            at=str(data.get("at") or ""),
        )


@dataclass(slots=True)
class ProjectAdvisorState:
    """Per-project advisor state."""

    enabled: bool = True
    last_run: str = ""          # ISO-8601 UTC
    last_tick_at: str = ""      # ISO-8601 UTC — any tick, fired or skipped
    pause_until: str = ""       # ISO-8601 UTC; empty = not paused
    recent_dismissals: list[Dismissal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_tick_at": self.last_tick_at,
            "pause_until": self.pause_until,
            "recent_dismissals": [d.to_dict() for d in self.recent_dismissals],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectAdvisorState":
        enabled_raw = data.get("enabled", True)
        dismissals_raw = data.get("recent_dismissals", [])
        dismissals: list[Dismissal] = []
        if isinstance(dismissals_raw, list):
            for d in dismissals_raw:
                if isinstance(d, dict):
                    dismissals.append(Dismissal.from_dict(d))
        return cls(
            enabled=bool(enabled_raw) if isinstance(enabled_raw, bool) else True,
            last_run=str(data.get("last_run") or ""),
            last_tick_at=str(data.get("last_tick_at") or ""),
            pause_until=str(data.get("pause_until") or ""),
            recent_dismissals=dismissals,
        )


@dataclass(slots=True)
class AdvisorState:
    """Full advisor state — a mapping of project-key → project state."""

    projects: dict[str, ProjectAdvisorState] = field(default_factory=dict)

    def get(self, project_key: str) -> ProjectAdvisorState:
        """Return the state for ``project_key``, creating it if missing."""
        if project_key not in self.projects:
            self.projects[project_key] = ProjectAdvisorState()
        return self.projects[project_key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "projects": {k: v.to_dict() for k, v in self.projects.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AdvisorState":
        projects_raw = data.get("projects", {}) if isinstance(data, dict) else {}
        projects: dict[str, ProjectAdvisorState] = {}
        if isinstance(projects_raw, dict):
            for key, value in projects_raw.items():
                if isinstance(value, dict):
                    projects[str(key)] = ProjectAdvisorState.from_dict(value)
        return cls(projects=projects)


def state_path(base_dir: Path) -> Path:
    return Path(base_dir) / STATE_FILENAME


def load_state(base_dir: Path) -> AdvisorState:
    """Load advisor state. Missing / corrupt → empty state."""
    path = state_path(base_dir)
    if not path.exists():
        return AdvisorState()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return AdvisorState()
    if not isinstance(raw, dict):
        return AdvisorState()
    return AdvisorState.from_dict(raw)


def save_state(base_dir: Path, state: AdvisorState) -> None:
    """Persist advisor state atomically. Creates parent directory if missing."""
    path = state_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
    )


def iso_utc_now() -> str:
    """Canonical UTC ISO-8601 timestamp used across the advisor plugin."""
    return datetime.now(UTC).isoformat()


def record_dismissal(
    base_dir: Path,
    project_key: str,
    topic: str,
    *,
    at: str | None = None,
) -> None:
    """Append a dismissal, keep list capped at MAX_DISMISSALS_PER_PROJECT."""
    state = load_state(base_dir)
    proj = state.get(project_key)
    proj.recent_dismissals.append(
        Dismissal(topic=topic, at=at or iso_utc_now()),
    )
    # Keep the most recent MAX_DISMISSALS_PER_PROJECT.
    if len(proj.recent_dismissals) > MAX_DISMISSALS_PER_PROJECT:
        proj.recent_dismissals = proj.recent_dismissals[-MAX_DISMISSALS_PER_PROJECT:]
    save_state(base_dir, state)


def is_paused(project_state: ProjectAdvisorState, *, now_utc: datetime | None = None) -> bool:
    """Return True if the project is currently paused (pause_until in future)."""
    if not project_state.pause_until:
        return False
    try:
        until = datetime.fromisoformat(project_state.pause_until)
    except ValueError:
        return False
    current = now_utc or datetime.now(UTC)
    # Normalize to UTC-aware for comparison.
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current < until
