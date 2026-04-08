from __future__ import annotations

import shlex

from promptmaster.models import AccountConfig, ProjectSettings
from promptmaster.providers.base import LaunchCommand
from promptmaster.runtime_env import provider_profile_env


class LocalRuntimeAdapter:
    def wrap_command(
        self,
        command: LaunchCommand,
        account: AccountConfig,
        project: ProjectSettings,
    ) -> str:
        env = provider_profile_env(account, base_env=command.env)
        home = account.home

        parts = [f"cd {shlex.quote(str(command.cwd))}"]
        if home is not None:
            parts.append(f"mkdir -p {shlex.quote(str(home))}")
            if "CODEX_HOME" in env:
                parts.append(f"mkdir -p {shlex.quote(env['CODEX_HOME'])}")
            if "CLAUDE_CONFIG_DIR" in env:
                parts.append(f"mkdir -p {shlex.quote(env['CLAUDE_CONFIG_DIR'])}")
        if command.resume_marker is not None:
            parts.append(f"mkdir -p {shlex.quote(str(command.resume_marker.parent))}")

        for key, value in env.items():
            parts.append(f"export {key}={shlex.quote(value)}")

        if command.resume_argv and command.resume_marker is not None:
            resume_marker = shlex.quote(str(command.resume_marker))
            resume_cmd = shlex.join(command.resume_argv)
            fresh_cmd = shlex.join(command.argv)
            parts.append(
                f"if [ -f {resume_marker} ]; then {resume_cmd}; _pm_status=$?; "
                f"if [ $_pm_status -eq 0 ]; then exit 0; fi; fi"
            )
            parts.append(f"exec {fresh_cmd}")
        else:
            parts.append(f"exec {shlex.join(command.argv)}")
        return " && ".join(parts)
