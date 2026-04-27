"""Cockpit-wide interaction contract and shared keybinding registry.

Issue #881 — establishes one runtime contract for focus ownership,
modal trapping, search-mode semantics, destructive-action gating, and
help-text source-of-truth across every cockpit surface (Home, Rail,
Inbox, Activity, Settings, Tasks, Project Dashboard, PM Chat, Help).

Before this module:

* Each cockpit ``App`` declared its own ``BINDINGS`` list and ad-hoc
  ``on_key`` handlers. The same letter behaved differently across
  screens (``a`` = approve in Tasks, archive in Inbox, alerts in
  Settings/Activity/Dashboard) and there was no central place to spot
  the conflict.
* Destructive single-key actions in Tasks were guarded only by
  ``isinstance(self.focused, Input)``. The Tasks view opens with
  ``DataTable`` focus (cockpit_tasks.py:1186), so the guard never
  fired and a bench test was able to approve a real task purely by
  pressing ``a`` (#840).
* Help text was already mostly generated from runtime ``BINDINGS``
  (good!), but the contract for *what each binding scope means* and
  what initial-focus / arming each screen declares was implicit.

This module is the structural fix. It does not rewrite every screen —
that is too risky to land in one PR right before v1 — but it gives
every screen one place to declare its contract, one helper to gate
destructive actions, and a CI-time audit that catches drift.

Contract (also see ``docs/cockpit-interaction-contract.md``):

1. **Initial focus.** Every primary cockpit surface declares its
   ``initial_focus`` widget kind (``table``, ``list``, ``input``,
   ``custom``) via :class:`ScreenContract`. The audit
   :meth:`InteractionRegistry.audit` fails if a screen with a visible
   text input also has destructive single-key actions and does *not*
   declare ``arming_required_for_destructive=True``.
2. **Destructive-action gating.** Single-key destructive actions
   (approve, reject, archive, remove, kill, …) in any screen with a
   visible text input run through :func:`destructive_action_safe`.
   That helper returns ``True`` only when the action is explicitly
   armed: either the action target is in an explicit selection set,
   *or* the same action key was pressed within
   :data:`ARM_WINDOW_SECONDS`. The first press shows a confirmation
   hint and arms; the second confirms.
3. **Modal trapping.** Modal screens trap all ``BindingScope.APP``
   bindings while mounted. Only ``BindingScope.GLOBAL`` (``Ctrl+Q``,
   ``Ctrl+W``, ``?``) and the modal's own ``BindingScope.MODAL``
   bindings fire.
4. **Search mode.** When a search affordance has focus, all
   single-letter ``BindingScope.APP`` keys route to the input.
   ``Escape`` exits search; ``Enter`` submits and yields focus back
   to the table. ``BindingScope.GLOBAL`` keys still fire.
5. **Escape policy.** ``Escape`` is never silently consumed. It
   closes a modal, exits search, returns to the home view, or quits
   the app — in that priority order.
6. **Quit policy.** ``q``/``Q`` is local-back inside any detail
   ``App``. ``Ctrl+Q`` is the only universal quit. The Rail
   declares plain ``q`` as global only because the rail is the
   top-level view.
7. **Help text source.** :func:`help_text_for_app` walks the live
   ``BINDINGS`` list of the active screen plus the global reference
   table. The keyboard-help modal is forbidden from maintaining its
   own duplicated copy.

Migration policy: existing screens are not required to register their
contract immediately. Newly-added screens *must*. The audit lists
every primary surface that has not registered yet, so progress is
visible.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from textual.app import App
from textual.binding import Binding
from textual.widgets import Input


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------


ARM_WINDOW_SECONDS: float = 3.0
"""How long an armed destructive action stays primed for confirmation.

The window is short enough that an unattended cockpit cannot accept a
second press by accident, but long enough that a deliberate user can
read the prompt and confirm (#840).
"""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BindingScope(enum.Enum):
    """Which surfaces and modal states a binding is allowed to fire from.

    Members:

    * :attr:`GLOBAL` — fires from any cockpit surface, including while a
      modal is on top. Reserved for ``Ctrl+Q`` (quit), ``Ctrl+W``
      (detach), ``?`` (help), ``Ctrl+K``/``:`` (command palette). Ten
      bindings or fewer; document each addition.
    * :attr:`APP` — fires only on the owning ``App``. Suspended while
      any ``ModalScreen`` is on top.
    * :attr:`SEARCH` — fires only while the screen's search ``Input``
      has focus.
    * :attr:`MODAL` — fires only while the owning ``ModalScreen`` is
      itself on top.
    """

    GLOBAL = "global"
    APP = "app"
    SEARCH = "search"
    MODAL = "modal"


class ActionKind(enum.Enum):
    """Risk classification for a binding's action.

    Used by :func:`destructive_action_safe` and the contract audit to
    decide whether arming is required.
    """

    NAVIGATION = "navigation"
    """Cursor and focus motion: ``j/k``, arrows, ``g/G``, ``Tab``,
    ``Enter`` (when it just opens). Always safe."""

    READ = "read"
    """Refresh, start-search, view-toggle, expand/collapse. Safe."""

    WRITE = "write"
    """Toggles a state the user can freely undo: filter on/off, pin
    project, mark unread, switch tab. Reversible, no external effect."""

    DESTRUCTIVE = "destructive"
    """Approve, reject, archive, remove, kill, dismiss, send.
    Requires arming when the screen has a visible text input."""


class FocusKind(enum.Enum):
    """The widget kind a screen brings focus to on mount."""

    TABLE = "table"
    """A ``DataTable``. Cursor keys navigate rows."""

    LIST = "list"
    """A ``ListView`` or list-like widget. Cursor keys navigate items."""

    INPUT = "input"
    """A text ``Input``. Letter keys type into the input by default."""

    CUSTOM = "custom"
    """A custom widget; the screen owns its own focus semantics."""

    NONE = "none"
    """Modal/overlay where focus is owned by the modal itself."""


# ---------------------------------------------------------------------------
# Binding metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CockpitBinding:
    """Structured metadata for one keybinding.

    The Textual ``Binding`` type only carries ``key``, ``action``, and
    ``description``. The launch-hardening contract needs ``scope`` and
    ``kind`` so the help generator and the audit can reason about
    each binding without re-parsing ``BINDINGS`` source code.
    """

    keys: tuple[str, ...]
    action: str
    description: str
    scope: BindingScope = BindingScope.APP
    kind: ActionKind = ActionKind.NAVIGATION
    priority: bool = False
    show_in_help: bool = True

    @classmethod
    def from_textual(
        cls,
        binding: Binding,
        *,
        scope: BindingScope = BindingScope.APP,
        kind: ActionKind = ActionKind.NAVIGATION,
    ) -> "CockpitBinding":
        """Adapt a Textual ``Binding`` into a structured ``CockpitBinding``."""
        keys = tuple(
            part.strip() for part in (binding.key or "").split(",") if part.strip()
        )
        return cls(
            keys=keys,
            action=binding.action,
            description=binding.description or "",
            scope=scope,
            kind=kind,
            priority=getattr(binding, "priority", False),
            show_in_help=getattr(binding, "show", True),
        )


# ---------------------------------------------------------------------------
# Screen contract
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScreenContract:
    """Per-screen declaration of the interaction contract.

    A screen's contract is the source of truth for:

    * which widget kind owns the cursor on mount (``initial_focus``)
    * whether the screen has a visible text input that competes for
      letter keys (``has_visible_input``)
    * whether destructive single-key actions on this screen require
      arming (``arming_required_for_destructive``)
    * the structured binding list (``bindings``) — duplicates the
      runtime ``BINDINGS`` declarations but adds ``scope`` and
      ``kind`` so the audit can reason about them.
    """

    screen_name: str
    """Stable identifier — typically the App class name (e.g.,
    ``"PollyTasksApp"``) or a route name (e.g., ``"home"``)."""

    initial_focus: FocusKind
    """Where focus lands when the screen mounts."""

    has_visible_input: bool
    """``True`` if the screen renders a text ``Input`` (search, filter,
    reply, etc.) at any time during its lifecycle."""

    bindings: tuple[CockpitBinding, ...] = field(default_factory=tuple)
    """Structured copy of the screen's ``BINDINGS`` list."""

    arming_required_for_destructive: bool = True
    """When ``True``, destructive single-key actions on this screen
    must run through :func:`destructive_action_safe`. Override to
    ``False`` only on screens that have *no* visible text input and
    therefore cannot suffer the #840 ambiguity."""

    notes: str = ""
    """Human-readable notes about screen-specific exceptions."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class InteractionRegistry:
    """Process-wide registry of every screen's :class:`ScreenContract`.

    Screens register their contract at import time (or on App
    instantiation). Tests and the runtime help generator read from
    this registry; ``audit()`` returns the list of violations the CI
    suite asserts is empty (modulo the migration allow-list).
    """

    def __init__(self) -> None:
        self._screens: dict[str, ScreenContract] = {}

    def register(self, contract: ScreenContract) -> None:
        """Register a screen's contract.

        Re-registering the same ``screen_name`` overwrites the prior
        entry — convenient for tests that mutate contracts and for
        screens whose contract depends on runtime state.
        """
        self._screens[contract.screen_name] = contract

    def unregister(self, screen_name: str) -> None:
        """Remove a screen's contract. No-op if not registered."""
        self._screens.pop(screen_name, None)

    def get(self, screen_name: str) -> ScreenContract | None:
        """Return the contract for ``screen_name`` or ``None``."""
        return self._screens.get(screen_name)

    def all_screens(self) -> Iterator[ScreenContract]:
        """Iterate every registered contract in registration order."""
        return iter(self._screens.values())

    def screen_names(self) -> tuple[str, ...]:
        """Return registered screen names (sorted for stable diffs)."""
        return tuple(sorted(self._screens))

    def audit(self) -> list[str]:
        """Return the list of contract violations.

        A clean run returns ``[]``. Each violation is a single-line
        human-readable string suitable for inclusion in a test
        failure message.
        """
        violations: list[str] = []
        for contract in self._screens.values():
            violations.extend(_audit_screen(contract))
        return violations


REGISTRY: InteractionRegistry = InteractionRegistry()
"""Module-level singleton registry. Tests can construct their own
:class:`InteractionRegistry` for isolation, but production cockpit
code uses this one."""


def _audit_screen(contract: ScreenContract) -> list[str]:
    """Return violations for a single screen contract."""
    out: list[str] = []
    has_destructive = any(
        b.kind is ActionKind.DESTRUCTIVE for b in contract.bindings
    )
    # Rule: screens with a visible input AND destructive bindings must
    # opt into arming. The #840 bug is exactly this combination
    # without arming — Tasks view, search input present, default
    # DataTable focus, and `a`/`x` firing on first press.
    if (
        has_destructive
        and contract.has_visible_input
        and not contract.arming_required_for_destructive
    ):
        out.append(
            f"{contract.screen_name}: has visible input + destructive "
            f"bindings but arming_required_for_destructive=False (#840)"
        )

    # Rule: every destructive binding must declare description text so
    # help can show the user what the key does before they press it.
    for binding in contract.bindings:
        if binding.kind is ActionKind.DESTRUCTIVE and not binding.description:
            keys = "/".join(binding.keys) or "<unset>"
            out.append(
                f"{contract.screen_name}: destructive binding {keys} "
                f"(action={binding.action}) is missing description"
            )

    # Rule: GLOBAL bindings must be priority=True. Otherwise the rail
    # cannot intercept them when a non-rail App has focus.
    for binding in contract.bindings:
        if binding.scope is BindingScope.GLOBAL and not binding.priority:
            keys = "/".join(binding.keys) or "<unset>"
            out.append(
                f"{contract.screen_name}: GLOBAL binding {keys} "
                f"(action={binding.action}) must be priority=True"
            )

    return out


# ---------------------------------------------------------------------------
# Destructive-action arming primitive (#840 fix)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ArmedAction:
    """Internal record of an armed destructive action."""

    action: str
    target_id: str | None
    expires_at: float


_ARM_ATTR: str = "_pollypm_armed_action"
"""The attribute name we attach to App instances to remember the
currently-armed action. Using a single attribute keeps the cockpit
simple: only one destructive action can be armed at a time."""


def _now() -> float:
    """Return the current monotonic time. Indirected so tests can patch."""
    return time.monotonic()


def _input_has_focus(app: App) -> bool:
    """Return ``True`` when the focused widget is a ``Input``."""
    focused = getattr(app, "focused", None)
    return isinstance(focused, Input)


def destructive_action_safe(
    app: App,
    action: str,
    *,
    target_id: str | None = None,
    selection: Iterable[str] | None = None,
    now: Callable[[], float] = _now,
) -> bool:
    """Return ``True`` when a destructive single-key action may fire.

    Use this in place of the older ``isinstance(self.focused, Input)``
    guard for any single-key destructive action on a screen that has a
    visible text input. See :class:`ScreenContract` and
    :data:`ARM_WINDOW_SECONDS`.

    Parameters:

    * ``app`` — the host Textual ``App`` instance. Arming state is
      stored on the app attribute :data:`_ARM_ATTR` so it survives
      across keypresses.
    * ``action`` — stable name for the action (e.g.,
      ``"approve_task"``). Two different actions cannot share an arm;
      arming the second cancels the first.
    * ``target_id`` — optional identifier of the action target (task
      id, message id, account name). When provided, the second press
      must target the same id to confirm — guards against the user
      navigating to a different row between arm and confirm.
    * ``selection`` — optional iterable of explicit-selection ids
      maintained by the screen (e.g., ``_selected_task_ids``). When
      the action target is in the selection set, the action fires
      without arming because the user already opted in.
    * ``now`` — clock function; injected for tests.

    Decision tree (in order):

    1. If a text ``Input`` currently has focus, the keystroke is the
       user typing — return ``False``. The action key is consumed by
       the input, not the destructive handler.
    2. If ``target_id`` is in ``selection``, the user explicitly
       opted in — return ``True`` and clear any arming state.
    3. If a previous arm is active for the same ``(action,
       target_id)`` and within :data:`ARM_WINDOW_SECONDS`, return
       ``True`` (this is the confirmation press) and clear the arm.
    4. Otherwise: arm the action and return ``False``. The caller is
       expected to surface a transient hint (toast, footer line) so
       the user knows a second press will confirm.

    The function never raises; on any unexpected error it falls back
    to the safest answer (``False``) so a destructive action never
    fires from a half-broken arming subsystem.
    """
    try:
        if _input_has_focus(app):
            return False

        if target_id is not None and selection is not None:
            if target_id in selection:
                cancel_arming(app)
                return True

        armed: _ArmedAction | None = getattr(app, _ARM_ATTR, None)
        current = now()
        if (
            armed is not None
            and armed.action == action
            and armed.target_id == target_id
            and armed.expires_at > current
        ):
            # Confirmation press: clear arming, allow the action.
            setattr(app, _ARM_ATTR, None)
            return True

        # First press (or stale arm): arm now and refuse.
        setattr(
            app,
            _ARM_ATTR,
            _ArmedAction(
                action=action,
                target_id=target_id,
                expires_at=current + ARM_WINDOW_SECONDS,
            ),
        )
        return False
    except Exception:  # noqa: BLE001 — never leak arming bugs as approvals
        return False


def is_action_armed(
    app: App,
    action: str,
    *,
    target_id: str | None = None,
    now: Callable[[], float] = _now,
) -> bool:
    """Return ``True`` when ``action`` is currently armed for ``target_id``.

    Read-only check used by render code that wants to show ``"press
    again to confirm"`` hints without disturbing the arming state.
    The ``now`` parameter is injected for tests; production callers
    leave it at the default monotonic clock.
    """
    armed: _ArmedAction | None = getattr(app, _ARM_ATTR, None)
    if armed is None:
        return False
    if armed.action != action:
        return False
    if armed.target_id != target_id:
        return False
    return armed.expires_at > now()


def cancel_arming(app: App) -> None:
    """Cancel any active arm.

    Call from focus-change handlers, modal mount, search start, and
    cursor-row change so a stale arm cannot survive the user's
    attention shifting elsewhere.
    """
    setattr(app, _ARM_ATTR, None)


def armed_hint(
    app: App,
    *,
    now: Callable[[], float] = _now,
) -> str | None:
    """Return a short human-readable hint about the current arm.

    Returns ``None`` when nothing is armed. Used by the cockpit
    footer / toast layer to render ``"Press a again within 3s to
    approve"`` after the first press. The ``now`` parameter is
    injected for tests.
    """
    armed: _ArmedAction | None = getattr(app, _ARM_ATTR, None)
    if armed is None:
        return None
    if armed.expires_at <= now():
        return None
    target = f" {armed.target_id}" if armed.target_id else ""
    return f"Press again within {ARM_WINDOW_SECONDS:.0f}s to {armed.action}{target}"


# ---------------------------------------------------------------------------
# Help-text source-of-truth helpers
# ---------------------------------------------------------------------------


GLOBAL_REFERENCE_BINDINGS: tuple[CockpitBinding, ...] = (
    CockpitBinding(
        keys=("ctrl+k",),
        action="open_command_palette",
        description="command palette",
        scope=BindingScope.GLOBAL,
        kind=ActionKind.READ,
        priority=True,
    ),
    CockpitBinding(
        keys=("colon",),
        action="open_command_palette",
        description="command palette",
        scope=BindingScope.GLOBAL,
        kind=ActionKind.READ,
        priority=True,
    ),
    CockpitBinding(
        keys=("question_mark",),
        action="show_keyboard_help",
        description="this help",
        scope=BindingScope.GLOBAL,
        kind=ActionKind.READ,
        priority=True,
    ),
    CockpitBinding(
        keys=("ctrl+q",),
        action="request_quit",
        description="quit",
        scope=BindingScope.GLOBAL,
        kind=ActionKind.WRITE,
        priority=True,
    ),
    CockpitBinding(
        keys=("ctrl+w",),
        action="detach",
        description="detach",
        scope=BindingScope.GLOBAL,
        kind=ActionKind.WRITE,
        priority=True,
    ),
)
"""The canonical global keymap. Help text and the audit both consume
this tuple. Adding a new global key requires updating this list and
the contract doc."""


def help_text_for_app(app: App) -> list[CockpitBinding]:
    """Walk the active ``BINDINGS`` of ``app`` plus the global keymap.

    The keyboard-help modal calls this. It must not maintain its own
    copy of binding text — the contract requires runtime BINDINGS as
    the single source of truth.
    """
    out: list[CockpitBinding] = []
    seen: set[tuple[tuple[str, ...], str]] = set()
    bindings = list(GLOBAL_REFERENCE_BINDINGS)

    raw_app_bindings = getattr(app, "BINDINGS", None) or ()
    for raw in raw_app_bindings:
        b = _coerce_textual_binding(raw)
        if b is None:
            continue
        bindings.append(CockpitBinding.from_textual(b))

    for binding in bindings:
        if not binding.show_in_help:
            continue
        key = (binding.keys, binding.action)
        if key in seen:
            continue
        seen.add(key)
        out.append(binding)
    return out


def _coerce_textual_binding(raw: object) -> Binding | None:
    """Normalize a Textual ``BINDINGS`` entry into a ``Binding``.

    Textual accepts both ``Binding`` instances and ``(key, action,
    description, ...)`` tuples in ``BINDINGS``. The cockpit uses both
    forms, so the help generator coerces whatever it finds.
    """
    if isinstance(raw, Binding):
        return raw
    if isinstance(raw, tuple) and len(raw) >= 2:
        key = str(raw[0])
        action = str(raw[1])
        description = str(raw[2]) if len(raw) >= 3 else ""
        return Binding(key=key, action=action, description=description)
    return None
