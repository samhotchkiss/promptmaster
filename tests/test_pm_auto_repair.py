"""Tests for the PM auto-repair scaffold (#1026).

Covers:

* The :class:`RebaseAgainstMainRecipe` ``applies_to`` predicate (stale
  base vs fresh base vs missing-worktree-path).
* :class:`RebaseAgainstMainRecipe.attempt` happy path (no conflicts).
* :class:`RebaseAgainstMainRecipe.attempt` conflict path
  (failed_with_diagnosis with conflicting paths).
* :func:`try_pm_repair` orchestration (recipe registry walking,
  defensive exception handling).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pollypm.pm_auto_repair import (
    BlockerContext,
    BlockerType,
    RebaseAgainstMainRecipe,
    RepairOutcome,
    RepairResult,
    try_pm_repair,
)


# ---------------------------------------------------------------------------
# git scaffolding helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _git_must(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = _git(repo, *args)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}: {result.stderr or result.stdout}"
        )
    return result


@pytest.fixture
def git_main_repo(tmp_path: Path) -> Path:
    """Initialize a repo on ``main`` with one commit."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_must(repo, "init", "--initial-branch=main")
    # Local identity so commits work even when global identity is unset.
    _git_must(repo, "config", "user.name", "Test")
    _git_must(repo, "config", "user.email", "test@local")
    (repo / "README.md").write_text("hello\n")
    _git_must(repo, "add", "README.md")
    _git_must(repo, "commit", "-m", "initial")
    return repo


@pytest.fixture
def stale_base_worktree(git_main_repo: Path) -> dict[str, Path]:
    """Worker branch behind main (sibling merged after worker branched).

    Layout:
        main:    A -- B (sibling.txt added)
                  \\
        worker:    A   (worker branched at A, hasn't seen B)

    The recipe should detect main is not an ancestor of HEAD and run
    ``git merge main`` cleanly.
    """

    repo = git_main_repo
    # Branch worker off the initial commit.
    _git_must(repo, "branch", "task/demo-1")
    # Move main forward (the "sibling merged" event).
    (repo / "sibling.txt").write_text("from sibling\n")
    _git_must(repo, "add", "sibling.txt")
    _git_must(repo, "commit", "-m", "sibling lands on main")
    # Switch the worker to its own branch in the same repo so we can
    # treat the repo dir as the worker worktree (single-worktree
    # variant of the real layout — adequate for the recipe's logic).
    _git_must(repo, "checkout", "task/demo-1")
    return {"worktree": repo, "main": repo}


@pytest.fixture
def fresh_base_worktree(git_main_repo: Path) -> Path:
    """Worker branch strictly ahead of main — nothing to rebase."""

    repo = git_main_repo
    _git_must(repo, "checkout", "-b", "task/demo-2")
    (repo / "feature.txt").write_text("worker work\n")
    _git_must(repo, "add", "feature.txt")
    _git_must(repo, "commit", "-m", "worker change")
    return repo


@pytest.fixture
def conflicting_worktree(git_main_repo: Path) -> Path:
    """Stale-base worker branch whose merge will conflict with main.

    Both sides edit ``README.md`` differently after the common ancestor.
    """

    repo = git_main_repo
    _git_must(repo, "checkout", "-b", "task/demo-3")
    (repo / "README.md").write_text("worker side\n")
    _git_must(repo, "add", "README.md")
    _git_must(repo, "commit", "-m", "worker edits readme")
    _git_must(repo, "checkout", "main")
    (repo / "README.md").write_text("main side\n")
    _git_must(repo, "add", "README.md")
    _git_must(repo, "commit", "-m", "main edits readme")
    _git_must(repo, "checkout", "task/demo-3")
    return repo


# ---------------------------------------------------------------------------
# applies_to
# ---------------------------------------------------------------------------


def test_applies_to_stale_base_with_worktree(tmp_path: Path) -> None:
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="rebase against main",
        worktree_path=tmp_path,
    )
    assert recipe.applies_to(ctx) is True


def test_applies_to_unknown_with_hint_in_detail(tmp_path: Path) -> None:
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.UNKNOWN,
        blocker_detail="Reviewer: please rebase against main",
        worktree_path=tmp_path,
    )
    assert recipe.applies_to(ctx) is True


def test_does_not_apply_when_worktree_path_missing() -> None:
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=None,
    )
    assert recipe.applies_to(ctx) is False


def test_does_not_apply_for_other_blocker_types(tmp_path: Path) -> None:
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.DIRTY_WORKTREE,
        blocker_detail="worktree dirty",
        worktree_path=tmp_path,
    )
    assert recipe.applies_to(ctx) is False


# ---------------------------------------------------------------------------
# attempt — happy path
# ---------------------------------------------------------------------------


def test_attempt_repairs_stale_base_cleanly(
    stale_base_worktree: dict[str, Path],
) -> None:
    worktree = stale_base_worktree["worktree"]
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="reviewer says base is stale",
        worktree_path=worktree,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.REPAIRED, outcome.diagnosis
    assert outcome.recipe_name == "rebase_against_main"
    # Sibling commit should now be reachable from the worker branch.
    assert (worktree / "sibling.txt").exists()
    log = _git_must(worktree, "log", "--oneline")
    assert "sibling lands on main" in log.stdout


def test_attempt_uses_pm_identity_for_merge_commit(
    stale_base_worktree: dict[str, Path],
) -> None:
    worktree = stale_base_worktree["worktree"]
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=worktree,
    )

    outcome = recipe.attempt(ctx)
    assert outcome.result == RepairResult.REPAIRED

    author = _git_must(worktree, "log", "-1", "--pretty=%an <%ae>")
    # ``--no-ff`` ensures we get a merge commit even from a fresh
    # branch, so the most recent commit is PM-authored.
    assert author.stdout.strip() == "PollyPM-PM <pm@local>"


# ---------------------------------------------------------------------------
# attempt — not-applicable shapes (defensive)
# ---------------------------------------------------------------------------


def test_attempt_returns_not_applicable_when_main_already_ancestor(
    fresh_base_worktree: Path,
) -> None:
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-2",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="reviewer claimed stale base",
        worktree_path=fresh_base_worktree,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.NOT_APPLICABLE
    assert "ancestor" in outcome.diagnosis.lower()


def test_attempt_returns_not_applicable_when_not_a_git_worktree(
    tmp_path: Path,
) -> None:
    plain_dir = tmp_path / "not-git"
    plain_dir.mkdir()
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-9",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=plain_dir,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.NOT_APPLICABLE


def test_attempt_returns_not_applicable_when_worktree_path_does_not_exist(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-9",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=missing,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.NOT_APPLICABLE


def test_attempt_returns_not_applicable_when_no_main_branch(
    git_main_repo: Path,
) -> None:
    repo = git_main_repo
    # Rename main -> trunk and DON'T set up an ``origin`` so neither
    # ``main`` nor ``origin/main`` resolves.
    _git_must(repo, "branch", "-m", "main", "trunk")
    _git_must(repo, "checkout", "-b", "task/demo-1")
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=repo,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.NOT_APPLICABLE


def test_attempt_returns_not_applicable_when_head_detached(
    git_main_repo: Path,
) -> None:
    repo = git_main_repo
    head_sha = _git_must(repo, "rev-parse", "HEAD").stdout.strip()
    _git_must(repo, "checkout", "--detach", head_sha)
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=repo,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# attempt — conflict path
# ---------------------------------------------------------------------------


def test_attempt_fails_with_diagnosis_on_conflict(
    conflicting_worktree: Path,
) -> None:
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-3",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=conflicting_worktree,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.FAILED_WITH_DIAGNOSIS
    assert "README.md" in outcome.diagnosis
    assert outcome.details["conflicting_paths"] == ["README.md"]
    # Merge must have been aborted — no MERGE_HEAD lingering.
    merge_head = conflicting_worktree / ".git" / "MERGE_HEAD"
    assert not merge_head.exists()


def test_attempt_refuses_to_merge_into_dirty_worktree(
    stale_base_worktree: dict[str, Path],
) -> None:
    worktree = stale_base_worktree["worktree"]
    # Add an uncommitted change to the worker side.
    (worktree / "scratch.txt").write_text("uncommitted\n")
    recipe = RebaseAgainstMainRecipe()
    ctx = BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=worktree,
    )

    outcome = recipe.attempt(ctx)

    assert outcome.result == RepairResult.FAILED_WITH_DIAGNOSIS
    assert "dirty" in outcome.diagnosis.lower()


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


class _StubRecipe:
    def __init__(
        self,
        name: str,
        applies: bool,
        outcome_result: RepairResult,
        diagnosis: str = "",
    ) -> None:
        self._name = name
        self._applies = applies
        self._outcome_result = outcome_result
        self._diagnosis = diagnosis
        self.attempts = 0

    @property
    def name(self) -> str:
        return self._name

    def applies_to(self, ctx: BlockerContext) -> bool:  # noqa: ARG002
        return self._applies

    def attempt(self, ctx: BlockerContext) -> RepairOutcome:  # noqa: ARG002
        self.attempts += 1
        return RepairOutcome(
            result=self._outcome_result,
            recipe_name=self._name,
            diagnosis=self._diagnosis,
        )


def _ctx() -> BlockerContext:
    return BlockerContext(
        project_key="demo",
        task_id="demo-1",
        worker_role="worker",
        blocker_type=BlockerType.STALE_BASE,
        blocker_detail="stale base",
        worktree_path=Path("/tmp/does-not-matter"),
    )


def test_orchestrator_skips_non_applicable_then_runs_first_match() -> None:
    skip = _StubRecipe("skip", applies=False, outcome_result=RepairResult.REPAIRED)
    hit = _StubRecipe("hit", applies=True, outcome_result=RepairResult.REPAIRED)
    after = _StubRecipe("after", applies=True, outcome_result=RepairResult.REPAIRED)

    outcome = try_pm_repair(_ctx(), recipes=[skip, hit, after])

    assert outcome.result == RepairResult.REPAIRED
    assert outcome.recipe_name == "hit"
    assert skip.attempts == 0
    assert hit.attempts == 1
    assert after.attempts == 0  # short-circuit on REPAIRED


def test_orchestrator_returns_first_diagnosis_when_no_recipe_repairs() -> None:
    fail_first = _StubRecipe(
        "fail_first",
        applies=True,
        outcome_result=RepairResult.FAILED_WITH_DIAGNOSIS,
        diagnosis="first failure",
    )
    fail_second = _StubRecipe(
        "fail_second",
        applies=True,
        outcome_result=RepairResult.FAILED_WITH_DIAGNOSIS,
        diagnosis="second failure",
    )

    outcome = try_pm_repair(_ctx(), recipes=[fail_first, fail_second])

    assert outcome.result == RepairResult.FAILED_WITH_DIAGNOSIS
    assert outcome.recipe_name == "fail_first"
    # Both run because a later recipe might still REPAIR.
    assert fail_first.attempts == 1
    assert fail_second.attempts == 1


def test_orchestrator_continues_past_failed_with_diagnosis_to_repair() -> None:
    fail = _StubRecipe(
        "fail",
        applies=True,
        outcome_result=RepairResult.FAILED_WITH_DIAGNOSIS,
        diagnosis="too bad",
    )
    repair = _StubRecipe("repair", applies=True, outcome_result=RepairResult.REPAIRED)

    outcome = try_pm_repair(_ctx(), recipes=[fail, repair])

    assert outcome.result == RepairResult.REPAIRED
    assert outcome.recipe_name == "repair"


def test_orchestrator_returns_not_applicable_when_no_recipe_matches() -> None:
    skip = _StubRecipe("skip", applies=False, outcome_result=RepairResult.REPAIRED)

    outcome = try_pm_repair(_ctx(), recipes=[skip])

    assert outcome.result == RepairResult.NOT_APPLICABLE
    assert outcome.recipe_name == ""


def test_orchestrator_swallows_recipe_exceptions() -> None:
    class _BoomRecipe:
        name = "boom"

        def applies_to(self, ctx: BlockerContext) -> bool:  # noqa: ARG002
            raise RuntimeError("intentional")

        def attempt(self, ctx: BlockerContext) -> RepairOutcome:
            raise AssertionError("should not be called")

    fallback = _StubRecipe(
        "fallback", applies=True, outcome_result=RepairResult.REPAIRED
    )

    outcome = try_pm_repair(_ctx(), recipes=[_BoomRecipe(), fallback])

    assert outcome.result == RepairResult.REPAIRED
    assert outcome.recipe_name == "fallback"
