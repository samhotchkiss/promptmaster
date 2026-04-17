"""``[advisor]`` config reader.

The advisor plugin reads its own settings section directly from the
user's ``pollypm.toml``. Keys (spec §9):

* ``enabled`` (bool, default ``True``) — master kill switch.
* ``cadence`` (str, default ``"@every 30m"``) — roster schedule.
  Overridable to quiet a chatty project (e.g. ``"@every 2h"``). The
  plugin's ``register_roster`` hook reads this at startup; CLI changes
  take effect on the next rail restart.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CADENCE = "@every 30m"


@dataclass(slots=True, frozen=True)
class AdvisorSettings:
    enabled: bool = True
    cadence: str = DEFAULT_CADENCE


def parse_advisor_settings(raw: object) -> AdvisorSettings:
    """Parse a raw ``[advisor]`` TOML table into settings."""
    if not isinstance(raw, dict):
        return AdvisorSettings()
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True
    cadence_raw = raw.get("cadence", DEFAULT_CADENCE)
    cadence = (
        cadence_raw.strip()
        if isinstance(cadence_raw, str) and cadence_raw.strip()
        else DEFAULT_CADENCE
    )
    return AdvisorSettings(enabled=enabled, cadence=cadence)


def load_advisor_settings(config_path: Path) -> AdvisorSettings:
    """Load the ``[advisor]`` section from a ``pollypm.toml`` file.

    Missing file / parse error / missing section → defaults. Advisor
    config must never crash the rail.
    """
    try:
        raw = tomllib.loads(Path(config_path).read_text())
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return AdvisorSettings()
    section = raw.get("advisor") if isinstance(raw, dict) else None
    return parse_advisor_settings(section)
