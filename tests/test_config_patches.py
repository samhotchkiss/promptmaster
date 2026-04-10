from pathlib import Path

from pollypm.config_patches import (
    detect_preference_patch,
    list_project_overrides,
    remove_project_override,
    write_plugin_override,
)


def test_detect_preference_patch_routes_to_expected_override_type(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()

    rule = detect_preference_patch(project_root, "I don't want to run unit tests before every commit")
    plugin = detect_preference_patch(project_root, "Stop using that plugin selection")
    setting = detect_preference_patch(project_root, "I don't like this default persona")

    assert rule is not None and rule.kind == "rule"
    assert plugin is not None and plugin.kind == "plugin_selection"
    assert setting is not None and setting.kind == "project_setting"


def test_plugin_override_is_listed_and_removable(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()

    plugin_dir = write_plugin_override(
        project_root,
        "demo_override",
        "from pollypm.plugin_api.v1 import PollyPMPlugin\nplugin = PollyPMPlugin(name='demo_override')\n",
    )

    overrides = list_project_overrides(project_root)
    assert plugin_dir / "pollypm-plugin.toml" in overrides

    remove_project_override(project_root, "plugin", "demo_override")
    assert not plugin_dir.exists()
