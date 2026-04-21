from __future__ import annotations

from pollypm.cockpit_task_priority import (
    priority_glyph,
    priority_label,
    priority_rank,
    priority_value,
)
from pollypm.work.models import Priority


class _Task:
    def __init__(self, priority):
        self.priority = priority


def test_priority_helpers_accept_strings_enums_and_tasks() -> None:
    assert priority_value("critical") == "critical"
    assert priority_value(Priority.HIGH) == "high"
    assert priority_value(_Task(Priority.LOW)) == "low"


def test_priority_helpers_surface_expected_order_and_glyphs() -> None:
    assert priority_rank("critical") < priority_rank("high") < priority_rank("normal") < priority_rank("low")
    assert priority_glyph("critical") == "🔴"
    assert priority_glyph("high") == "🟠"
    assert priority_glyph("normal") == "🟡"
    assert priority_glyph("low") == "🟢"
    assert priority_label("critical") == "🔴 critical"


def test_priority_helpers_degrade_for_unknown_values() -> None:
    assert priority_value("") == "normal"
    assert priority_glyph("urgent") == "⚪"
    assert priority_rank("urgent") == 4
