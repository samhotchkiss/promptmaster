from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TmuxWindow:
    session: str
    index: int
    name: str
    active: bool
    pane_id: str
    pane_current_command: str
    pane_current_path: str
    pane_dead: bool


class TmuxClient:
    def _inside_tmux(self) -> bool:
        return bool(os.environ.get("TMUX"))

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", *args],
            check=check,
            text=True,
            capture_output=True,
        )

    def has_session(self, name: str) -> bool:
        result = self.run("has-session", "-t", name, check=False)
        return result.returncode == 0

    def current_session_name(self) -> str | None:
        if not self._inside_tmux():
            return None
        result = self.run("display-message", "-p", "#S", check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def current_window_index(self) -> str | None:
        if not self._inside_tmux():
            return None
        result = self.run("display-message", "-p", "#I", check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def create_session(self, name: str, window_name: str, command: str, *, remain_on_exit: bool = True) -> None:
        self.run("new-session", "-d", "-s", name, "-n", window_name, command)
        self.run("set-option", "-t", name, "remain-on-exit", "on" if remain_on_exit else "off")

    def new_session_attached(self, name: str, window_name: str, command: str) -> int:
        result = subprocess.run(
            ["tmux", "new-session", "-A", "-s", name, "-n", window_name, command],
            check=False,
        )
        return result.returncode

    def create_window(self, name: str, window_name: str, command: str, *, detached: bool = False) -> None:
        args = ["new-window", "-t", name, "-n", window_name]
        if detached:
            args.append("-d")
        args.append(command)
        self.run(*args)

    def select_window(self, target: str) -> None:
        self.run("select-window", "-t", target)

    def kill_window(self, target: str) -> None:
        self.run("kill-window", "-t", target)

    def kill_session(self, name: str) -> None:
        self.run("kill-session", "-t", name)

    def pipe_pane(self, target: str, log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.run("pipe-pane", "-o", "-t", target, f"cat >> {shlex.quote(str(log_path))}")

    def list_windows(self, name: str) -> list[TmuxWindow]:
        fmt = (
            "#{session_name}\t#{window_index}\t#{window_name}\t#{window_active}\t#{pane_id}\t"
            "#{pane_current_command}\t#{pane_current_path}\t#{pane_dead}"
        )
        result = self.run("list-windows", "-t", name, "-F", fmt)
        windows: list[TmuxWindow] = []
        for line in result.stdout.splitlines():
            (
                session,
                index,
                window_name,
                active,
                pane_id,
                pane_current_command,
                pane_current_path,
                pane_dead,
            ) = line.split("\t", 7)
            windows.append(
                TmuxWindow(
                    session=session,
                    index=int(index),
                    name=window_name,
                    active=active == "1",
                    pane_id=pane_id,
                    pane_current_command=pane_current_command,
                    pane_current_path=pane_current_path,
                    pane_dead=pane_dead == "1",
                )
            )
        return windows

    def capture_pane(self, target: str, lines: int = 200) -> str:
        result = self.run("capture-pane", "-p", "-S", f"-{lines}", "-t", target)
        return result.stdout

    def send_keys(self, target: str, text: str, press_enter: bool = True) -> None:
        self.run("send-keys", "-l", "-t", target, text)
        if press_enter:
            self.run("send-keys", "-t", target, "Enter")

    def attach_session(self, name: str) -> int:
        result = subprocess.run(["tmux", "attach", "-t", name], check=False)
        return result.returncode

    def switch_client(self, name: str) -> int:
        result = subprocess.run(["tmux", "switch-client", "-t", name], check=False)
        return result.returncode
