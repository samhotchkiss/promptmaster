"""Shared task-priority helpers for cockpit task surfaces.

Contract:
- Inputs: a priority string / enum or any task-like object with a
  ``.priority`` field.
- Outputs: stable glyphs, labels, and sort ranks for cockpit task lists.
- Side effects: none.
- Invariants: unknown priorities degrade gracefully to a neutral glyph
  and lowest precedence so the UI never crashes on unexpected data.
"""

from __future__ import annotations

_PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}

_PRIORITY_GLYPHS = {
    "critical": "🔴",
    "high": "🟠",
    "normal": "🟡",
    "low": "🟢",
}


def priority_value(priority_or_task: object) -> str:
    """Normalize a priority enum / task / raw string to a lowercase value."""
    raw = getattr(priority_or_task, "priority", priority_or_task)
    value = getattr(raw, "value", raw)
    if value in (None, ""):
        return "normal"
    return str(value).strip().lower() or "normal"


def priority_rank(priority_or_task: object) -> int:
    """Return the cockpit sort rank for a priority value."""
    return _PRIORITY_ORDER.get(priority_value(priority_or_task), len(_PRIORITY_ORDER))


def priority_glyph(priority_or_task: object) -> str:
    """Return the colored glyph used in cockpit task lists."""
    return _PRIORITY_GLYPHS.get(priority_value(priority_or_task), "⚪")


def priority_label(priority_or_task: object) -> str:
    """Return a human-readable priority label with the cockpit glyph."""
    value = priority_value(priority_or_task)
    return f"{priority_glyph(value)} {value}"
