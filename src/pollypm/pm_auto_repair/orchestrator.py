"""Recipe-walking orchestrator for PM auto-repair (#1026).

The orchestrator is intentionally thin: it walks a recipe registry in
order, returning the first non-:data:`RepairResult.NOT_APPLICABLE`
outcome. This keeps the call-site contract trivial — callers see a
single :class:`RepairOutcome` and don't need to know which recipes
exist or how many ran.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

from pollypm.pm_auto_repair._types import (
    BlockerContext,
    RepairOutcome,
    RepairRecipe,
    RepairResult,
)
from pollypm.pm_auto_repair.rebase_recipe import RebaseAgainstMainRecipe

logger = logging.getLogger(__name__)


# Module-level default registry. Recipes are tried in order; the first
# applicable one wins. Future recipes append here (see the package
# docstring in ``__init__.py`` for the extension recipe).
DEFAULT_RECIPES: tuple[RepairRecipe, ...] = (RebaseAgainstMainRecipe(),)


def try_pm_repair(
    ctx: BlockerContext,
    *,
    recipes: Sequence[RepairRecipe] | None = None,
) -> RepairOutcome:
    """Walk the recipe registry and return the first decisive outcome.

    Parameters
    ----------
    ctx:
        The blocker context. See :class:`BlockerContext`.
    recipes:
        Override the registry (used by tests + future per-project
        customization). When ``None``, :data:`DEFAULT_RECIPES` is used.

    Returns
    -------
    RepairOutcome
        Either ``REPAIRED`` (caller should retry), or
        ``FAILED_WITH_DIAGNOSIS`` (caller should escalate with the
        attached diagnosis), or ``NOT_APPLICABLE`` (no recipe matched
        — caller falls back to the existing block/reject behavior).
    """

    chain: Iterable[RepairRecipe] = recipes if recipes is not None else DEFAULT_RECIPES
    last_diagnosis: RepairOutcome | None = None
    for recipe in chain:
        try:
            applies = recipe.applies_to(ctx)
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning(
                "pm_auto_repair recipe %s.applies_to raised: %s",
                getattr(recipe, "name", recipe.__class__.__name__),
                exc,
            )
            continue
        if not applies:
            continue
        try:
            outcome = recipe.attempt(ctx)
        except Exception as exc:  # noqa: BLE001 - never let a buggy recipe block the worker
            logger.warning(
                "pm_auto_repair recipe %s.attempt raised: %s",
                getattr(recipe, "name", recipe.__class__.__name__),
                exc,
            )
            continue
        if outcome.result == RepairResult.REPAIRED:
            return outcome
        if outcome.result == RepairResult.FAILED_WITH_DIAGNOSIS:
            # Remember the first diagnosis but keep walking — a later
            # recipe might still repair the blocker (different angle on
            # the same shape).
            if last_diagnosis is None:
                last_diagnosis = outcome
            continue
        # NOT_APPLICABLE — keep walking.
    if last_diagnosis is not None:
        return last_diagnosis
    return RepairOutcome(
        result=RepairResult.NOT_APPLICABLE,
        recipe_name="",
        diagnosis="No PM repair recipe matched the blocker.",
    )
