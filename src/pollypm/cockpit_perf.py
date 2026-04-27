"""Cockpit performance budgets and latency assertions (#891).

Defines the launch-hardening latency contract every cockpit
hot path must meet, plus assertion helpers used by the smoke
matrix and CI.

The pre-launch audit (``docs/launch-issue-audit-2026-04-27.md``
§10) cites the recurring shape: render paths scan too much,
import too much, or recompute on every tick. The headline
example was `#839` — every ``j``/``k`` rail keypress took 3-5
seconds because each press triggered a full dashboard
snapshot scan.

Strategy:

* :class:`PerfBudget` declares an upper-bound latency for one
  named hot path.
* :data:`COCKPIT_PERF_BUDGETS` is the canonical list. The
  cockpit smoke matrix consults it.
* :func:`assert_within_budget` is a context manager for tests:
  ``with assert_within_budget("rail_keypress"): ...``.
* :func:`record_render_timing` is a hook the smoke harness can
  call to log the actual measurement; the release gate (#889)
  reads the latest run for regression detection.

Migration policy: the budgets are deliberately *generous* at
launch. Tightening them happens after the first round of
optimisations lands and we have empirical baselines. The
release gate uses the budget as a hard ceiling — performance
better than the budget is fine; worse fails the gate.
"""

from __future__ import annotations

import contextlib
import enum
import time
from dataclasses import dataclass, field
from typing import Iterator, Mapping


# ---------------------------------------------------------------------------
# Hot-path catalogue
# ---------------------------------------------------------------------------


class HotPath(enum.Enum):
    """Named hot paths the cockpit cares about.

    Each member's value is the canonical key used in budgets and
    measurements. New hot paths added to the cockpit must register
    here so the audit and the release gate can budget them."""

    COCKPIT_STARTUP = "cockpit_startup"
    """First mount of the cockpit App — from process start to
    the rail being interactive."""

    RAIL_KEYPRESS = "rail_keypress"
    """j / k / arrow / Enter on the rail. The #839 budget; every
    keystroke must complete inside this window or the cockpit
    feels sluggish."""

    VIEW_SWITCH = "view_switch"
    """Routing from one cockpit App to another (Home → Inbox,
    Inbox → Activity, etc.)."""

    ACTIVITY_SEARCH = "activity_search"
    """Activity feed search / filter."""

    INBOX_REFRESH = "inbox_refresh"
    """Inbox ``r`` refresh."""

    SETTINGS_LOAD = "settings_load"
    """Initial mount of the Settings App."""

    DASHBOARD_RENDER = "dashboard_render"
    """Home / Dashboard render after a refresh."""


# ---------------------------------------------------------------------------
# Budget shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PerfBudget:
    """Upper bound for one hot path's latency.

    Fields:

    * ``hot_path`` — :class:`HotPath` member.
    * ``budget_ms`` — milliseconds. The hot path must complete
      within this many milliseconds at p95 over the smoke matrix.
    * ``rationale`` — short human-readable note explaining why
      the budget is what it is. Used by the release-gate report
      so a developer reviewing a budget breach sees the cost of
      the regression at a glance.
    """

    hot_path: HotPath
    budget_ms: int
    rationale: str = ""


COCKPIT_PERF_BUDGETS: tuple[PerfBudget, ...] = (
    PerfBudget(
        hot_path=HotPath.COCKPIT_STARTUP,
        budget_ms=2_500,
        rationale=(
            "Total time from `pm up` to interactive rail must feel "
            "fast; 2.5s is the loosest target the audit calls "
            "actionable. Tighten as the supervisor cold path "
            "improves."
        ),
    ),
    PerfBudget(
        hot_path=HotPath.RAIL_KEYPRESS,
        budget_ms=120,
        rationale=(
            "The #839 budget. Anything slower than ~120ms feels "
            "laggy on a 60Hz terminal redraw."
        ),
    ),
    PerfBudget(
        hot_path=HotPath.VIEW_SWITCH,
        budget_ms=350,
        rationale=(
            "Routing to a different App involves an inbox / "
            "activity / settings load. Below 350ms the user "
            "perceives it as instant."
        ),
    ),
    PerfBudget(
        hot_path=HotPath.ACTIVITY_SEARCH,
        budget_ms=300,
        rationale=(
            "FTS5 search against the unified messages table. The "
            "shadow table + indexes already hit this budget on "
            "representative data."
        ),
    ),
    PerfBudget(
        hot_path=HotPath.INBOX_REFRESH,
        budget_ms=600,
        rationale=(
            "Per-project DB scan plus annotation. Loosest hot "
            "path; can tighten once the consolidated read API "
            "ships."
        ),
    ),
    PerfBudget(
        hot_path=HotPath.SETTINGS_LOAD,
        budget_ms=500,
        rationale=(
            "Settings reads accounts + sessions + project list. "
            "Cached snapshots keep us inside this budget."
        ),
    ),
    PerfBudget(
        hot_path=HotPath.DASHBOARD_RENDER,
        budget_ms=400,
        rationale=(
            "Home / Dashboard renders many cards but every card "
            "is a precomputed snapshot read. Bigger than expected "
            "is the smoking gun for a re-introduced live scan."
        ),
    ),
)


def budget_for(hot_path: HotPath) -> PerfBudget:
    """Return the canonical :class:`PerfBudget` for ``hot_path``."""
    for budget in COCKPIT_PERF_BUDGETS:
        if budget.hot_path is hot_path:
            return budget
    raise KeyError(f"no budget registered for {hot_path!r}")


def all_hot_paths_have_budgets() -> tuple[str, ...]:
    """Return the names of any :class:`HotPath` lacking a budget.

    A clean run returns ``()``. The release gate (#889) consults
    this so a new hot path cannot land without an explicit budget."""
    have = {b.hot_path for b in COCKPIT_PERF_BUDGETS}
    return tuple(p.name for p in HotPath if p not in have)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PerfMeasurement:
    """One observed latency sample."""

    hot_path: HotPath
    duration_ms: float
    note: str = ""


_RECORDED_MEASUREMENTS: list[PerfMeasurement] = []


def record_render_timing(
    hot_path: HotPath, duration_ms: float, *, note: str = ""
) -> None:
    """Record one latency sample for the smoke matrix or perf
    test to consult."""
    _RECORDED_MEASUREMENTS.append(
        PerfMeasurement(
            hot_path=hot_path, duration_ms=duration_ms, note=note
        )
    )


def measurements_for(hot_path: HotPath) -> tuple[PerfMeasurement, ...]:
    """Return every recorded measurement for ``hot_path``."""
    return tuple(
        m for m in _RECORDED_MEASUREMENTS if m.hot_path is hot_path
    )


def clear_recorded_measurements() -> None:
    """Reset the recorded list. Tests call this between runs."""
    _RECORDED_MEASUREMENTS.clear()


# ---------------------------------------------------------------------------
# Assertion context manager
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def assert_within_budget(hot_path: HotPath) -> Iterator[None]:
    """Yield a context that asserts the wrapped block stays
    under :func:`budget_for`'s budget.

    Usage::

        with assert_within_budget(HotPath.RAIL_KEYPRESS):
            await pilot.press("j")

    The context records the measured duration via
    :func:`record_render_timing` so the release gate report
    can include it."""
    budget = budget_for(hot_path)
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1_000
        record_render_timing(
            hot_path,
            elapsed_ms,
            note=f"budget={budget.budget_ms}ms",
        )
        if elapsed_ms > budget.budget_ms:
            raise AssertionError(
                f"{hot_path.value} took {elapsed_ms:.0f}ms, exceeding "
                f"budget of {budget.budget_ms}ms. ({budget.rationale})"
            )


# ---------------------------------------------------------------------------
# Release gate helper
# ---------------------------------------------------------------------------


def perf_budget_audit() -> tuple[str, ...]:
    """Return one human-readable line per recorded measurement
    that exceeds its budget.

    Empty tuple = clean. The release gate (#889) consults this
    when it has prior measurements available."""
    out: list[str] = []
    for measurement in _RECORDED_MEASUREMENTS:
        budget = budget_for(measurement.hot_path)
        if measurement.duration_ms > budget.budget_ms:
            out.append(
                f"{measurement.hot_path.value}: "
                f"{measurement.duration_ms:.0f}ms exceeds "
                f"{budget.budget_ms}ms budget "
                f"({measurement.note})"
            )
    return tuple(out)
