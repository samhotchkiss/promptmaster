"""Advisor plugin entrypoint.

Per ``docs/advisor-plugin-spec.md``, this plugin registers:

* An ``advisor`` agent profile (the carefully-tuned persona loaded from
  ``profiles/advisor.md``).
* A ``@every 30m`` roster entry → ``advisor.tick`` job handler.
* A ``@every 12h`` roster entry → ``advisor.autoclose`` sweep (ad05 will
  wire the real inbox auto-close logic).
* The corresponding job-handler registrations.

The advisor cadence can be overridden via ``[advisor].cadence`` in
``pollypm.toml`` (spec §9). The roster schedule is resolved at
``register_roster`` time — per spec — and a config change requires a
rail restart to pick up.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pollypm.plugin_api.v1 import (
    Capability,
    JobHandlerAPI,
    PollyPMPlugin,
    RosterAPI,
)
from pollypm.plugins_builtin.advisor.handlers.advisor_tick import advisor_tick_handler
from pollypm.plugins_builtin.advisor.handlers.autoclose import advisor_autoclose_handler
from pollypm.plugins_builtin.advisor.settings import (
    DEFAULT_CADENCE,
    load_advisor_settings,
)

if TYPE_CHECKING:
    from pollypm.agent_profiles.base import AgentProfileContext


PROFILE_PATH = Path(__file__).resolve().parent / "profiles" / "advisor.md"


class _AdvisorProfile:
    """Markdown-prompt profile for the advisor persona.

    Implemented as a plain slotted class (not ``@dataclass(slots=True)``)
    because plugin modules are loaded via ``importlib.util.spec_from_file_location``
    without being added to ``sys.modules``; under Python 3.14 that trips
    an internal ``cls.__module__`` lookup inside ``dataclasses``. Same
    reason the project_planning plugin uses a plain class for its
    markdown profile.
    """

    __slots__ = ("name", "path")

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path

    def build_prompt(self, context: "AgentProfileContext") -> str | None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        return text.strip() or None


def _advisor_profile_factory() -> _AdvisorProfile:
    return _AdvisorProfile(name="advisor", path=PROFILE_PATH)


def _resolve_cadence() -> str:
    """Resolve the advisor cadence from config, falling back to default.

    Read at ``register_roster`` time — the roster is registered once at
    rail bootstrap, so a cadence change in ``pollypm.toml`` requires a
    rail restart to pick up. That's explicitly called out in ad06.
    """
    try:
        from pollypm.config import DEFAULT_CONFIG_PATH, resolve_config_path
        config_path = resolve_config_path(DEFAULT_CONFIG_PATH)
        if not config_path.exists():
            return DEFAULT_CADENCE
        settings = load_advisor_settings(config_path)
        return settings.cadence or DEFAULT_CADENCE
    except Exception:  # noqa: BLE001
        # Config resolution failures must never block the rail bootstrap.
        return DEFAULT_CADENCE


def _register_handlers(api: JobHandlerAPI) -> None:
    api.register_handler(
        "advisor.tick",
        advisor_tick_handler,
        max_attempts=1,
        timeout_seconds=60.0,
    )
    api.register_handler(
        "advisor.autoclose",
        advisor_autoclose_handler,
        max_attempts=1,
        timeout_seconds=30.0,
    )


def _register_roster(api: RosterAPI) -> None:
    cadence = _resolve_cadence()
    api.register_recurring(
        cadence,
        "advisor.tick",
        {},
        dedupe_key="advisor.tick",
    )
    # Inbox auto-close — every 12 hours. Stub until ad05. Kept registered
    # from day one so ad05 only has to replace the handler body, not add
    # a fresh roster entry.
    api.register_recurring(
        "@every 12h",
        "advisor.autoclose",
        {},
        dedupe_key="advisor.autoclose",
    )


plugin = PollyPMPlugin(
    name="advisor",
    version="0.1.0",
    description=(
        "Advisor: every 30 minutes, reviews recent project activity "
        "against the plan and emits an inbox insight only when it sees "
        "something structurally worth saying. Silent by default — the "
        "persona is the quality filter."
    ),
    capabilities=(
        Capability(kind="agent_profile", name="advisor"),
        Capability(kind="job_handler", name="advisor.tick"),
        Capability(kind="job_handler", name="advisor.autoclose"),
        Capability(kind="roster_entry", name="advisor.tick"),
        Capability(kind="roster_entry", name="advisor.autoclose"),
    ),
    agent_profiles={"advisor": _advisor_profile_factory},
    register_handlers=_register_handlers,
    register_roster=_register_roster,
)
