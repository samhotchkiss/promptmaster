"""Tests for the canonical role/persona invariant (#885)."""

from __future__ import annotations

import pytest

from pollypm.role_contract import (
    ROLE_REGISTRY,
    RoleContract,
    build_remediation_message,
    can_write_session_state,
    canonical_role,
    get_contract,
    guide_path_for,
    identity_markers_for,
    legacy_persona_table_disagreements,
    persona_for,
    validate_identity,
)


# ---------------------------------------------------------------------------
# canonical_role
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("operator_pm", "operator_pm"),
        ("operator-pm", "operator_pm"),
        ("OPERATOR-PM", "operator_pm"),
        ("Polly", "operator_pm"),
        ("polly", "operator_pm"),
        ("operator", "operator_pm"),
        ("architect", "architect"),
        ("Archie", "architect"),
        ("reviewer", "reviewer"),
        ("Russell", "reviewer"),
        ("worker", "worker"),
        ("heartbeat", "heartbeat_supervisor"),
        ("heartbeat-supervisor", "heartbeat_supervisor"),
    ],
)
def test_canonical_role_normalizes_aliases(raw: str, expected: str) -> None:
    """Aliases (display form / persona name / hyphenated form) all
    map to the canonical key."""
    assert canonical_role(raw) == expected


def test_canonical_role_rejects_unknown() -> None:
    """An unknown role raises rather than silently coercing — the
    audit cites the recurring shape of typo'd role names taking
    the wrong code path."""
    with pytest.raises(ValueError, match="Unknown role"):
        canonical_role("planner")


def test_canonical_role_rejects_none() -> None:
    with pytest.raises(ValueError):
        canonical_role(None)


def test_canonical_role_rejects_empty() -> None:
    with pytest.raises(ValueError):
        canonical_role("")


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_registry_contains_all_required_roles() -> None:
    """Every role the audit names is present in the canonical
    registry. New roles must be added here."""
    required = {
        "operator_pm",
        "architect",
        "reviewer",
        "worker",
        "heartbeat_supervisor",
    }
    assert set(ROLE_REGISTRY) >= required


def test_each_contract_is_frozen() -> None:
    """A consumer cannot mutate the contract for everyone else."""
    contract = ROLE_REGISTRY["operator_pm"]
    with pytest.raises((AttributeError, TypeError)):
        contract.persona_name = "Hacked"  # type: ignore[misc]


def test_no_contract_has_overlapping_markers_and_conflicts() -> None:
    """The dataclass __post_init__ enforces this; verifying
    explicitly so a future contract addition cannot regress."""
    for contract in ROLE_REGISTRY.values():
        markers = {m.lower() for m in contract.identity_markers}
        conflicts = {c.lower() for c in contract.conflicting_personas}
        assert markers.isdisjoint(conflicts), contract.key


def test_overlap_in_constructor_raises() -> None:
    """Constructing a contract with overlap raises immediately."""
    with pytest.raises(ValueError, match="overlap"):
        RoleContract(
            key="bad",
            persona_name="Bad",
            guide_path=None,
            identity_markers=("foo",),
            conflicting_personas=("FOO",),  # case-insensitive overlap
        )


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def test_persona_for_returns_canonical_name() -> None:
    """The persona name must round-trip from any alias."""
    assert persona_for("operator_pm") == "Polly"
    assert persona_for("operator-pm") == "Polly"
    assert persona_for("Polly") == "Polly"


def test_guide_path_for_returns_repository_path() -> None:
    """Operator's guide path is the canonical Polly operator guide."""
    path = guide_path_for("operator_pm")
    assert path is not None
    assert "polly-operator-guide" in path


def test_guide_path_for_worker_is_none() -> None:
    """Worker has no canonical guide — its identity is per-task."""
    assert guide_path_for("worker") is None


def test_identity_markers_returns_lowercase_safe_strings() -> None:
    """Markers are case-insensitive at validate time, so the
    contract just stores a representative lowercase set."""
    assert "polly" in [m.lower() for m in identity_markers_for("operator_pm")]


def test_can_write_session_state_matches_legacy_set() -> None:
    """The legacy heartbeat write-access set is preserved."""
    assert can_write_session_state("operator_pm") is True
    assert can_write_session_state("worker") is True
    assert can_write_session_state("heartbeat_supervisor") is True
    assert can_write_session_state("reviewer") is False
    assert can_write_session_state("architect") is False


# ---------------------------------------------------------------------------
# validate_identity — drift detection contract
# ---------------------------------------------------------------------------


def test_validate_identity_clean_when_marker_matches() -> None:
    """A pane that says 'I'm Polly' for the operator role: no drift."""
    assert validate_identity("operator_pm", "I'm Polly, ready to help.") is None


def test_validate_identity_detects_persona_swap() -> None:
    """The audit's #757 case: operator session identifies as Russell.

    The function returns the persona name the session drifted to."""
    drift = validate_identity("operator_pm", "I'm Russell, the reviewer.")
    assert drift == "Russell"


def test_validate_identity_handles_paraphrased_claim() -> None:
    """Identity claims phrased as 'This is X' must also be caught."""
    drift = validate_identity("operator_pm", "This is Russell, on shift.")
    assert drift == "Russell"


def test_validate_identity_ignores_third_person_mentions() -> None:
    """Mentioning another persona's name in passing is not drift.

    The patterns require an *identity claim*, not just a name."""
    text = "Russell is reviewing your PR; I'm Polly."
    assert validate_identity("operator_pm", text) is None


def test_validate_identity_returns_none_for_empty_pane() -> None:
    assert validate_identity("operator_pm", "") is None


def test_validate_identity_returns_none_on_unknown_role() -> None:
    """An unknown role yields ``None`` — the heartbeat is on the
    hot path and must not crash on a stale role string."""
    assert validate_identity("nonexistent_role", "I'm Polly.") is None


def test_validate_identity_detects_cross_role_drift_via_registry() -> None:
    """Reviewer claiming to be Polly is drift even though the
    contract's ``conflicting_personas`` list happens to enumerate
    the case — the cross-registry check is the safety net."""
    drift = validate_identity("reviewer", "I'm Polly, ready to help.")
    assert drift == "Polly"


# ---------------------------------------------------------------------------
# build_remediation_message
# ---------------------------------------------------------------------------


def test_remediation_message_names_canonical_persona() -> None:
    """The corrective message tells the session who it should
    be. The audit (#755) requires explicit re-anchor wording."""
    msg = build_remediation_message("operator_pm", "Russell")
    assert "Polly" in msg
    assert "operator_pm" in msg
    assert "Russell" in msg


def test_remediation_message_includes_guide_path_when_available() -> None:
    """Guides are how a drifted session re-anchors. Including the
    path makes recovery actionable."""
    msg = build_remediation_message("operator_pm", "Russell")
    assert "polly-operator-guide" in msg


def test_remediation_message_omits_guide_for_workers() -> None:
    """Worker has no canonical guide; the message must omit the
    line rather than print an empty path."""
    msg = build_remediation_message("worker", "Polly")
    assert "Operating guide" not in msg


def test_remediation_message_avoids_system_update_tag() -> None:
    """The audit (#755) cites the prompt-injection defense
    rejecting ``<system-update>``. The canonical wording must
    not use that tag."""
    msg = build_remediation_message("operator_pm", "Russell")
    assert "<system-update>" not in msg


def test_remediation_message_requests_acknowledgement() -> None:
    """Operator visibility requires the session reply with
    ``"OK <persona>"`` before its next action."""
    msg = build_remediation_message("operator_pm", "Russell")
    assert '"OK Polly"' in msg


# ---------------------------------------------------------------------------
# Legacy table reconciliation
# ---------------------------------------------------------------------------


def test_legacy_table_clean_when_in_sync() -> None:
    """When a legacy persona dict matches the registry, no
    disagreements are reported."""
    legacy = {"operator-pm": "Polly", "reviewer": "Russell"}
    assert legacy_persona_table_disagreements(legacy) == ()


def test_legacy_table_flags_persona_disagreement() -> None:
    """A legacy table that names the wrong persona for a known
    role must be flagged so the heartbeat / kickoff cannot
    diverge from the canonical registry (#869)."""
    legacy = {"operator-pm": "Frank"}
    out = legacy_persona_table_disagreements(legacy)
    assert any("operator-pm" in line and "Polly" in line for line in out)


def test_legacy_table_flags_unknown_role() -> None:
    """A legacy table with a role the registry doesn't know about
    is suspicious — likely an obsolete code path that needs to
    be retired or registered."""
    legacy = {"planner": "Pete"}
    out = legacy_persona_table_disagreements(legacy)
    assert any("planner" in line and "unknown" in line.lower() for line in out)


# ---------------------------------------------------------------------------
# Real legacy table — direct reconciliation (release-gate adjacent)
# ---------------------------------------------------------------------------


def test_real_heartbeat_persona_table_matches_registry() -> None:
    """Reconcile :mod:`pollypm.heartbeats.local`'s legacy
    ``_ROLE_PERSONA_NAMES`` dict with the canonical registry.

    A passing test means every persona name the heartbeat uses
    today is the same one the registry declares. A failing test
    is the audit's #869 / #868 shape: persona table drift."""
    from pollypm.heartbeats.local import _ROLE_PERSONA_NAMES
    out = legacy_persona_table_disagreements(_ROLE_PERSONA_NAMES)
    assert out == (), f"persona table disagreement: {out}"
