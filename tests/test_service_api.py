from pathlib import Path

from promptmaster.service_api import PromptMasterService


def test_service_create_and_launch_worker_uses_worker_api(monkeypatch, tmp_path: Path) -> None:
    service = PromptMasterService(tmp_path / "promptmaster.toml")
    calls: list[tuple[str, object]] = []

    class FakeSession:
        name = "worker_promptmaster"

    class FakeTmux:
        def has_session(self, _name: str) -> bool:
            return True

    class FakeConfigProject:
        tmux_session = "promptmaster"

    class FakeConfig:
        project = FakeConfigProject()

    class FakeSupervisor:
        tmux = FakeTmux()
        config = FakeConfig()

    monkeypatch.setattr(
        "promptmaster.service_api.create_worker_session",
        lambda config_path, project_key, prompt: calls.append(("create", project_key, prompt)) or FakeSession(),
    )
    monkeypatch.setattr(
        "promptmaster.service_api.launch_worker_session",
        lambda config_path, session_name: calls.append(("launch", session_name)),
    )
    monkeypatch.setattr(service, "load_supervisor", lambda: FakeSupervisor())

    session = service.create_and_launch_worker(project_key="promptmaster", prompt="Do the next task")

    assert session.name == "worker_promptmaster"
    assert calls == [
        ("create", "promptmaster", "Do the next task"),
        ("launch", "worker_promptmaster"),
    ]


def test_service_focus_and_send_input_use_supervisor(monkeypatch, tmp_path: Path) -> None:
    service = PromptMasterService(tmp_path / "promptmaster.toml")
    calls: list[tuple[str, str, str | None]] = []

    class FakeSupervisor:
        def focus_session(self, session_name: str) -> None:
            calls.append(("focus", session_name, None))

        def send_input(self, session_name: str, text: str, owner: str = "human") -> None:
            calls.append(("send", session_name, text))

    monkeypatch.setattr(service, "load_supervisor", lambda: FakeSupervisor())

    service.focus_session("operator")
    service.send_input("operator", "Continue", owner="human")

    assert calls == [
        ("focus", "operator", None),
        ("send", "operator", "Continue"),
    ]
