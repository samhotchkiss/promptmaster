"""Cockpit widget + app for the Live Activity Feed (lf03).

Two surfaces:

* :class:`ActivityFeedPanel` — a Textual widget that renders a
  reverse-chronological feed of :class:`FeedEntry` records. Reused by
  lf04 (filter UI + detail view).
* :class:`ActivityFeedApp` — a Textual ``App`` that mounts the panel,
  polls the state-epoch counter, and re-renders when new events land.
  Launched by ``pm cockpit-pane activity``.

A plain-text renderer (:func:`render_activity_feed_text`) is exported
for the cockpit's ``_build_cockpit_detail_dispatch`` so environments
without Textual mounted (e.g. the ``PollyCockpitPaneApp`` fallback)
still show the feed.

The widget uses the state-epoch monotonic counter (see
:mod:`pollypm.state_epoch`) so reads are cheap — the expensive
projection only runs when something changed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from pollypm.plugins_builtin.activity_feed.handlers.event_projector import (
    EventProjector,
    FeedEntry,
)
from pollypm.plugins_builtin.activity_feed.plugin import build_projector

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure rendering helpers — testable without Textual.
# ---------------------------------------------------------------------------


_SEVERITY_COLOURS = {
    "critical": "red",
    "recommendation": "yellow",
    "routine": "white",
}


def format_relative_time(
    timestamp: str, *, now: datetime | None = None,
) -> str:
    """Return a relative-time label (``"3m ago"``, ``"2h ago"``).

    ``timestamp`` is an ISO-8601 string (the projector always produces
    these). Unparseable values fall back to the raw string. ``now`` is
    injectable for deterministic tests.
    """
    if not timestamp:
        return "—"
    try:
        when = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    anchor = now or datetime.now(UTC)
    delta = anchor - when
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def format_entry_row(entry: FeedEntry, *, now: datetime | None = None) -> str:
    """Render one FeedEntry as a single plain-text row.

    Layout:: ``[rel] [project] [actor] verb summary``. Severity is not
    encoded here — the Textual renderer applies colour; the plain-text
    renderer prefixes a ``!`` on critical entries so `pm activity` can
    stay unstyled but still draw attention.
    """
    rel = format_relative_time(entry.timestamp, now=now)
    project = entry.project or "-"
    actor = entry.actor or "system"
    verb = entry.verb or entry.kind
    prefix = "!" if entry.severity == "critical" else " "
    return f"{prefix} {rel:>8}  [{project}]  [{actor}]  {verb}: {entry.summary}"


def render_entries_as_text(entries: Iterable[FeedEntry]) -> str:
    """Render a list of FeedEntry rows as multi-line plain text.

    Empty input renders a friendly placeholder so the cockpit panel
    doesn't look broken on a brand-new install.
    """
    rows = [format_entry_row(e) for e in entries]
    if not rows:
        return (
            "No activity yet.\n\n"
            "Events accumulate as sessions start, tasks transition, "
            "and heartbeats fire. Check back after the next sweep."
        )
    return "\n".join(rows)


def render_activity_feed_text(config: Any, *, limit: int = 50) -> str:
    """Render the feed as plain text for the cockpit's static pane path.

    Picks up the projector from :func:`build_projector` — missing config
    or missing state DB yields the same friendly placeholder as an
    empty feed.
    """
    header = "Activity Feed"
    projector = build_projector(config)
    if projector is None:
        return f"{header}\n\nNo state store configured — nothing to show yet."
    try:
        entries = projector.project(limit=limit)
    except Exception:  # noqa: BLE001
        logger.exception("activity_feed: projection failed for text render")
        return f"{header}\n\nFailed to read activity events."
    return f"{header}\n\n{render_entries_as_text(entries)}"


# ---------------------------------------------------------------------------
# Textual widget + App — imported lazily so the plugin loads in
# environments without Textual (unit tests, headless CI).
# ---------------------------------------------------------------------------


def _severity_style(severity: str) -> str:
    colour = _SEVERITY_COLOURS.get(severity, "white")
    if severity == "critical":
        return f"bold {colour}"
    return colour


class _PanelState:
    """Tracks state between renders so new entries can be highlighted.

    Kept as a plain object (not a dataclass) so the widget can live-
    patch ``last_seen_id`` without freezing semantics.
    """

    __slots__ = ("last_seen_id", "last_epoch", "entries")

    def __init__(self) -> None:
        self.last_seen_id: int | None = None
        self.last_epoch: float = 0.0
        self.entries: list[FeedEntry] = []

    def update_with(self, entries: list[FeedEntry]) -> list[FeedEntry]:
        """Merge a fresh projection in at the top; return the ids of
        rows that are new since the last render (used to highlight).
        """
        existing_ids = {e.id for e in self.entries}
        new_rows = [e for e in entries if e.id not in existing_ids]
        # Keep the window bounded so the widget stays cheap. The spec
        # paginates at 50; we keep 100 so scrolling a bit doesn't drop
        # rows that just arrived.
        merged = (new_rows + self.entries)[:100]
        self.entries = merged
        if entries:
            # Track the numeric id so badge-provider math can detect
            # "new since last view" without re-querying.
            numeric_ids: list[int] = []
            for e in entries:
                if e.id.startswith("evt:"):
                    try:
                        numeric_ids.append(int(e.id.split(":", 1)[1]))
                    except ValueError:
                        continue
            if numeric_ids:
                self.last_seen_id = max(numeric_ids + ([self.last_seen_id] if self.last_seen_id else []))
        return new_rows


def _try_import_textual():  # pragma: no cover - import guard
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.widget import Widget
        from textual.widgets import Static

        return App, ComposeResult, Binding, Widget, Static
    except Exception as exc:
        raise RuntimeError(
            "Textual is not available — ActivityFeedPanel / ActivityFeedApp "
            "require the cockpit's textual dependency. "
            f"Underlying error: {exc}"
        )


def _build_widget_classes():
    """Construct the Textual widget + app lazily.

    Keeps plugin import cheap; only materialises Textual dependencies
    when someone actually launches the cockpit pane.
    """
    App, ComposeResult, Binding, Widget, Static = _try_import_textual()

    class ActivityFeedPanel(Widget):
        """Reverse-chronological feed of FeedEntry rows.

        Subclasses ``Static`` indirectly through ``Widget`` composition
        so the render path stays simple. ``render()`` returns Rich-style
        markup; the host app triggers ``refresh()`` on new data.
        """

        DEFAULT_CSS = """
        ActivityFeedPanel {
            padding: 1 2;
            background: #10161b;
            color: #eef2f4;
        }
        """

        def __init__(self, projector: EventProjector | None = None, *, limit: int = 50):
            super().__init__()
            self._projector = projector
            self._limit = limit
            self._state = _PanelState()
            self._body = Static("", id="feed-body")
            self._last_rendered_ids: set[str] = set()

        def compose(self) -> ComposeResult:  # pragma: no cover - Textual harness
            yield self._body

        def refresh_feed(self) -> list[FeedEntry]:
            """Fetch the latest projection and update the body.

            Returns the list of newly-arrived entries (for caller-side
            badge math / notifications).
            """
            if self._projector is None:
                self._body.update(
                    "No projector wired — activity feed is inert.\n"
                    "Ensure `activity_feed` is loaded and the state DB exists."
                )
                return []
            try:
                fresh = self._projector.project(limit=self._limit)
            except Exception:  # noqa: BLE001  # pragma: no cover
                logger.exception("activity_feed: widget refresh failed")
                return []
            newly = self._state.update_with(fresh)
            self._body.update(self._render_markup(self._state.entries, newly))
            self._last_rendered_ids = {e.id for e in self._state.entries}
            return newly

        def _render_markup(
            self, entries: list[FeedEntry], new_ids: list[FeedEntry],
        ) -> str:
            new_id_set = {e.id for e in new_ids}
            now = datetime.now(UTC)
            lines: list[str] = []
            if not entries:
                return (
                    "[dim italic]No activity yet.[/]\n\n"
                    "[dim]Events accumulate as sessions start, tasks "
                    "transition, and heartbeats fire.[/]"
                )
            for entry in entries:
                rel = format_relative_time(entry.timestamp, now=now)
                style = _severity_style(entry.severity)
                project = entry.project or "-"
                actor = entry.actor or "system"
                verb = entry.verb or entry.kind
                marker = "[bold green]\u25cf[/] " if entry.id in new_id_set else "  "
                # Escape Rich-unsafe brackets in user content by passing
                # through the tag-safe escaping helper.
                summary = _rich_escape(entry.summary)
                verb_s = _rich_escape(verb)
                proj_s = _rich_escape(project)
                actor_s = _rich_escape(actor)
                lines.append(
                    f"{marker}[dim]{rel:>8}[/]  "
                    f"[cyan][{proj_s}][/]  [magenta][{actor_s}][/]  "
                    f"[{style}]{verb_s}[/]: {summary}"
                )
            return "\n".join(lines)

    class ActivityFeedApp(App):
        TITLE = "PollyPM"
        SUB_TITLE = "Activity"
        CSS = """
        Screen { background: #10161b; color: #eef2f4; padding: 1; }
        #feed-body { padding: 1 2; }
        """
        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
        ]

        def __init__(self, config_path: Path, *, limit: int = 50) -> None:
            super().__init__()
            self.config_path = config_path
            self._panel: ActivityFeedPanel | None = None
            self._limit = limit
            self._last_epoch = 0.0

        def compose(self) -> ComposeResult:  # pragma: no cover - Textual harness
            projector = self._load_projector()
            self._panel = ActivityFeedPanel(projector, limit=self._limit)
            yield self._panel

        def _load_projector(self) -> EventProjector | None:
            try:
                from pollypm.config import load_config

                config = load_config(self.config_path)
                return build_projector(config)
            except Exception:  # noqa: BLE001  # pragma: no cover
                logger.exception("activity_feed: could not load projector")
                return None

        def on_mount(self) -> None:  # pragma: no cover - Textual harness
            if self._panel is not None:
                self._panel.refresh_feed()
            # 2-second tick keeps live updates responsive without
            # hammering the DB (the projector only does real work when
            # the state-epoch has moved).
            self.set_interval(2.0, self._tick)

        def _tick(self) -> None:  # pragma: no cover - Textual harness
            try:
                from pollypm.state_epoch import mtime
            except Exception:  # noqa: BLE001
                mtime = lambda: 0.0  # type: ignore[assignment]
            current = mtime()
            if current and current == self._last_epoch:
                return
            self._last_epoch = current
            if self._panel is not None:
                self._panel.refresh_feed()

        def action_refresh(self) -> None:  # pragma: no cover - Textual harness
            if self._panel is not None:
                self._panel.refresh_feed()

    return ActivityFeedPanel, ActivityFeedApp


def _rich_escape(text: str) -> str:
    """Escape ``[`` / ``]`` in user content so Rich markup doesn't mis-
    parse summaries that contain tags-looking substrings.
    """
    if not text:
        return ""
    return text.replace("[", "\\[")


def __getattr__(name: str):
    """Lazy-load the Textual classes so the module imports cheaply.

    Tests that don't touch the UI (and CLI paths that render plain
    text) never pay the Textual import cost. The widget / app classes
    are built on first access and cached on the module.
    """
    if name in {"ActivityFeedPanel", "ActivityFeedApp"}:
        panel_cls, app_cls = _build_widget_classes()
        globals()["ActivityFeedPanel"] = panel_cls
        globals()["ActivityFeedApp"] = app_cls
        return globals()[name]
    raise AttributeError(name)


# ---------------------------------------------------------------------------
# Badge + rail helpers — consumed by plugin.py at rail-registration time.
# ---------------------------------------------------------------------------


def new_event_count(
    projector: EventProjector | None, last_seen_id: int | None,
) -> int:
    """How many events landed since ``last_seen_id`` was recorded.

    Returned as the badge value for the rail entry. ``None`` means "no
    cursor" — counts all events below the limit (capped so the badge
    doesn't read ``999+`` for a fresh install).
    """
    if projector is None:
        return 0
    try:
        entries = projector.project(since_id=last_seen_id, limit=50)
    except Exception:  # noqa: BLE001
        logger.exception("activity_feed: badge probe failed")
        return 0
    return len(entries)


__all__ = [
    "ActivityFeedApp",
    "ActivityFeedPanel",
    "format_entry_row",
    "format_relative_time",
    "new_event_count",
    "render_activity_feed_text",
    "render_entries_as_text",
]
