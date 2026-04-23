import pytest

from pathlib import Path

from pollypm.models import KnownProject, ModelAssignment, ProjectConfig, ProjectKind


def test_known_project_display_label_prefers_name_without_persona() -> None:
    # Persona is only shown in the PM Chat label, not the project label
    project = KnownProject(
        key="demo",
        path=Path("/tmp/demo"),
        name="Demo",
        persona_name="Dora",
        kind=ProjectKind.GIT,
    )

    assert project.display_label() == "Demo"


def test_known_project_display_label_falls_back_to_key() -> None:
    project = KnownProject(
        key="demo",
        path=Path("/tmp/demo"),
        kind=ProjectKind.FOLDER,
    )

    assert project.display_label() == "demo"


def test_model_assignment_requires_exactly_one_variant() -> None:
    assert ModelAssignment(alias="opus-4.7") == ModelAssignment(alias="opus-4.7")
    assert ModelAssignment(provider="claude", model="claude-opus-4-7") == ModelAssignment(
        provider="claude",
        model="claude-opus-4-7",
    )

    with pytest.raises(ValueError):
        ModelAssignment()

    with pytest.raises(ValueError):
        ModelAssignment(alias="opus-4.7", provider="claude", model="claude-opus-4-7")


def test_project_config_alias_exposes_role_assignments_default() -> None:
    project = ProjectConfig(key="demo", path=Path("/tmp/demo"))

    assert project.role_assignments == {}
