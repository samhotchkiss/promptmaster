from pathlib import Path

from pollypm.rules import discover_rules, render_rules_manifest


def test_discover_rules_includes_packaged_defaults(tmp_path: Path) -> None:
    rules = discover_rules(tmp_path)

    assert {"bugfix", "build", "audit"} <= set(rules)
    assert rules["bugfix"].description == "Specialized bug fixing process"


def test_discover_rules_respects_override_hierarchy(monkeypatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    (fake_home / ".pollypm" / "rules").mkdir(parents=True)
    (fake_home / ".pollypm" / "rules" / "bugfix.md").write_text(
        "Description: User bugfix\nTrigger: when user wants custom bugfixing\n"
    )
    (tmp_path / ".pollypm" / "rules").mkdir(parents=True)
    (tmp_path / ".pollypm" / "rules" / "bugfix.md").write_text(
        "Description: Project bugfix\nTrigger: when project wants bugfixing\n"
    )
    (tmp_path / ".pollypm" / "rules" / "deploy.md").write_text(
        "Description: Deploy instructions\nTrigger: when deploying\n"
    )

    rules = discover_rules(tmp_path)

    assert rules["bugfix"].description == "Project bugfix"
    assert rules["bugfix"].display_path == ".pollypm/rules/bugfix.md"
    assert rules["deploy"].description == "Deploy instructions"


def test_rules_manifest_lists_merged_rules(monkeypatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    (tmp_path / ".pollypm" / "rules").mkdir(parents=True)
    (tmp_path / ".pollypm" / "rules" / "audit.md").write_text(
        "Description: Project audit flow\nTrigger: when reviewing code in this project\n"
    )

    manifest = render_rules_manifest(tmp_path)

    assert "## Available Rules" in manifest
    assert "- audit: Project audit flow -> .pollypm/rules/audit.md (when reviewing code in this project)" in manifest
    assert "- build: Feature building process -> pollypm/defaults/rules/build.md" in manifest
