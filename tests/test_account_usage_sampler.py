from __future__ import annotations

from pathlib import Path

from pollypm.account_usage_sampler import (
    AccountUsageSample,
    refresh_account_usage,
    refresh_all_account_usage,
)
from pollypm.config import write_config
from pollypm.models import (
    AccountConfig,
    PollyPMConfig,
    PollyPMSettings,
    ProjectSettings,
    ProviderKind,
    RuntimeKind,
)
from pollypm.provider_sdk import ProviderUsageSnapshot
from pollypm.storage.state import StateStore


class _FakeTmux:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.killed: list[str] = []
        self._sessions: set[str] = set()

    def create_session(self, name: str, window_name: str, command: str) -> None:
        self.created.append((name, window_name, command))
        self._sessions.add(name)

    def has_session(self, name: str) -> bool:
        return name in self._sessions

    def kill_session(self, name: str) -> None:
        self.killed.append(name)
        self._sessions.discard(name)


def _config(tmp_path: Path) -> tuple[Path, PollyPMConfig]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    config = PollyPMConfig(
        project=ProjectSettings(
            name="TestProject",
            root_dir=project_root,
            base_dir=project_root / ".pollypm",
            logs_dir=project_root / ".pollypm/logs",
            snapshots_dir=project_root / ".pollypm/snapshots",
            state_db=project_root / ".pollypm/state.db",
        ),
        pollypm=PollyPMSettings(controller_account="claude_primary"),
        accounts={
            "claude_primary": AccountConfig(
                name="claude_primary",
                provider=ProviderKind.CLAUDE,
                runtime=RuntimeKind.LOCAL,
                home=project_root / ".pollypm" / "homes" / "claude_primary",
            ),
            "codex_backup": AccountConfig(
                name="codex_backup",
                provider=ProviderKind.CODEX,
                runtime=RuntimeKind.LOCAL,
                home=project_root / ".pollypm" / "homes" / "codex_backup",
            ),
        },
        sessions={},
        projects={},
    )
    config_path = project_root / "pollypm.toml"
    write_config(config, config_path)
    return config_path, config


def test_refresh_account_usage_persists_structured_fields(monkeypatch, tmp_path: Path) -> None:
    config_path, config = _config(tmp_path)
    fake_tmux = _FakeTmux()

    monkeypatch.setattr(
        "pollypm.account_usage_sampler._build_probe_command",
        lambda *_args, **_kwargs: "probe-cmd",
    )
    monkeypatch.setattr(
        "pollypm.account_usage_sampler.collect_usage_snapshot",
        lambda *args, **kwargs: ProviderUsageSnapshot(
            plan="max",
            health="healthy",
            summary="79% left this week · resets Apr 10 at 1am",
            raw_text="Current week (all models)",
            used_pct=21,
            remaining_pct=79,
            reset_at="Apr 10 at 1am",
            period_label="current week",
        ),
    )

    sample = refresh_account_usage(
        config_path,
        "claude_primary",
        tmux_client=fake_tmux,
    )

    assert sample.remaining_pct == 79
    assert fake_tmux.created
    assert fake_tmux.killed == [fake_tmux.created[0][0]]

    with StateStore(config.project.state_db) as store:
        usage = store.get_account_usage("claude_primary")
    assert usage is not None
    assert usage.plan == "max"
    assert usage.used_pct == 21
    assert usage.remaining_pct == 79
    assert usage.reset_at == "Apr 10 at 1am"
    assert usage.period_label == "current week"


def test_refresh_account_usage_keeps_cached_shape_on_probe_failure(
    monkeypatch, tmp_path: Path,
) -> None:
    config_path, config = _config(tmp_path)
    with StateStore(config.project.state_db) as store:
        store.upsert_account_usage(
            account_name="claude_primary",
            provider="claude",
            plan="max",
            health="healthy",
            usage_summary="81% left this week",
            raw_text="old",
            used_pct=19,
            remaining_pct=81,
            reset_at="Apr 09 at 1am",
            period_label="current week",
        )

    monkeypatch.setattr(
        "pollypm.account_usage_sampler.collect_account_usage_sample",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Claude probe session is not authenticated.")
        ),
    )

    sample = refresh_account_usage(config_path, "claude_primary", tmux_client=_FakeTmux())

    assert sample.health == "auth-broken"
    assert "usage refresh failed" in sample.usage_summary
    assert sample.remaining_pct == 81

    with StateStore(config.project.state_db) as store:
        usage = store.get_account_usage("claude_primary")
    assert usage is not None
    assert usage.health == "auth-broken"
    assert usage.remaining_pct == 81
    assert usage.plan == "max"


def test_refresh_all_account_usage_continues_past_single_account_failure(
    monkeypatch, tmp_path: Path,
) -> None:
    config_path, _config_obj = _config(tmp_path)

    calls: list[str] = []

    def _recording_refresh(_config_path: Path, account_name: str, *, tmux_client=None):
        del _config_path, tmux_client
        calls.append(account_name)
        if account_name == "codex_backup":
            raise RuntimeError("boom")
        return AccountUsageSample(
            account_name=account_name,
            provider=ProviderKind.CLAUDE,
            plan="max",
            health="healthy",
            usage_summary="80% left",
            raw_text="",
        )

    monkeypatch.setattr(
        "pollypm.account_usage_sampler.refresh_account_usage",
        _recording_refresh,
    )

    samples = refresh_all_account_usage(config_path)

    assert calls == ["claude_primary", "codex_backup"]
    assert [sample.account_name for sample in samples] == ["claude_primary"]
