from pathlib import Path

import pytest

from promptmaster.accounts import add_account_via_login
from promptmaster.config import write_config
from promptmaster.models import (
    AccountConfig,
    PromptMasterConfig,
    PromptMasterSettings,
    ProjectSettings,
    ProviderKind,
)


def _config(tmp_path: Path) -> PromptMasterConfig:
    return PromptMasterConfig(
        project=ProjectSettings(
            root_dir=tmp_path,
            base_dir=tmp_path / ".promptmaster",
            logs_dir=tmp_path / ".promptmaster/logs",
            snapshots_dir=tmp_path / ".promptmaster/snapshots",
            state_db=tmp_path / ".promptmaster/state.db",
        ),
        promptmaster=PromptMasterSettings(controller_account="", failover_enabled=False),
        accounts={},
        sessions={},
        projects={},
    )


def test_add_account_reuses_orphaned_home_with_same_email(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "promptmaster.toml"
    write_config(_config(tmp_path), config_path)
    orphan_home = tmp_path / ".promptmaster" / "homes" / "claude_s_example_com"
    orphan_home.mkdir(parents=True, exist_ok=True)
    (orphan_home / "stale.txt").write_text("keep me")

    def fake_login_window(_tmux, provider, home, window_label):  # noqa: ANN001
        home.mkdir(parents=True, exist_ok=True)
        (home / "fresh.txt").write_text("fresh")
        return "done"

    def fake_detect(provider, home):  # noqa: ANN001
        if home.name == "claude_s_example_com":
            return "s@example.com"
        if home.name.startswith("ad-hoc-claude"):
            return "s@example.com"
        return None

    monkeypatch.setattr("promptmaster.accounts._run_login_window", fake_login_window)
    monkeypatch.setattr("promptmaster.accounts._detect_account_email", fake_detect)
    monkeypatch.setattr("promptmaster.accounts._prime_claude_home", lambda home: None)

    key, email = add_account_via_login(config_path, ProviderKind.CLAUDE)

    assert key == "claude_s_example_com"
    assert email == "s@example.com"
    assert orphan_home.exists()
    assert (orphan_home / "stale.txt").exists()
    assert not any(path.name.startswith("ad-hoc-claude") for path in (tmp_path / ".promptmaster" / "homes").iterdir())


def test_add_account_replaces_orphaned_home_when_stale(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "promptmaster.toml"
    write_config(_config(tmp_path), config_path)
    orphan_home = tmp_path / ".promptmaster" / "homes" / "claude_s_example_com"
    orphan_home.mkdir(parents=True, exist_ok=True)
    (orphan_home / "stale.txt").write_text("stale")

    def fake_login_window(_tmux, provider, home, window_label):  # noqa: ANN001
        home.mkdir(parents=True, exist_ok=True)
        (home / "fresh.txt").write_text("fresh")
        return "done"

    def fake_detect(provider, home):  # noqa: ANN001
        if home.name == "claude_s_example_com":
            return None
        if home.name.startswith("ad-hoc-claude"):
            return "s@example.com"
        return None

    monkeypatch.setattr("promptmaster.accounts._run_login_window", fake_login_window)
    monkeypatch.setattr("promptmaster.accounts._detect_account_email", fake_detect)
    monkeypatch.setattr("promptmaster.accounts._prime_claude_home", lambda home: None)

    key, _email = add_account_via_login(config_path, ProviderKind.CLAUDE)

    assert key == "claude_s_example_com"
    assert orphan_home.exists()
    assert (orphan_home / "fresh.txt").exists()
    assert not (orphan_home / "stale.txt").exists()


def test_add_account_rejects_duplicate_configured_account(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "promptmaster.toml"
    config = _config(tmp_path)
    config.accounts["claude_s_example_com"] = AccountConfig(
        name="claude_s_example_com",
        provider=ProviderKind.CLAUDE,
        email="s@example.com",
        home=tmp_path / ".promptmaster" / "homes" / "claude_s_example_com",
    )
    write_config(config, config_path)

    def fake_login_window(_tmux, provider, home, window_label):  # noqa: ANN001
        home.mkdir(parents=True, exist_ok=True)
        return "done"

    monkeypatch.setattr("promptmaster.accounts._run_login_window", fake_login_window)
    monkeypatch.setattr("promptmaster.accounts._detect_account_email", lambda provider, home: "s@example.com")

    with pytest.raises(Exception, match="already exists"):
        add_account_via_login(config_path, ProviderKind.CLAUDE)
