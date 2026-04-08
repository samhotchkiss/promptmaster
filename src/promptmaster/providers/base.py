from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from promptmaster.models import AccountConfig, SessionConfig


@dataclass(slots=True)
class LaunchCommand:
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    resume_argv: list[str] | None = None
    resume_marker: Path | None = None


class ProviderAdapter(Protocol):
    name: str
    binary: str

    def is_available(self) -> bool: ...

    def build_launch_command(
        self,
        session: SessionConfig,
        account: AccountConfig,
    ) -> LaunchCommand: ...
