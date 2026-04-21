"""In-flight tasks section (#403)."""

from __future__ import annotations

from pollypm.cockpit_task_priority import priority_glyph, priority_rank
from pollypm.cockpit_sections.base import (
    _DASHBOARD_BULLET,
    _STATUS_ICONS,
    _age_from_dt,
    _dashboard_divider,
    _iso_to_dt,
)


def _in_flight_sort_key(task) -> tuple[int, float, int]:
    updated = _iso_to_dt(getattr(task, "updated_at", None))
    return (
        priority_rank(task),
        -(updated.timestamp() if updated is not None else 0.0),
        int(getattr(task, "task_number", 0) or 0),
    )


def _section_in_flight(in_progress: list) -> list[str]:
    """Tasks currently being worked on."""
    lines = [_dashboard_divider("In flight"), ""]
    if not in_progress:
        lines.extend([f"{_DASHBOARD_BULLET}(none)", ""])
        return lines
    ordered = sorted(in_progress, key=_in_flight_sort_key)
    for t in ordered:
        icon = _STATUS_ICONS.get(t.work_status.value, "\u27f3")
        assignee = f" [{t.assignee}]" if getattr(t, "assignee", None) else ""
        node = (
            f" @ {t.current_node_id}"
            if getattr(t, "current_node_id", None)
            else ""
        )
        age = _age_from_dt(_iso_to_dt(getattr(t, "updated_at", None)))
        age_part = f" \u00b7 {age}" if age else ""
        lines.append(
            f"{_DASHBOARD_BULLET}{icon} {priority_glyph(t)} #{t.task_number} {t.title}"
            f"{assignee}{node}{age_part}"
        )
    lines.append("")
    return lines
