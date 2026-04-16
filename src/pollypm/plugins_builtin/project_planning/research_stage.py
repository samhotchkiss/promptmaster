"""Research stage helpers — ReAct loop, budget, and context artifact.

The architect runs stage 0 of ``plan_project`` as a ReAct loop: act
(grep/read/list_files/web_search) → observe → think → refine → repeat.
The loop produces a ``context.md`` artifact in the planning worktree
that summarises what the architect learned. This artifact becomes the
input to stage 1 (Discover) — the `Understanding` step cannot start
without it.

This module ships three things used by the research stage:

1. ``research_stage_prompt()`` — the exact instruction block the
   architect reads when entering the research node. Appended to the
   architect's persona prompt; spelled out in full here so it's a
   single load-bearing source.
2. ``ResearchBudget`` — a wall-clock timer with an expiry check. The
   flow engine can surface remaining time to the session via
   ``seconds_remaining()`` and tell the agent to wrap up.
3. ``write_context_artifact`` / ``context_artifact_ready`` — helpers
   that own the path (``docs/planning-context.md``) and the
   "non-empty" contract that stage 1 inherits.

Budgets are re-used by subsequent stages (discover, decompose, magic,
critic, synthesize) — pp07 wires the per-stage defaults through
``pollypm.toml``. Research's default is 10 minutes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RESEARCH_BUDGET_SECONDS: int = 10 * 60
"""Hard cap for the Research stage per spec §6 (10 minutes)."""


CONTEXT_ARTIFACT_RELATIVE = "docs/planning-context.md"
"""Where the architect writes the context artifact, relative to project root."""


def research_stage_prompt(budget_seconds: int = DEFAULT_RESEARCH_BUDGET_SECONDS) -> str:
    """Return the ReAct-loop instruction block for the research stage."""
    return (
        "<research-stage>\n"
        "You are in Stage 0 (Research) of the PollyPM planning flow. Before "
        "you form opinions about decomposition, you must gather evidence.\n\n"
        "## ReAct loop\n"
        "Repeat until your understanding stabilises OR the budget expires:\n"
        "1. **Act** — invoke at least one of: grep, read, list_files, "
        "web_search. Pick the tool that answers the specific question you "
        "have right now.\n"
        "2. **Observe** — record what the tool told you. Copy the load-bearing "
        "bits verbatim; paraphrases lose nuance.\n"
        "3. **Think** — what did you expect? What was surprising? What is "
        "still unknown?\n"
        "4. **Refine** — update your running hypothesis. If your hypothesis "
        "is unchanged, either pick a different tool or move on.\n\n"
        f"## Time budget: {budget_seconds} seconds ({budget_seconds // 60} min)\n"
        "Wall-clock budget is hard. When the budget expires, STOP "
        "whatever you were doing and write the context artifact with what "
        "you have — partial context beats no context.\n\n"
        "## Mandatory actions\n"
        "- Before you emit the context artifact, you MUST have invoked "
        "grep, read, or list_files at least once during this stage. A "
        "research stage that never acted is not research.\n"
        "- If the project is fresh / empty, list_files on the project root "
        "counts. The point is to ground your opinions in the actual tree, "
        "not your priors.\n\n"
        "## Output contract\n"
        f"Write `{CONTEXT_ARTIFACT_RELATIVE}` at the project root. It must "
        "contain, at minimum:\n"
        "- Project summary (1 paragraph): what this project is, where it "
        "lives on disk, what language / stack.\n"
        "- Observed structure: top-level files/dirs and their apparent "
        "purposes.\n"
        "- Ecosystem notes: any external docs / libraries / tools the "
        "project depends on, cited from your web_search.\n"
        "- Known unknowns: questions you could not resolve within the "
        "budget. These become clarifying-question candidates for stage 1.\n"
        "The artifact must be non-empty. An empty file fails the transition "
        "to Discover.\n"
        "</research-stage>"
    )


@dataclass(slots=True)
class ResearchBudget:
    """Wall-clock budget tracker for the research stage.

    Instantiate at stage entry; call ``expired()`` to check, or
    ``seconds_remaining()`` to surface to the agent. A ``total_seconds``
    override lets ``pollypm.toml``-driven budgets (pp07) replace the
    default without changing callers.
    """

    total_seconds: int = DEFAULT_RESEARCH_BUDGET_SECONDS
    started_at: float | None = None

    def start(self) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()

    def seconds_remaining(self) -> float:
        if self.started_at is None:
            return float(self.total_seconds)
        elapsed = time.monotonic() - self.started_at
        return max(0.0, float(self.total_seconds) - elapsed)

    def expired(self) -> bool:
        return self.seconds_remaining() <= 0.0


def context_artifact_path(project_root: str | Path) -> Path:
    """Return the absolute path of the context artifact for a project."""
    return Path(project_root) / CONTEXT_ARTIFACT_RELATIVE


def write_context_artifact(project_root: str | Path, body: str) -> Path:
    """Write the context artifact atomically and return its path.

    The caller owns the body text. An empty body is rejected here so
    the contract is enforced at the write-site, not only at the
    transition check.
    """
    if not body or not body.strip():
        raise ValueError(
            "Context artifact body is empty; the research stage must "
            "produce non-empty context before stage 1."
        )
    path = context_artifact_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    return path


def context_artifact_ready(project_root: str | Path) -> bool:
    """True when the context artifact exists and is non-empty.

    Used as the transition guard between stage 0 (Research) and stage 1
    (Discover) — the pp04 acceptance gate.
    """
    path = context_artifact_path(project_root)
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False
