"""Tests for the issue #963 two-phase tmux session launch contract.

Old flow: ``tmux new-window -d 'claude --resume X'`` — pane materializes
already running the slow agent CLI bootstrap, leaving the user staring
at a blank pane for several seconds.

New flow:
1. ``tmux new-window -d`` (no command) — pane shows a normal shell
   prompt instantly.
2. ``tmux send-keys -t <pane> '<launch cmd>' Enter`` — the user sees
   the launch command typed into the prompt and the agent CLI come up.

These tests pin down both phases at the ``TmuxClient`` boundary and
through ``TmuxSessionService.create`` (the path used by the per-task
worker spawn, the supervisor recreate path, and the operator/heartbeat
boot).
"""

from __future__ import annotations

import subprocess

from pollypm.tmux.client import TmuxClient


def _capture_run(monkeypatch, *, pane_id: str = "%42") -> list[list[str]]:
    """Install a fake ``subprocess.run`` that records every tmux call.

    Returns the list (mutated in place by the recorder) so tests can
    walk through the command sequence.
    """
    captured: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured.append(list(args))

        class Result:
            returncode = 0
            stdout = pane_id
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    return captured


# ---------------------------------------------------------------------------
# Phase 1 — empty pane creation
# ---------------------------------------------------------------------------


def test_phase1_create_window_does_not_carry_command(monkeypatch) -> None:
    """Phase 1: ``new-window`` invocation lacks the launch command.

    The pane must materialize as a default shell — no agent CLI banner,
    no slow bootstrap. The launch command lives in Phase 2.
    """
    captured = _capture_run(monkeypatch)
    client = TmuxClient()
    monkeypatch.setattr(client, "has_session", lambda name: False)

    client.create_window(
        "storage", "task-acme-7", "claude --resume long-id", detached=True,
    )

    new_window_calls = [c for c in captured if "new-window" in c]
    assert len(new_window_calls) == 1
    new_window = new_window_calls[0]
    # The launch command must not appear anywhere in the new-window call.
    assert "claude --resume long-id" not in new_window
    assert "claude" not in new_window
    # tmux must be detached (-d) so the pane materializes without
    # stealing focus from whatever pane the user is already viewing.
    assert "-d" in new_window
    # tmux must capture the new pane's id (-P -F '#{pane_id}') so the
    # caller can target Phase 2 by stable id.
    assert "-P" in new_window
    assert "#{pane_id}" in new_window


def test_phase1_create_session_does_not_carry_command(monkeypatch) -> None:
    """Same Phase 1 contract for ``new-session`` (used at bootstrap)."""
    captured = _capture_run(monkeypatch, pane_id="%99")
    client = TmuxClient()
    monkeypatch.setattr(client, "has_session", lambda name: False)

    client.create_session("storage", "main", "codex --foo")

    new_session_calls = [c for c in captured if "new-session" in c]
    assert len(new_session_calls) == 1
    assert "codex --foo" not in new_session_calls[0]
    assert "codex" not in new_session_calls[0]


# ---------------------------------------------------------------------------
# Phase 2 — launch command delivered via send-keys
# ---------------------------------------------------------------------------


def test_phase2_send_keys_delivers_launch_command(monkeypatch) -> None:
    """Phase 2: the launch command is typed via ``send-keys`` and Enter."""
    captured = _capture_run(monkeypatch)
    client = TmuxClient()
    monkeypatch.setattr(client, "has_session", lambda name: False)

    client.create_window("storage", "task-acme-7", "claude --resume long-id")

    send_keys_calls = [c for c in captured if "send-keys" in c]
    assert len(send_keys_calls) == 1
    send_keys = send_keys_calls[0]
    assert "claude --resume long-id" in send_keys
    # Enter must be the last argument so the command actually runs.
    assert send_keys[-1] == "Enter"


def test_phase2_targets_pane_id_when_available(monkeypatch) -> None:
    """Phase 2 targets the pane_id returned by Phase 1, not the
    ``session:window`` string.

    Pane ids are stable across rename/move; window-name targets resolve
    through tmux at call time and can race when other windows of the
    same name exist (the bug pattern that motivated #934).
    """
    captured = _capture_run(monkeypatch, pane_id="%77")
    client = TmuxClient()
    monkeypatch.setattr(client, "has_session", lambda name: False)

    pane_id = client.create_window("storage", "task-foo-1", "claude --resume X")
    assert pane_id == "%77"

    send_keys = next(c for c in captured if "send-keys" in c)
    # ``-t %77`` must appear: the pane_id is the target, not "storage:task-foo-1".
    assert "-t" in send_keys
    target_idx = send_keys.index("-t")
    assert send_keys[target_idx + 1] == "%77"


def test_phase2_skipped_when_window_already_exists(monkeypatch) -> None:
    """Idempotency guard: re-running ``create_window`` for an existing
    window must NOT re-send the launch command (otherwise we'd type a
    second ``claude --resume`` into an already-bootstrapped pane).
    """
    from pollypm.tmux.client import TmuxWindow

    captured = _capture_run(monkeypatch)
    client = TmuxClient()
    monkeypatch.setattr(client, "has_session", lambda name: True)
    monkeypatch.setattr(
        client,
        "list_windows",
        lambda name: [TmuxWindow(
            session=name, index=0, name="task-foo-1", active=True,
            pane_id="%5", pane_current_command="bash",
            pane_current_path="/tmp", pane_dead=False,
        )],
    )

    result = client.create_window("storage", "task-foo-1", "claude --resume X")
    assert result is None
    assert not any("send-keys" in c for c in captured)
    assert not any("new-window" in c for c in captured)


def test_phase2_skipped_when_session_already_exists(monkeypatch) -> None:
    """Same idempotency contract for ``create_session``."""
    captured = _capture_run(monkeypatch)
    client = TmuxClient()
    monkeypatch.setattr(client, "has_session", lambda name: True)

    result = client.create_session("storage", "main", "claude --resume X")
    assert result is None
    assert not any("send-keys" in c for c in captured)
    assert not any("new-session" in c for c in captured)


# ---------------------------------------------------------------------------
# Backwards compatibility — explicit two_phase=False still inlines
# ---------------------------------------------------------------------------


def test_two_phase_false_inlines_command(monkeypatch) -> None:
    """The legacy single-call form is still available behind
    ``two_phase=False`` for non-agent callers (e.g. ``recover_session``
    which respawns a pane scoped to the lifetime of one command).
    """
    captured = _capture_run(monkeypatch)
    client = TmuxClient()
    monkeypatch.setattr(client, "has_session", lambda name: False)

    client.create_window("storage", "win", "echo hello", two_phase=False)
    new_window = next(c for c in captured if "new-window" in c)
    assert "echo hello" in new_window
    assert not any("send-keys" in c for c in captured)


# ---------------------------------------------------------------------------
# Integration: TmuxSessionService.create routes through two-phase
# ---------------------------------------------------------------------------


def test_session_service_create_uses_two_phase(monkeypatch, tmp_path) -> None:
    """End-to-end: ``TmuxSessionService.create`` (the path the per-task
    worker spawn, supervisor reconcile, and account-switch flows all
    funnel through) drives the two-phase contract.

    We mock at the ``subprocess.run`` boundary so this exercises the
    real ``TmuxClient.create_window`` code path.
    """
    from pollypm.models import (
        AccountConfig,
        PollyPMConfig,
        PollyPMSettings,
        ProjectSettings,
        ProviderKind,
    )
    from pollypm.session_services.tmux import TmuxSessionService

    base_dir = tmp_path / ".pollypm"
    config = PollyPMConfig(
        project=ProjectSettings(
            name="Fixture",
            root_dir=tmp_path,
            tmux_session="pollypm",
            base_dir=base_dir,
            logs_dir=base_dir / "logs",
            snapshots_dir=base_dir / "snapshots",
            state_db=base_dir / "state.db",
        ),
        pollypm=PollyPMSettings(controller_account="acct"),
        accounts={
            "acct": AccountConfig(name="acct", provider=ProviderKind.CLAUDE),
        },
        sessions={},
    )

    # Minimal store stub.
    class _Store:
        def list_sessions(self) -> list:
            return []

    captured: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured.append(list(args))

        class Result:
            returncode = 0
            # has-session -> 1 (no), list-windows -> empty
            stdout = "%21" if args and args[0] == "tmux" and "new-window" in args else ""
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    service = TmuxSessionService(config=config, store=_Store())
    # Force ``has_session`` to True so the create_session branch isn't
    # taken (we want to hit create_window, the more common path).
    monkeypatch.setattr(service.tmux, "has_session", lambda name: True)
    # Skip stabilization (we're not testing provider readiness here).
    monkeypatch.setattr(service, "_stabilize", lambda *a, **kw: None)
    # Skip _find_window so the pre-existing-window short-circuit doesn't
    # fire — the test wants to exercise the actual two-phase create.
    monkeypatch.setattr(service, "_find_window", lambda s, w: None)
    # Skip pipe-pane and history-limit (they hit subprocess too but are
    # noise for this test — they're already exercised by other tests).
    monkeypatch.setattr(service.tmux, "pipe_pane", lambda *a, **kw: None)
    monkeypatch.setattr(service.tmux, "set_pane_history_limit", lambda *a, **kw: None)
    monkeypatch.setattr(service.tmux, "set_window_option", lambda *a, **kw: None)

    handle = service.create(
        name="task-acme-7",
        provider="claude",
        account="acct",
        cwd=tmp_path,
        command="claude --resume xyz",
        window_name="task-acme-7",
        tmux_session="pollypm-storage-closet",
        stabilize=False,
    )
    assert handle.window_name == "task-acme-7"

    # The two phases must be present in the recorded subprocess calls.
    new_window_calls = [c for c in captured if "new-window" in c]
    send_keys_calls = [c for c in captured if "send-keys" in c]
    assert new_window_calls, "expected a new-window call from TmuxClient.create_window"
    assert send_keys_calls, "expected a send-keys call delivering the launch command"
    # Phase 1: launch command must not appear in new-window.
    assert "claude --resume xyz" not in new_window_calls[0]
    # Phase 2: launch command + Enter must appear in send-keys.
    sk = send_keys_calls[0]
    assert "claude --resume xyz" in sk
    assert sk[-1] == "Enter"
