import json
from pathlib import Path

from pollypm.config import write_config
from pollypm.knowledge_extract import extract_knowledge_once
from pollypm.models import AccountConfig, KnownProject, PollyPMConfig, PollyPMSettings, ProjectKind, ProjectSettings, ProviderKind, SessionConfig


def _config(tmp_path: Path) -> PollyPMConfig:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return PollyPMConfig(
        project=ProjectSettings(
            name="PollyPM",
            root_dir=project_root,
            base_dir=project_root / ".pollypm-state",
            logs_dir=project_root / ".pollypm-state/logs",
            snapshots_dir=project_root / ".pollypm-state/snapshots",
            state_db=project_root / ".pollypm-state/state.db",
        ),
        pollypm=PollyPMSettings(controller_account="claude_main"),
        accounts={
            "claude_main": AccountConfig(
                name="claude_main",
                provider=ProviderKind.CLAUDE,
                home=project_root / ".pollypm-state" / "homes" / "claude_main",
            )
        },
        sessions={
            "heartbeat": SessionConfig(
                name="heartbeat",
                role="heartbeat-supervisor",
                provider=ProviderKind.CLAUDE,
                account="claude_main",
                cwd=project_root,
            ),
            "operator": SessionConfig(
                name="operator",
                role="operator-pm",
                provider=ProviderKind.CLAUDE,
                account="claude_main",
                cwd=project_root,
            ),
        },
        projects={
            "pollypm": KnownProject(
                key="pollypm",
                path=project_root,
                name="PollyPM",
                kind=ProjectKind.FOLDER,
            )
        },
    )


def test_knowledge_extraction_updates_docs_with_checkpointing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config_path = tmp_path / "pollypm.toml"
    write_config(config, config_path, force=True)

    transcript_dir = config.project.root_dir / ".pollypm" / "transcripts" / "session-a"
    transcript_dir.mkdir(parents=True)
    events_path = transcript_dir / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-10T06:00:00Z",
                        "event_type": "assistant_turn",
                        "session_id": "session-a",
                        "project_key": "pollypm",
                        "source_path": "source-a",
                        "source_offset": 1,
                        "payload": {"text": "Decision: we will split the deployment pipeline. Risk: staging tokens must stay out of docs."},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-10T06:01:00Z",
                        "event_type": "assistant_turn",
                        "session_id": "session-a",
                        "project_key": "pollypm",
                        "source_path": "source-a",
                        "source_offset": 2,
                        "payload": {"text": "Architecture: migrate the docs updater into a scheduled pipeline. Idea: consider a changelog digest."},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-10T06:02:00Z",
                        "event_type": "assistant_turn",
                        "session_id": "session-a",
                        "project_key": "pollypm",
                        "source_path": "source-a",
                        "source_offset": 3,
                        "payload": {"text": "Convention: always regenerate the Summary section after updates. Goal: prioritize transcript-derived docs."},
                    }
                ),
            ]
        )
        + "\n"
    )

    result = extract_knowledge_once(config)
    assert result == {"processed_events": 3, "updated_docs": 4}

    overview = (config.project.root_dir / "docs" / "project-overview.md").read_text()
    decisions = (config.project.root_dir / "docs" / "decisions.md").read_text()
    risks = (config.project.root_dir / "docs" / "risks.md").read_text()
    ideas = (config.project.root_dir / "docs" / "ideas.md").read_text()

    assert "## Summary" in overview
    assert "Goals: Goal: prioritize transcript-derived docs." in overview
    assert "## Architecture Changes" in overview
    assert "migrate the docs updater into a scheduled pipeline." in overview
    assert "## Convention Shifts" in overview
    assert "always regenerate the Summary section after updates." in overview
    assert "## Decisions" in decisions
    assert "Decision: we will split the deployment pipeline." in decisions
    assert "[redacted-secret]" not in risks
    assert "Risk: staging tokens must stay out of docs." in risks
    assert "Idea: consider a changelog digest." in ideas

    decisions_path = config.project.root_dir / "docs" / "decisions.md"
    decisions_path.write_text(decisions + "\n## Notes\nKeep this manual note.\n")
    before = decisions_path.read_text()
    second = extract_knowledge_once(config)
    assert second == {"processed_events": 0, "updated_docs": 0}
    assert decisions_path.read_text() == before

    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": "2026-04-10T06:03:00Z",
                    "event_type": "assistant_turn",
                    "session_id": "session-a",
                    "project_key": "pollypm",
                    "source_path": "source-a",
                    "source_offset": 4,
                    "payload": {"text": "Decision: use the cheap Haiku model for extraction, not Opus."},
                }
            )
            + "\n"
        )

    third = extract_knowledge_once(config)
    assert third == {"processed_events": 1, "updated_docs": 1}
    updated_decisions = decisions_path.read_text()
    assert "Decision: use the cheap Haiku model for extraction, not Opus." in updated_decisions
    assert "## Notes\nKeep this manual note." in updated_decisions
