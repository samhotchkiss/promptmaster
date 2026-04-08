from pathlib import Path

from promptmaster.models import ProviderKind
from promptmaster.onboarding import _run_login_window


def test_run_login_window_outside_tmux_uses_non_persistent_temp_session(tmp_path: Path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeTmux:
        def current_session_name(self):
            return None

        def has_session(self, name: str) -> bool:
            calls.setdefault("has_session", []).append(name)
            return False

        def kill_session(self, name: str) -> None:
            calls["killed"] = name

        def create_session(self, name: str, window_name: str, command: str, *, remain_on_exit: bool = True) -> None:
            calls["created"] = (name, window_name, command, remain_on_exit)

        def attach_session(self, name: str) -> int:
            calls["attached"] = name
            return 0

    pane_text = _run_login_window(
        FakeTmux(),
        provider=ProviderKind.CLAUDE,
        home=tmp_path / "claude-home",
        window_label="onboard-claude-1",
        quiet=True,
    )

    assert pane_text == ""
    assert calls["created"][0] == "promptmaster-login-onboard-claude-1"
    assert calls["created"][3] is False
    assert calls["attached"] == "promptmaster-login-onboard-claude-1"
