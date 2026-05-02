"""PM-led auto-repair for blocked task workers (#1026).

Surface
=======
When a task worker is blocked for any reason (stale base, dirty worktree,
missing deps, build error, ambiguous review feedback, ...), the supervising
project PM gets a chance to fix it before the blocker escalates to the
human. Repair recipes are small, focused, side-effect-bearing units that
each diagnose one well-known shape of blocker and either resolve it or
return a structured diagnosis.

The orchestrator (:func:`try_pm_repair`) walks a registry of recipes in
order. The first recipe whose ``applies_to(ctx)`` returns ``True`` is
attempted. If it returns :class:`RepairResult.repaired`, the worker can
retry. If it returns :class:`RepairResult.failed_with_diagnosis`, the
caller should escalate with the diagnosis attached. If it returns
:class:`RepairResult.not_applicable`, the orchestrator continues to the
next recipe.

Adding a new recipe
===================
Recipes are stand-alone modules that expose a single class implementing
the :class:`RepairRecipe` protocol. To add a new recipe (e.g. for
"dirty worktree", "missing deps", "build error"):

1. Create ``src/pollypm/pm_auto_repair/<name>_recipe.py``.
2. Implement a class with ``name``, ``applies_to``, and ``attempt``.
3. Register it by appending to :data:`DEFAULT_RECIPES` below.

The protocol intentionally keeps recipes self-contained: each recipe
owns its own subprocess plumbing and decides for itself whether it has
enough information to act. That keeps the orchestrator a thin walker
and lets recipes evolve independently.

v1 ships with a single recipe, :class:`RebaseAgainstMainRecipe`, which
addresses the original #1026 case ("parallel workers don't auto-rebase
when siblings merge — Russell rejects on stale base"). Follow-ups will
add recipes for dirty-worktree, missing-deps, build-error, and
ambiguous-reviewer-rejection.
"""

from __future__ import annotations

from pollypm.pm_auto_repair._types import (
    BlockerContext,
    BlockerType,
    RepairRecipe,
    RepairResult,
    RepairOutcome,
)
from pollypm.pm_auto_repair.orchestrator import (
    DEFAULT_RECIPES,
    try_pm_repair,
)
from pollypm.pm_auto_repair.rebase_recipe import RebaseAgainstMainRecipe

__all__ = [
    "BlockerContext",
    "BlockerType",
    "DEFAULT_RECIPES",
    "RebaseAgainstMainRecipe",
    "RepairOutcome",
    "RepairRecipe",
    "RepairResult",
    "try_pm_repair",
]
