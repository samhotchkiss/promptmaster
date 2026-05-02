"""Types shared by the PM auto-repair scaffold (#1026).

These types are the public-facing surface of :mod:`pollypm.pm_auto_repair`.
They live in their own module to break the import cycle between the
orchestrator and individual recipes (each recipe imports the protocol
and the result enum; the orchestrator imports the recipes).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class BlockerType(str, enum.Enum):
    """The shape of blocker the worker hit.

    Recipes inspect ``BlockerContext.blocker_type`` first as a cheap
    triage filter before doing anything expensive (subprocess calls,
    filesystem reads, etc.). Unknown / unclassified blockers carry
    :data:`UNKNOWN`; recipes that can still do useful work for an
    unclassified blocker (e.g. by sniffing the worktree directly)
    should opt in explicitly.
    """

    STALE_BASE = "stale_base"
    DIRTY_WORKTREE = "dirty_worktree"
    MISSING_DEPS = "missing_deps"
    BUILD_FAILURE = "build_failure"
    AMBIGUOUS_REVIEW = "ambiguous_review"
    UNKNOWN = "unknown"


class RepairResult(str, enum.Enum):
    """Three-valued result for a single repair attempt.

    ``repaired``
        The recipe made the change(s) it needed to and the caller
        should retry the worker submission.
    ``not_applicable``
        The recipe declined to act (didn't match the blocker shape, or
        couldn't safely determine that it should). The orchestrator
        moves on to the next recipe; the caller should treat this like
        "no repair attempted".
    ``failed_with_diagnosis``
        The recipe matched and tried, but couldn't fix it. The recipe
        attaches a human-readable diagnosis on the
        :class:`RepairOutcome` so the caller can surface a richer
        message than the raw blocker would carry.
    """

    REPAIRED = "repaired"
    NOT_APPLICABLE = "not_applicable"
    FAILED_WITH_DIAGNOSIS = "failed_with_diagnosis"


@dataclass(slots=True)
class BlockerContext:
    """Everything a recipe needs to know to decide if it can repair.

    Fields are kept deliberately narrow — recipes that need richer
    context (e.g. work-service handles, full task records) take that
    via additional optional fields rather than dragging in the world.

    Attributes
    ----------
    project_key:
        Project identifier (e.g. ``"pollypm"``). Used for logging /
        diagnosis text.
    task_id:
        Full task id (e.g. ``"pollypm-1234"``). Used for logging /
        diagnosis text.
    worker_role:
        Role of the worker that hit the blocker (e.g. ``"worker"``,
        ``"russell"``, ``"bea"``). Future recipes may use this to
        choose whether to act.
    blocker_type:
        Triage category, see :class:`BlockerType`.
    blocker_detail:
        Free-form text describing what went wrong (typically the
        reviewer's rejection reason or the worker's failure message).
    worktree_path:
        Filesystem path to the worker's git worktree. ``None`` means
        the caller didn't know — recipes that need it should return
        :data:`RepairResult.NOT_APPLICABLE`.
    main_branch:
        Name of the project's mainline branch. Defaults to ``"main"``
        but recipes should treat this field as authoritative so a
        project on ``master``/``trunk`` works without code changes.
    extra:
        Reserved for future fields. Recipes MUST tolerate unknown
        keys.
    """

    project_key: str
    task_id: str
    worker_role: str
    blocker_type: BlockerType
    blocker_detail: str
    worktree_path: Path | None = None
    main_branch: str = "main"
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RepairOutcome:
    """Structured outcome of a repair attempt.

    Recipes return this from :meth:`RepairRecipe.attempt` so the
    orchestrator + the caller share one shape.

    ``recipe_name`` is the name of the recipe that produced the
    outcome (so escalation messages can say "PM tried RebaseAgainstMain
    — none worked"). ``diagnosis`` is recipe-authored prose that the
    caller can show the user when ``result`` is
    :data:`RepairResult.FAILED_WITH_DIAGNOSIS`.
    """

    result: RepairResult
    recipe_name: str
    diagnosis: str = ""
    details: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class RepairRecipe(Protocol):
    """One repair recipe.

    Implementations must be cheap to construct (typically
    parameter-less) so the orchestrator can keep a module-level
    instance in :data:`DEFAULT_RECIPES`. All side effects belong in
    :meth:`attempt` — :meth:`applies_to` must be a pure inspection of
    ``ctx``.
    """

    @property
    def name(self) -> str:
        """Stable identifier for the recipe (used in diagnoses)."""
        ...

    def applies_to(self, ctx: BlockerContext) -> bool:
        """Cheap predicate: can this recipe potentially repair ``ctx``?

        ``True`` does NOT promise success — only that
        :meth:`attempt` is worth calling.
        """
        ...

    def attempt(self, ctx: BlockerContext) -> RepairOutcome:
        """Try to repair the blocker. May have side effects."""
        ...
