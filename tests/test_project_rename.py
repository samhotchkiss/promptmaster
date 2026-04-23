"""Tests for ``pm project rename`` (#766)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pollypm.plugins_builtin.project_planning.cli.project import project_app
from pollypm.projects import rename_project


runner = CliRunner()


def _write_config(
    config_path: Path,
    *,
    workspace_root: Path,
    project_path: Path,
    slug: str,
    extra_sessions: str = "",
) -> None:
    config_path.write_text(
        "[project]\n"
        'tmux_session = "pollypm-test"\n'
        f'workspace_root = "{workspace_root}"\n'
        "\n"
        f'[projects.{slug}]\n'
        f'key = "{slug}"\n'
        'name = "Demo"\n'
        f'path = "{project_path}"\n'
        f"{extra_sessions}"
    )


@pytest.fixture
def env(tmp_path: Path) -> dict:
    workspace_root = tmp_path / "dev"
    workspace_root.mkdir()
    project_path = workspace_root / "old_demo"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    config_path = tmp_path / "pollypm.toml"
    _write_config(
        config_path,
        workspace_root=workspace_root,
        project_path=project_path,
        slug="old_demo",
        extra_sessions=(
            "[sessions.architect_old_demo]\n"
            'role = "architect"\n'
            'provider = "claude"\n'
            'account = "claude_main"\n'
            'cwd = "."\n'
            'project = "old_demo"\n'
            'window_name = "architect-old_demo"\n'
            "\n"
            "[accounts.claude_main]\n"
            'provider = "claude"\n'
            'home = "/tmp/claude_home"\n'
        ),
    )
    return {
        "config_path": config_path,
        "project_path": project_path,
        "workspace_root": workspace_root,
    }


def _load_cfg(config_path: Path):
    from pollypm.config import load_config
    return load_config(config_path)


# --------------------------------------------------------------------------
# rename_project (library)
# --------------------------------------------------------------------------


def test_rename_project_moves_config_entry(env) -> None:
    renamed, warnings = rename_project(env["config_path"], "old_demo", "new_demo")
    assert renamed.key == "new_demo"
    config = _load_cfg(env["config_path"])
    assert "new_demo" in config.projects
    assert "old_demo" not in config.projects


def test_rename_project_updates_session_references(env) -> None:
    rename_project(env["config_path"], "old_demo", "new_demo")
    config = _load_cfg(env["config_path"])
    # The session's project field now points to the new key.
    session = config.sessions["architect_old_demo"]
    assert session.project == "new_demo"


def test_rename_project_warns_about_live_state_not_touched(env) -> None:
    _, warnings = rename_project(env["config_path"], "old_demo", "new_demo")
    text = "\n".join(warnings)
    # Session name still contains old slug.
    assert "architect_old_demo" in text
    # Tmux window mentions old slug.
    assert "architect-old_demo" in text


def test_rename_project_dry_run_does_not_mutate(env) -> None:
    before = _load_cfg(env["config_path"]).projects
    renamed, _ = rename_project(
        env["config_path"], "old_demo", "new_demo", dry_run=True,
    )
    after = _load_cfg(env["config_path"]).projects
    assert "old_demo" in after
    assert "new_demo" not in after
    # Returned project reflects the existing old entry when dry-run.
    assert renamed.key == "old_demo"


def test_rename_project_rejects_unknown_old_slug(env) -> None:
    import typer
    with pytest.raises(typer.BadParameter):
        rename_project(env["config_path"], "does_not_exist", "whatever")


def test_rename_project_rejects_collision(env) -> None:
    import typer
    # Inject a second project at the target key.
    from pollypm.config import load_config, write_config
    from pollypm.models import KnownProject, ProjectKind
    config = load_config(env["config_path"])
    other = env["workspace_root"] / "other"
    other.mkdir()
    config.projects["other"] = KnownProject(
        key="other", path=other, name="Other", kind=ProjectKind.FOLDER,
    )
    write_config(config, env["config_path"], force=True)

    with pytest.raises(typer.BadParameter):
        rename_project(env["config_path"], "old_demo", "other")


def test_rename_project_rejects_non_canonical_slug(env) -> None:
    import typer
    # Uppercase, hyphen, or whitespace — all reject with a hint.
    with pytest.raises(typer.BadParameter):
        rename_project(env["config_path"], "old_demo", "New-Demo")


def test_rename_project_rejects_same_slug(env) -> None:
    import typer
    with pytest.raises(typer.BadParameter):
        rename_project(env["config_path"], "old_demo", "old_demo")


# --------------------------------------------------------------------------
# CLI (``pm project rename``)
# --------------------------------------------------------------------------


def test_cli_rename_happy_path(env) -> None:
    result = runner.invoke(
        project_app,
        ["rename", "old_demo", "new_demo", "--config", str(env["config_path"])],
    )
    assert result.exit_code == 0, result.output
    assert "Renamed 'old_demo' → 'new_demo'" in result.output

    config = _load_cfg(env["config_path"])
    assert "new_demo" in config.projects
    assert "old_demo" not in config.projects


def test_cli_rename_dry_run_prints_preview(env) -> None:
    result = runner.invoke(
        project_app,
        ["rename", "old_demo", "new_demo", "--dry-run",
         "--config", str(env["config_path"])],
    )
    assert result.exit_code == 0, result.output
    assert "Would rename 'old_demo' → 'new_demo'" in result.output
    assert "Re-run without --dry-run" in result.output

    config = _load_cfg(env["config_path"])
    assert "old_demo" in config.projects  # unchanged


def test_cli_rename_collision_errors_cleanly(env) -> None:
    # Create another project to collide with.
    from pollypm.config import load_config, write_config
    from pollypm.models import KnownProject, ProjectKind
    config = load_config(env["config_path"])
    other = env["workspace_root"] / "other"
    other.mkdir()
    config.projects["other"] = KnownProject(
        key="other", path=other, name="Other", kind=ProjectKind.FOLDER,
    )
    write_config(config, env["config_path"], force=True)

    result = runner.invoke(
        project_app,
        ["rename", "old_demo", "other", "--config", str(env["config_path"])],
    )
    assert result.exit_code == 1, result.output
    assert "already exists" in result.output.lower() or "already" in result.output.lower()
