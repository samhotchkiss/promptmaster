"""Tests for cockpit performance budgets (#891)."""

from __future__ import annotations

import time

import pytest

from pollypm.cockpit_perf import (
    COCKPIT_PERF_BUDGETS,
    HotPath,
    PerfBudget,
    PerfMeasurement,
    all_hot_paths_have_budgets,
    assert_within_budget,
    budget_for,
    clear_recorded_measurements,
    measurements_for,
    perf_budget_audit,
    record_render_timing,
)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_every_hot_path_has_a_budget() -> None:
    """The release gate consults this — a new hot path with no
    budget is a launch blocker."""
    assert all_hot_paths_have_budgets() == ()


def test_budget_for_returns_canonical_entry() -> None:
    budget = budget_for(HotPath.RAIL_KEYPRESS)
    assert isinstance(budget, PerfBudget)
    assert budget.hot_path is HotPath.RAIL_KEYPRESS
    assert budget.budget_ms > 0
    assert budget.rationale  # documented


def test_budget_for_known_hot_path_does_not_raise() -> None:
    """Every value in :class:`HotPath` resolves to a budget."""
    for hot_path in HotPath:
        assert isinstance(budget_for(hot_path), PerfBudget)


def test_rail_keypress_budget_matches_audit_target() -> None:
    """The audit cites #839 explicitly: rail keypress took 3-5
    seconds. The launch-hardening budget is 120ms — anything
    bigger means the cockpit feels laggy."""
    assert budget_for(HotPath.RAIL_KEYPRESS).budget_ms == 120


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_record_and_query_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_recorded_measurements()
    record_render_timing(HotPath.RAIL_KEYPRESS, 75.0, note="ok")
    record_render_timing(HotPath.RAIL_KEYPRESS, 95.0, note="ok2")
    record_render_timing(HotPath.INBOX_REFRESH, 200.0)

    rail = measurements_for(HotPath.RAIL_KEYPRESS)
    assert len(rail) == 2
    assert {m.duration_ms for m in rail} == {75.0, 95.0}
    inbox = measurements_for(HotPath.INBOX_REFRESH)
    assert len(inbox) == 1


def test_clear_recorded_measurements_resets() -> None:
    record_render_timing(HotPath.VIEW_SWITCH, 50.0)
    clear_recorded_measurements()
    assert measurements_for(HotPath.VIEW_SWITCH) == ()


# ---------------------------------------------------------------------------
# assert_within_budget context manager
# ---------------------------------------------------------------------------


def test_assert_within_budget_passes_under_budget() -> None:
    """A trivially fast block must pass."""
    clear_recorded_measurements()
    with assert_within_budget(HotPath.RAIL_KEYPRESS):
        pass
    samples = measurements_for(HotPath.RAIL_KEYPRESS)
    assert len(samples) == 1
    assert samples[0].duration_ms < 120


def test_assert_within_budget_fails_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A block that takes too long must raise AssertionError.

    The test simulates a slow block by monkey-patching
    ``time.perf_counter`` to advance more than the budget."""
    clear_recorded_measurements()

    state = {"calls": 0}
    real_pc = time.perf_counter

    def fake_pc() -> float:
        # First call (start) returns 0; second (end) returns 200ms
        # — well over the 120ms rail keypress budget.
        if state["calls"] == 0:
            state["calls"] += 1
            return 0.0
        return 0.200

    monkeypatch.setattr("pollypm.cockpit_perf.time.perf_counter", fake_pc)

    with pytest.raises(AssertionError, match="exceeding budget"):
        with assert_within_budget(HotPath.RAIL_KEYPRESS):
            pass

    monkeypatch.setattr("pollypm.cockpit_perf.time.perf_counter", real_pc)


def test_assert_within_budget_records_measurement_on_pass() -> None:
    """Successful runs still record a measurement so the release
    gate has a baseline."""
    clear_recorded_measurements()
    with assert_within_budget(HotPath.DASHBOARD_RENDER):
        pass
    samples = measurements_for(HotPath.DASHBOARD_RENDER)
    assert len(samples) == 1


def test_assert_within_budget_records_measurement_on_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed runs must still record so the audit can see how
    much over budget the breach was."""
    clear_recorded_measurements()
    state = {"calls": 0}

    def fake_pc() -> float:
        if state["calls"] == 0:
            state["calls"] += 1
            return 0.0
        return 5.0  # 5 seconds — way over every budget

    monkeypatch.setattr("pollypm.cockpit_perf.time.perf_counter", fake_pc)
    with pytest.raises(AssertionError):
        with assert_within_budget(HotPath.RAIL_KEYPRESS):
            pass
    samples = measurements_for(HotPath.RAIL_KEYPRESS)
    assert len(samples) == 1
    assert samples[0].duration_ms >= 1_000


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_perf_budget_audit_clean_when_under_budget() -> None:
    """All measurements within budget → empty audit."""
    clear_recorded_measurements()
    record_render_timing(HotPath.RAIL_KEYPRESS, 50.0)
    record_render_timing(HotPath.INBOX_REFRESH, 200.0)
    assert perf_budget_audit() == ()


def test_perf_budget_audit_flags_over_budget() -> None:
    """A breach is reported as a single line per offending
    measurement."""
    clear_recorded_measurements()
    record_render_timing(HotPath.RAIL_KEYPRESS, 500.0, note="cold path")
    out = perf_budget_audit()
    assert len(out) == 1
    assert "rail_keypress" in out[0]
    assert "500" in out[0]
    assert "120" in out[0]


def test_perf_budget_audit_reports_each_breach() -> None:
    """Multiple breaches report separately so the developer
    can see the full list."""
    clear_recorded_measurements()
    record_render_timing(HotPath.RAIL_KEYPRESS, 500.0)
    record_render_timing(HotPath.RAIL_KEYPRESS, 600.0)
    out = perf_budget_audit()
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_budgets_are_immutable() -> None:
    """A consumer cannot mutate the canonical list."""
    budget = budget_for(HotPath.RAIL_KEYPRESS)
    with pytest.raises((AttributeError, TypeError)):
        budget.budget_ms = 999  # type: ignore[misc]


def test_budget_count_matches_hot_path_count() -> None:
    """One budget per hot path; no orphans, no extras."""
    assert len(COCKPIT_PERF_BUDGETS) == len(HotPath)
