from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from promptmaster.accounts import AccountStatus, list_account_statuses
from promptmaster.config import load_config
from promptmaster.supervisor import Supervisor
from promptmaster.workers import create_worker_session, launch_worker_session


@dataclass(slots=True)
class StatusSnapshot:
    launches: list[object]
    windows: list[object]
    alerts: list[object]
    leases: list[object]
    errors: list[str]


class PromptMasterService:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def load_supervisor(self) -> Supervisor:
        return Supervisor(load_config(self.config_path))

    def status_snapshot(self) -> StatusSnapshot:
        supervisor = self.load_supervisor()
        launches, windows, alerts, leases, errors = supervisor.status()
        return StatusSnapshot(
            launches=launches,
            windows=windows,
            alerts=alerts,
            leases=leases,
            errors=errors,
        )

    def list_account_statuses(self) -> list[AccountStatus]:
        return list_account_statuses(self.config_path)

    def create_and_launch_worker(self, *, project_key: str, prompt: str):
        session = create_worker_session(
            self.config_path,
            project_key=project_key,
            prompt=prompt,
        )
        supervisor = self.load_supervisor()
        if supervisor.tmux.has_session(supervisor.config.project.tmux_session):
            launch_worker_session(self.config_path, session.name)
        return session

    def focus_session(self, session_name: str) -> None:
        self.load_supervisor().focus_session(session_name)

    def send_input(self, session_name: str, text: str, *, owner: str = "human") -> None:
        self.load_supervisor().send_input(session_name, text, owner=owner)

    def run_heartbeat(self) -> None:
        self.load_supervisor().run_heartbeat()
