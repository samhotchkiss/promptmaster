"""Regression checks for the maintained work-service spec."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "docs" / "work-service-spec.md"
WORKER_ONBOARDING_PATH = PROJECT_ROOT / "docs" / "worker-onboarding.md"
WORKER_GUIDE_PATH = PROJECT_ROOT / "docs" / "worker-guide.md"


def test_work_service_spec_matches_current_runtime_shape():
    text = SPEC_PATH.read_text(encoding="utf-8")

    assert "in-process library" in text
    assert "work-service.sock" not in text
    assert "`list_tasks`" in text
    assert "ValidationResult" not in text
    assert "list[GateResult]" in text
    assert "one-way push only" in text
    assert "poll_inbound" not in text
    assert "work_service.move(" not in text
    assert "sync_status(task_id)" in text
    assert "`pm task sync-status`" not in text
    assert "`total_cost_usd`" not in text


def test_worker_assignment_docs_describe_auto_claim_lifecycle():
    """Guard against drift back to the manual tmux-poke worker model.

    After #919/#921/#922/#923/#936 the shipped flow is per-task, auto-claimed:
    `pm task claim` provisions the per-task tmux window, the heartbeat sweep
    force-pushes the kickoff, and Polly auto-claims her own queued
    worker-role tasks. The docs must not reinstate the old "supervisor pokes
    pete via tmux" / long-lived `worker_<project>` shell narrative.
    """

    spec = SPEC_PATH.read_text(encoding="utf-8")
    onboarding = WORKER_ONBOARDING_PATH.read_text(encoding="utf-8")
    guide = WORKER_GUIDE_PATH.read_text(encoding="utf-8")

    # Stale narrative from the pre-auto-claim era must not reappear.
    for stale in (
        "supervisor pokes pete via tmux",
        "The tmux poke is the only",
        "Supervisor tmux pokes",
        "Work service transitions + tmux pokes",
    ):
        assert stale not in spec, f"stale phrase reappeared in spec: {stale!r}"

    # The current shape must be described in the spec.
    assert "auto-claim" in spec.lower()
    assert "heartbeat sweep" in spec
    assert "per-task tmux window" in spec or "per-task tmux" in spec

    # Worker onboarding must describe the auto-claim/kickoff lifecycle, not
    # tell the reader to wait for a manual poke.
    assert "auto-claim" in onboarding.lower()
    assert "heartbeat sweep" in onboarding
    assert ".pollypm-task-prompt.md" in onboarding

    # The worker guide must steer workers away from polling for work and away
    # from poking themselves with tmux send-keys when the kickoff is delayed.
    assert "auto-claim" in guide.lower()
    assert "heartbeat sweep" in guide
