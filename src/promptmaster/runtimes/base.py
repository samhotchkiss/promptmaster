from __future__ import annotations

from typing import Protocol

from promptmaster.models import AccountConfig, ProjectSettings
from promptmaster.providers.base import LaunchCommand


class RuntimeAdapter(Protocol):
    def wrap_command(
        self,
        command: LaunchCommand,
        account: AccountConfig,
        project: ProjectSettings,
    ) -> str: ...
