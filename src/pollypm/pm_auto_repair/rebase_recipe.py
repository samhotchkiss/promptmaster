"""Rebase-against-main recipe (#1026 — first PM auto-repair pattern).

When parallel workers ship sibling tasks, the second worker's branch
ends up behind ``main`` once the first sibling merges. Reviewers
(particularly Russell on tightly-gated projects) reject the second
submission as "stale base". This recipe detects that case and runs
``git merge main`` inside the worker's worktree so the worker can
re-submit without losing work.

Design notes
============
* The recipe is **defensive about uncertainty**. If the worktree path
  is missing, the directory isn't a git worktree, the project has no
  ``main`` branch, ``main`` isn't reachable, or HEAD is detached, we
  return :data:`RepairResult.NOT_APPLICABLE` and let the caller fall
  through to existing behavior.
* The merge runs with explicit ``-c user.name=PollyPM-PM -c
  user.email=pm@local`` so the operation doesn't depend on the host
  user's git identity (some CI / sandbox environments have no global
  identity configured).
* On conflicts we do NOT attempt to auto-resolve. We abort the merge
  and return :data:`RepairResult.FAILED_WITH_DIAGNOSIS` with the
  conflicting paths in the diagnosis. The caller can surface those to
  the human reviewer.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pollypm.pm_auto_repair._types import (
    BlockerContext,
    BlockerType,
    RepairOutcome,
    RepairResult,
)

logger = logging.getLogger(__name__)


_PM_GIT_IDENTITY: tuple[str, ...] = (
    "-c",
    "user.name=PollyPM-PM",
    "-c",
    "user.email=pm@local",
)


# Phrases in a rejection reason that are strong signals of a stale-base
# situation. The recipe does NOT require any of these — they only act
# as a fast-path for ``applies_to`` when ``blocker_type`` is UNKNOWN.
_STALE_BASE_HINTS: tuple[str, ...] = (
    "stale base",
    "stale-base",
    "behind main",
    "rebase against main",
    "rebase onto main",
    "merge main",
    "out of date with main",
    "base is stale",
    "needs rebase",
)


@dataclass(slots=True)
class RebaseAgainstMainRecipe:
    """Run ``git merge <main>`` in the worker's worktree to refresh the base."""

    @property
    def name(self) -> str:
        return "rebase_against_main"

    def applies_to(self, ctx: BlockerContext) -> bool:
        if ctx.worktree_path is None:
            return False
        if ctx.blocker_type == BlockerType.STALE_BASE:
            return True
        # Fall-through: unclassified blockers whose detail text mentions
        # a stale-base shape. We don't claim DIRTY_WORKTREE etc. here —
        # a future recipe owns those.
        if ctx.blocker_type != BlockerType.UNKNOWN:
            return False
        detail = (ctx.blocker_detail or "").lower()
        return any(hint in detail for hint in _STALE_BASE_HINTS)

    def attempt(self, ctx: BlockerContext) -> RepairOutcome:
        worktree = ctx.worktree_path
        if worktree is None:
            return self._not_applicable("worktree_path is unset")
        worktree_path = Path(worktree)
        if not worktree_path.exists() or not worktree_path.is_dir():
            return self._not_applicable(
                f"worktree path does not exist: {worktree_path}"
            )

        # Confirm this is a git worktree at all.
        toplevel = self._git_run(worktree_path, "rev-parse", "--show-toplevel")
        if toplevel.returncode != 0:
            return self._not_applicable(f"{worktree_path} is not a git worktree")

        # HEAD must be on a named branch. A detached HEAD is ambiguous
        # — refuse rather than guess.
        head = self._git_run(worktree_path, "symbolic-ref", "--quiet", "HEAD")
        if head.returncode != 0:
            return self._not_applicable("HEAD is detached")
        current_branch = head.stdout.strip().removeprefix("refs/heads/")
        if not current_branch:
            return self._not_applicable("could not determine current branch")
        main_branch = ctx.main_branch or "main"
        if current_branch == main_branch:
            return self._not_applicable(f"already on {main_branch}; nothing to rebase")

        # Resolve the main ref. Prefer the local branch; fall back to
        # ``origin/<main>`` when the worktree only has the remote ref.
        main_ref = self._resolve_main_ref(worktree_path, main_branch)
        if main_ref is None:
            return self._not_applicable(
                f"no ref named {main_branch} (or origin/{main_branch}) in {worktree_path}"
            )

        # Cheap stale-base check: is main already an ancestor of HEAD?
        # If yes, there's nothing to merge.
        is_ancestor = self._git_run(
            worktree_path,
            "merge-base",
            "--is-ancestor",
            main_ref,
            "HEAD",
        )
        if is_ancestor.returncode == 0:
            return RepairOutcome(
                result=RepairResult.NOT_APPLICABLE,
                recipe_name=self.name,
                diagnosis=(
                    f"{main_ref} is already an ancestor of HEAD; nothing to rebase."
                ),
            )

        # Refuse to operate on a dirty worktree — that's a different
        # repair recipe's job and we don't want to clobber uncommitted
        # work.
        status = self._git_run(worktree_path, "status", "--porcelain")
        if status.returncode != 0:
            return self._fail(
                "git status failed",
                f"git status returned {status.returncode}: "
                f"{(status.stderr or status.stdout).strip()}",
            )
        if status.stdout.strip():
            return self._fail(
                "worktree is dirty",
                "Refusing to merge "
                f"{main_ref} into {current_branch} because the worktree "
                "has uncommitted changes. Resolve them (or run a "
                "future dirty-worktree recipe) and retry.",
            )

        merge = self._git_run(
            worktree_path,
            *_PM_GIT_IDENTITY,
            "merge",
            "--no-edit",
            "--no-ff",
            main_ref,
        )
        if merge.returncode == 0:
            return RepairOutcome(
                result=RepairResult.REPAIRED,
                recipe_name=self.name,
                diagnosis=(
                    f"Merged {main_ref} into {current_branch} in {worktree_path}."
                ),
                details={
                    "merged_ref": main_ref,
                    "branch": current_branch,
                    "worktree_path": str(worktree_path),
                },
            )

        # Conflict path: surface the conflicting paths and abort.
        conflicts = self._git_run(
            worktree_path, "diff", "--name-only", "--diff-filter=U"
        )
        conflicting_paths: list[str] = []
        if conflicts.returncode == 0:
            conflicting_paths = [
                line.strip() for line in conflicts.stdout.splitlines() if line.strip()
            ]
        # Best-effort abort — don't leave the worktree in a partial
        # merge state. Swallow errors here; if abort itself fails the
        # diagnosis still reports the conflict.
        abort = self._git_run(worktree_path, "merge", "--abort")
        if abort.returncode != 0:
            logger.warning(
                "pm_auto_repair: git merge --abort failed in %s: %s",
                worktree_path,
                (abort.stderr or abort.stdout).strip(),
            )

        if conflicting_paths:
            paths_block = "\n  - ".join(conflicting_paths)
            diagnosis = (
                f"Merging {main_ref} into {current_branch} produced "
                f"conflicts in:\n  - {paths_block}\n"
                "Auto-merge refuses to resolve these — a human (or the "
                "worker) needs to reconcile them."
            )
        else:
            diagnosis = (
                f"git merge {main_ref} failed in {worktree_path}: "
                f"{(merge.stderr or merge.stdout).strip()}"
            )
        return RepairOutcome(
            result=RepairResult.FAILED_WITH_DIAGNOSIS,
            recipe_name=self.name,
            diagnosis=diagnosis,
            details={
                "merged_ref": main_ref,
                "branch": current_branch,
                "worktree_path": str(worktree_path),
                "conflicting_paths": conflicting_paths,
            },
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _not_applicable(self, reason: str) -> RepairOutcome:
        return RepairOutcome(
            result=RepairResult.NOT_APPLICABLE,
            recipe_name=self.name,
            diagnosis=reason,
        )

    def _fail(self, summary: str, detail: str) -> RepairOutcome:
        return RepairOutcome(
            result=RepairResult.FAILED_WITH_DIAGNOSIS,
            recipe_name=self.name,
            diagnosis=f"{summary}: {detail}",
        )

    def _resolve_main_ref(self, worktree_path: Path, main_branch: str) -> str | None:
        local = self._git_run(
            worktree_path, "rev-parse", "--verify", "--quiet", main_branch
        )
        if local.returncode == 0 and local.stdout.strip():
            return main_branch
        remote = f"origin/{main_branch}"
        remote_check = self._git_run(
            worktree_path, "rev-parse", "--verify", "--quiet", remote
        )
        if remote_check.returncode == 0 and remote_check.stdout.strip():
            return remote
        return None

    @staticmethod
    def _git_run(worktree_path: Path, *args: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            ["git", "-C", str(worktree_path), *args],
            check=False,
            capture_output=True,
            text=True,
        )
