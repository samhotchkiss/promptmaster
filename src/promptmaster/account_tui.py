from __future__ import annotations

from pathlib import Path

from promptmaster.control_tui import PromptMasterApp


class AccountsApp(PromptMasterApp):
    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path=config_path)
