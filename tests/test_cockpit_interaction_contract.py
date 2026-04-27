"""Tests for the cockpit interaction contract (#881).

Locks in the rules from ``docs/cockpit-interaction-contract.md`` and
guards the #840 destructive-action regression at the contract layer
so future screens cannot reintroduce the same shape of bug.
"""

from __future__ import annotations

from textual.binding import Binding

from pollypm.cockpit_interaction import (
    ARM_WINDOW_SECONDS,
    ActionKind,
    BindingScope,
    CockpitBinding,
    FocusKind,
    GLOBAL_REFERENCE_BINDINGS,
    InteractionRegistry,
    REGISTRY,
    ScreenContract,
    armed_hint,
    cancel_arming,
    destructive_action_safe,
    help_text_for_app,
    is_action_armed,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
#
# #901 — earlier versions of this file replaced
# ``pollypm.cockpit_interaction.Input`` with a sibling class at module
# import time, which leaked into every later test in the same pytest
# process. The fix is to make ``_FakeInput`` a *real subclass* of
# ``textual.widgets.Input`` so the production ``isinstance(...,
# Input)`` check passes naturally — no module mutation, no fixture
# teardown needed. ``__new__`` skips Textual's ``__init__`` so we can
# build a valid Input instance without booting an event loop.

from textual.widgets import Input as _TextualInput  # noqa: E402


class _FakeApp:
    """Minimal stand-in for a Textual ``App`` for the arming helpers.

    The helpers only need ``focused`` (read) and the
    ``_pollypm_armed_action`` attribute (read+write). Using a real
    Textual App in unit tests would force an event loop; the helpers
    are pure-Python so a stub is sufficient and far faster.
    """

    def __init__(self, *, focused: object = None) -> None:
        self.focused = focused


class _FakeInput(_TextualInput):
    """Stand-in for ``textual.widgets.Input``.

    Subclassing the real Textual Input means the production
    ``isinstance(self.focused, Input)`` check returns True without
    monkey-patching the production module. Construct via ``__new__``
    so we never trigger Textual's ``__init__`` (which expects an
    event loop)."""

    def __new__(cls) -> "_FakeInput":  # type: ignore[override]
        return _TextualInput.__new__(cls)


def _now_factory(start: float = 1000.0):
    """Return a tuple ``(now, advance)`` of injectable clock helpers."""
    state = {"t": start}

    def now() -> float:
        return state["t"]

    def advance(dt: float) -> None:
        state["t"] += dt

    return now, advance


# ---------------------------------------------------------------------------
# destructive_action_safe — the #840 root-cause regression test
# ---------------------------------------------------------------------------


def test_first_press_arms_and_refuses() -> None:
    """The #840 regression: first press from default table focus must
    not approve. Returns False, arms internally, surfaces a hint."""
    app = _FakeApp(focused=None)
    now, _ = _now_factory()

    fired = destructive_action_safe(
        app,
        "approve_task",
        target_id="t1",
        selection=set(),
        now=now,
    )

    assert fired is False
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is True
    hint = armed_hint(app, now=now)
    assert hint is not None
    assert "approve_task" in hint


def test_second_press_within_window_confirms() -> None:
    """Second press of the same key on the same target inside the
    arm window confirms — that is the explicit-intent contract."""
    app = _FakeApp(focused=None)
    now, advance = _now_factory()

    first = destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )
    advance(ARM_WINDOW_SECONDS / 2)
    second = destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )

    assert first is False
    assert second is True
    # After confirmation the arm clears.
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is False


def test_second_press_after_window_does_not_confirm() -> None:
    """If the user waits longer than the arm window, a second press
    is treated as a fresh first press — re-arms and refuses."""
    app = _FakeApp(focused=None)
    now, advance = _now_factory()

    destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )
    advance(ARM_WINDOW_SECONDS + 1.0)
    fired = destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )

    assert fired is False
    # Re-arm is in place with the new expiry.
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is True


def test_second_press_on_different_target_does_not_confirm() -> None:
    """Arming a different target than the first press must not
    confirm — guards the user navigating to another row mid-arm."""
    app = _FakeApp(focused=None)
    now, advance = _now_factory()

    destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )
    advance(0.5)
    fired = destructive_action_safe(
        app, "approve_task", target_id="t2", selection=set(), now=now
    )

    assert fired is False
    # The arm is now on t2, not t1.
    assert is_action_armed(app, "approve_task", target_id="t2", now=now) is True
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is False


def test_input_focus_returns_false() -> None:
    """When the user is typing, the destructive key belongs to the
    input, not the action. Helper returns False without arming."""
    app = _FakeApp(focused=_FakeInput())
    now, _ = _now_factory()

    fired = destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )

    assert fired is False
    # No arm — typing into a search box must not leave a primed
    # destructive action behind.
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is False


def test_input_focus_returns_false_with_real_textual_input() -> None:
    """#901 regression — production ``isinstance(..., Input)`` check
    must work against a real ``textual.widgets.Input`` instance, not
    only the test stub. This test bypasses ``_FakeInput`` entirely
    and constructs the real widget so a future contract change that
    silently restricts the type check still surfaces here."""
    real_input = _TextualInput.__new__(_TextualInput)
    app = _FakeApp(focused=real_input)
    now, _ = _now_factory()

    fired = destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )
    assert fired is False
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is False


def test_production_input_global_is_unmodified() -> None:
    """#901 regression — the production module must NOT be mutated
    by importing this test file. Earlier versions reassigned
    ``pollypm.cockpit_interaction.Input`` at module load and
    silently broke later tests' Input-focus checks."""
    import pollypm.cockpit_interaction as _ci

    assert _ci.Input is _TextualInput


def test_explicit_selection_fires_immediately() -> None:
    """When the target is in the explicit selection set the user
    already opted in via space-toggle. Single press confirms."""
    app = _FakeApp(focused=None)
    now, _ = _now_factory()

    fired = destructive_action_safe(
        app,
        "approve_task",
        target_id="t1",
        selection={"t1"},
        now=now,
    )

    assert fired is True


def test_explicit_selection_clears_stale_arm() -> None:
    """If a stale arm exists and the user toggles selection then
    presses, the action must fire and clear the stale arm."""
    app = _FakeApp(focused=None)
    now, _ = _now_factory()

    destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )
    fired = destructive_action_safe(
        app,
        "approve_task",
        target_id="t1",
        selection={"t1"},
        now=now,
    )

    assert fired is True
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is False


def test_cancel_arming_clears_state() -> None:
    """``cancel_arming`` must clear any active arm so a stale arm
    cannot survive into a different row or modal."""
    app = _FakeApp(focused=None)
    now, _ = _now_factory()

    destructive_action_safe(
        app, "reject_task", target_id="t9", selection=set(), now=now
    )
    assert is_action_armed(app, "reject_task", target_id="t9", now=now) is True

    cancel_arming(app)
    assert is_action_armed(app, "reject_task", target_id="t9", now=now) is False
    assert armed_hint(app, now=now) is None


def test_arming_is_per_action_name() -> None:
    """Different action names hold independent arms — pressing `a`
    once then `x` once must not approve or reject."""
    app = _FakeApp(focused=None)
    now, _ = _now_factory()

    destructive_action_safe(
        app, "approve_task", target_id="t1", selection=set(), now=now
    )
    fired = destructive_action_safe(
        app, "reject_task", target_id="t1", selection=set(), now=now
    )

    assert fired is False
    # The most recent arm wins; reject is now armed, approve is not.
    assert is_action_armed(app, "reject_task", target_id="t1", now=now) is True
    assert is_action_armed(app, "approve_task", target_id="t1", now=now) is False


# ---------------------------------------------------------------------------
# Registry + contract audit
# ---------------------------------------------------------------------------


def test_module_registry_has_tasks_contract() -> None:
    """Tasks must be registered. It is the canonical #840 surface
    and the launch-hardening release gate (#889) blocks v1 if it is
    not present."""
    # Registration happens at import time.
    import pollypm.cockpit_tasks  # noqa: F401

    contract = REGISTRY.get("PollyTasksApp")
    assert contract is not None
    assert contract.has_visible_input is True
    assert contract.arming_required_for_destructive is True
    assert contract.initial_focus is FocusKind.TABLE
    # Tasks must have at least one destructive binding to be a
    # meaningful registration.
    assert any(b.kind is ActionKind.DESTRUCTIVE for b in contract.bindings)


def test_tasks_contract_audit_clean() -> None:
    """The Tasks contract registration must pass the audit clean."""
    import pollypm.cockpit_tasks  # noqa: F401

    violations = [
        v for v in REGISTRY.audit() if v.startswith("PollyTasksApp:")
    ]
    assert violations == []


def test_audit_flags_input_plus_destructive_without_arming() -> None:
    """Synthesized contract that violates the #840 rule must be
    flagged — this is the audit's primary job."""
    registry = InteractionRegistry()
    registry.register(
        ScreenContract(
            screen_name="LeakyScreen",
            initial_focus=FocusKind.TABLE,
            has_visible_input=True,
            bindings=(
                CockpitBinding(
                    keys=("a",),
                    action="approve",
                    description="Approve",
                    kind=ActionKind.DESTRUCTIVE,
                ),
            ),
            arming_required_for_destructive=False,
        )
    )
    violations = registry.audit()
    assert any("LeakyScreen" in v and "#840" in v for v in violations)


def test_audit_flags_destructive_without_description() -> None:
    """A destructive binding without a description cannot show the
    user what it does in help — audit must reject it."""
    registry = InteractionRegistry()
    registry.register(
        ScreenContract(
            screen_name="UndocumentedScreen",
            initial_focus=FocusKind.TABLE,
            has_visible_input=False,
            bindings=(
                CockpitBinding(
                    keys=("k",),
                    action="kill_session",
                    description="",
                    kind=ActionKind.DESTRUCTIVE,
                ),
            ),
            arming_required_for_destructive=False,
        )
    )
    violations = registry.audit()
    assert any(
        "UndocumentedScreen" in v and "missing description" in v for v in violations
    )


def test_audit_flags_global_binding_without_priority() -> None:
    """``BindingScope.GLOBAL`` keys must be ``priority=True`` so they
    fire even from non-rail Apps."""
    registry = InteractionRegistry()
    registry.register(
        ScreenContract(
            screen_name="DemoScreen",
            initial_focus=FocusKind.NONE,
            has_visible_input=False,
            bindings=(
                CockpitBinding(
                    keys=("ctrl+q",),
                    action="quit",
                    description="quit",
                    scope=BindingScope.GLOBAL,
                    priority=False,
                ),
            ),
        )
    )
    violations = registry.audit()
    assert any(
        "DemoScreen" in v and "must be priority=True" in v for v in violations
    )


def test_audit_passes_on_clean_contract() -> None:
    """A correctly-shaped contract must produce zero violations."""
    registry = InteractionRegistry()
    registry.register(
        ScreenContract(
            screen_name="CleanScreen",
            initial_focus=FocusKind.TABLE,
            has_visible_input=True,
            bindings=(
                CockpitBinding(
                    keys=("a",),
                    action="approve",
                    description="Approve",
                    kind=ActionKind.DESTRUCTIVE,
                ),
                CockpitBinding(
                    keys=("ctrl+q",),
                    action="quit",
                    description="quit",
                    scope=BindingScope.GLOBAL,
                    priority=True,
                ),
            ),
            arming_required_for_destructive=True,
        )
    )
    assert registry.audit() == []


def test_re_register_overwrites() -> None:
    """Re-registering the same screen overwrites — supports tests
    and supports screens whose contract depends on runtime state."""
    registry = InteractionRegistry()
    registry.register(
        ScreenContract(
            screen_name="X",
            initial_focus=FocusKind.TABLE,
            has_visible_input=False,
        )
    )
    registry.register(
        ScreenContract(
            screen_name="X",
            initial_focus=FocusKind.LIST,
            has_visible_input=False,
        )
    )
    assert registry.get("X").initial_focus is FocusKind.LIST


# ---------------------------------------------------------------------------
# Help-text source-of-truth
# ---------------------------------------------------------------------------


class _AppWithBindings:
    """Stand-in App carrying a runtime BINDINGS list."""

    def __init__(self, bindings) -> None:
        self.BINDINGS = bindings


def test_help_text_includes_global_reference_bindings() -> None:
    """``help_text_for_app`` always emits the canonical global keys
    (Ctrl+Q, Ctrl+W, ?, Ctrl+K, :)."""
    app = _AppWithBindings([])
    out = help_text_for_app(app)
    actions = {b.action for b in out}
    assert "request_quit" in actions
    assert "show_keyboard_help" in actions
    assert "open_command_palette" in actions


def test_help_text_includes_runtime_bindings() -> None:
    """A binding declared on the App must appear in help output."""
    app = _AppWithBindings(
        [
            Binding(key="r", action="refresh", description="Refresh"),
            Binding(key="a", action="approve", description="Approve"),
        ]
    )
    out = help_text_for_app(app)
    actions = {b.action for b in out}
    assert "refresh" in actions
    assert "approve" in actions


def test_help_text_skips_show_false_bindings() -> None:
    """Hidden bindings (``show=False``) must not leak into help."""
    app = _AppWithBindings(
        [
            Binding(
                key="x",
                action="hidden_action",
                description="Hidden",
                show=False,
            ),
            Binding(
                key="a",
                action="visible_action",
                description="Visible",
            ),
        ]
    )
    out = help_text_for_app(app)
    actions = {b.action for b in out}
    assert "visible_action" in actions
    assert "hidden_action" not in actions


def test_help_text_dedupes_repeated_keys() -> None:
    """If a runtime BINDINGS list duplicates a global, the helper
    emits the entry once."""
    app = _AppWithBindings(
        [
            Binding(
                key="ctrl+q",
                action="request_quit",
                description="quit",
            ),
        ]
    )
    out = help_text_for_app(app)
    quit_entries = [b for b in out if b.action == "request_quit"]
    assert len(quit_entries) == 1


def test_help_text_accepts_tuple_bindings() -> None:
    """Some cockpit Apps declare BINDINGS as ``(key, action, desc)``
    tuples instead of ``Binding(...)``. The helper must handle both."""
    app = _AppWithBindings(
        [
            ("r", "refresh", "Refresh"),
        ]
    )
    out = help_text_for_app(app)
    actions = {b.action for b in out}
    assert "refresh" in actions


def test_global_reference_bindings_are_priority() -> None:
    """The canonical global keymap must mark every binding
    ``priority=True`` so they fire from any non-rail App."""
    for binding in GLOBAL_REFERENCE_BINDINGS:
        assert binding.priority is True
        assert binding.scope is BindingScope.GLOBAL


def test_cockpit_binding_from_textual_normalizes_keys() -> None:
    """``CockpitBinding.from_textual`` must split comma-keys into a
    tuple so the audit and conflict detection can treat each key
    independently."""
    binding = Binding(key="j,down", action="cursor_down", description="Down")
    cb = CockpitBinding.from_textual(binding)
    assert cb.keys == ("j", "down")
    assert cb.action == "cursor_down"
    assert cb.description == "Down"
