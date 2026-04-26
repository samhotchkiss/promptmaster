import subprocess

from pollypm.tmux.client import TmuxClient


def test_current_session_name_returns_none_outside_tmux(monkeypatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    client = TmuxClient()

    assert client.current_session_name() is None


def test_current_window_index_returns_none_outside_tmux(monkeypatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    client = TmuxClient()

    assert client.current_window_index() is None


def test_current_pane_id_returns_none_outside_tmux(monkeypatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    client = TmuxClient()

    assert client.current_pane_id() is None


def test_new_session_attached_invokes_tmux(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(args, check=False):
        captured["args"] = args
        captured["check"] = check

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    client = TmuxClient()

    result = client.new_session_attached("pollypm-onboarding", "onboarding", "echo hello")

    assert result == 0
    assert captured["check"] is False
    assert captured["args"] == [
        "tmux",
        "new-session",
        "-A",
        "-s",
        "pollypm-onboarding",
        "-n",
        "onboarding",
        "echo hello",
    ]


def test_run_check_false_returns_124_on_timeout(monkeypatch) -> None:
    """A wedged tmux server must not crash callers using ``check=False``.

    Sam's screenshot 2026-04-26 14:08 PM showed ``tmux has-session -t
    pollypm`` hanging past the 15s timeout and propagating
    ``subprocess.TimeoutExpired`` all the way up to ``pm``, crashing
    the CLI. ``has_session`` and friends use ``check=False`` precisely
    so they can interpret the returncode — they should see a non-zero
    result on timeout, not an exception.
    """

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 15))

    monkeypatch.setattr("subprocess.run", fake_run)
    client = TmuxClient()

    result = client.run("has-session", "-t", "pollypm", check=False)
    assert result.returncode == 124
    assert "timed out" in result.stderr


def test_has_session_returns_false_when_tmux_hangs(monkeypatch) -> None:
    """``has_session`` returns False (not raises) when tmux is wedged."""

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 15))

    monkeypatch.setattr("subprocess.run", fake_run)
    client = TmuxClient()

    assert client.has_session("pollypm") is False


def test_send_keys_long_text_uses_per_call_named_buffer(monkeypatch, tmp_path) -> None:
    """#808: long sends must use a per-call named tmux paste buffer so
    two concurrent sends can't paste each other's text. Two
    interleaved long sends should hit two distinct buffer names and
    each load-buffer must pair with a paste-buffer that names the
    same buffer.
    """
    captured: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured.append(list(args))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    client = TmuxClient()

    long_a = "A" * 200
    long_b = "B" * 200

    # Simulate two concurrent senders by interleaving manually — both
    # sends must end up with their own buffer name, never the unnamed
    # default.
    client.send_keys("pollypm:0.1", long_a, press_enter=False)
    client.send_keys("pollypm:0.2", long_b, press_enter=False)

    load_calls = [args for args in captured if "load-buffer" in args]
    paste_calls = [args for args in captured if "paste-buffer" in args]

    assert len(load_calls) == 2
    assert len(paste_calls) == 2
    # Every load and every paste must carry an explicit ``-b <name>``
    # option — the unnamed global buffer is what made cross-talk
    # possible.
    for call in load_calls + paste_calls:
        assert "-b" in call, f"expected -b in {call!r}"
        idx = call.index("-b")
        assert call[idx + 1].startswith("pollypm-"), call

    # The two sends must use distinct buffer names so concurrent
    # callers can't see each other's text.
    def _buffer_name(call: list[str]) -> str:
        return call[call.index("-b") + 1]

    load_names = [_buffer_name(c) for c in load_calls]
    paste_names = [_buffer_name(c) for c in paste_calls]
    assert load_names[0] != load_names[1]
    # Each paste must reference the same name as its preceding load.
    assert paste_names == load_names


def test_run_check_true_still_raises_on_timeout(monkeypatch) -> None:
    """``check=True`` callers opted into propagation; preserve that contract."""

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 15))

    monkeypatch.setattr("subprocess.run", fake_run)
    client = TmuxClient()

    try:
        client.run("kill-server", check=True)
    except subprocess.TimeoutExpired:
        return
    raise AssertionError("expected TimeoutExpired to propagate when check=True")
