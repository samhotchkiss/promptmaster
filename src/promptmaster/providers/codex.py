from __future__ import annotations

import shutil
from pathlib import Path

from promptmaster.models import AccountConfig, SessionConfig
from promptmaster.providers.base import LaunchCommand


class CodexAdapter:
    name = "codex"
    binary = "codex"

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def build_launch_command(
        self,
        session: SessionConfig,
        account: AccountConfig,
    ) -> LaunchCommand:
        argv = [self.binary, "--no-alt-screen"]
        argv.extend(session.args)
        if session.prompt:
            argv.append(session.prompt)
        resume_argv: list[str] | None = None
        resume_marker: Path | None = None
        if session.role in {"heartbeat-supervisor", "operator-pm"} and account.home is not None:
            resume_argv = [self.binary, "resume", "--last", "--no-alt-screen", *session.args]
            resume_marker = account.home / ".promptmaster" / "session-markers" / f"{session.name}.resume"
        return LaunchCommand(
            argv=argv,
            env=dict(account.env),
            cwd=session.cwd,
            resume_argv=resume_argv,
            resume_marker=resume_marker,
        )
