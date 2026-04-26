"""Tests for mid-flight persona-drift detection (#757)."""

from __future__ import annotations

import pytest

from pollypm.supervisor import detect_persona_drift


# ---------------------------------------------------------------------------
# Positive: drift detected
# ---------------------------------------------------------------------------


def test_drift_detected_on_standing_by_as_wrong_persona() -> None:
    """Classic observed phrasing from the Notesy incident tonight."""
    pane = "Standing by as Russell. Please tell me which of these you want."
    assert detect_persona_drift("operator-pm", pane) == "Russell"


def test_drift_detected_on_holding_as_wrong_persona() -> None:
    pane = "Holding as Russell. Waiting for Sam to confirm."
    assert detect_persona_drift("operator-pm", pane) == "Russell"


def test_drift_detected_on_i_am_wrong_persona() -> None:
    pane = "I am Russell, the code reviewer for this repo."
    assert detect_persona_drift("operator-pm", pane) == "Russell"


def test_drift_detected_on_acting_as_wrong_persona() -> None:
    pane = "Acting as Polly, the project PM for this session."
    assert detect_persona_drift("reviewer", pane) == "Polly"


def test_drift_detected_on_initialized_as_wrong_persona() -> None:
    pane = "Initialized as Russell the code reviewer and waiting for tasks."
    assert detect_persona_drift("architect", pane) == "Russell"


def test_drift_detected_is_case_insensitive() -> None:
    pane = "STANDING BY AS RUSSELL"
    assert detect_persona_drift("operator-pm", pane) == "Russell"


# ---------------------------------------------------------------------------
# Negative: no drift detected
# ---------------------------------------------------------------------------


def test_no_drift_when_pane_empty() -> None:
    assert detect_persona_drift("operator-pm", "") is None


def test_no_drift_when_role_empty() -> None:
    assert detect_persona_drift("", "Standing by as Russell") is None


def test_no_drift_on_casual_mention() -> None:
    """Neutral references to another persona must not trip the detector."""
    pane = "Let me notify Russell about the review. Polly out."
    assert detect_persona_drift("operator-pm", pane) is None


def test_no_drift_when_expected_marker_also_present() -> None:
    """When the session's own marker is visible alongside another, the
    session is legitimately discussing / reviewing the other persona —
    not having an identity crisis."""
    pane = (
        "Polly reviewing Russell's last report.\n"
        "Standing by as Russell — [Russell quoted in transcript]"
    )
    # operator-pm (Polly) is present, so even a "Standing by as
    # Russell" quote doesn't count as drift.
    assert detect_persona_drift("operator-pm", pane) is None


def test_no_drift_on_own_persona_claim() -> None:
    """A session claiming its own identity is fine — no drift."""
    pane = "I am Polly, your operator. Inbox has 3 items."
    assert detect_persona_drift("operator-pm", pane) is None


def test_unknown_role_still_detects_drift_to_known_persona() -> None:
    """Even when the session's own role has no registered marker (e.g.
    worker), the detector still flags when the pane claims a KNOWN
    wrong persona — a worker saying 'Standing by as Russell' is drift
    regardless of whether worker has a canonical marker."""
    pane = "Standing by as Russell"
    # Worker isn't in the marker map (no expected marker), but the
    # pane clearly claims Russell's identity → drift.
    assert detect_persona_drift("worker", pane) == "Russell"


def test_no_drift_on_partial_match() -> None:
    """Loose substring tricks (e.g. 'Russell' embedded in a URL) must
    not trip the detector — the identity-claim patterns are explicit."""
    pane = "Pushed to https://github.com/org/russell-tool/pull/1"
    assert detect_persona_drift("operator-pm", pane) is None


def test_persona_reassertion_message_names_canonical_persona() -> None:
    """#757 — the heartbeat's reactive-remediation message must:
    name the session's canonical persona, name the persona the
    session drifted into (so the model has context), point at the
    operating guide for re-anchoring, and state the explicit ack
    phrase the model should produce so the operator can verify the
    drift cleared on the next snapshot.

    Avoids the ``<system-update>`` tag — prompt-injection defenses
    correctly reject that shape (#755). Uses an owner-tagged plain
    message instead.
    """
    from pollypm.heartbeats.local import (
        _build_persona_reassertion_message,
    )

    msg = _build_persona_reassertion_message(
        role="operator-pm", drifted_to="Russell",
    )

    assert "<system-update>" not in msg  # #755 — never use this shape
    assert "Polly" in msg  # canonical persona for operator-pm
    assert "Russell" in msg  # the persona we drifted INTO
    assert "polly-operator-guide.md" in msg  # operating guide path
    assert "OK Polly" in msg  # ack phrase

    # Reviewer drift to Polly — same shape, mirror role.
    rev_msg = _build_persona_reassertion_message(
        role="reviewer", drifted_to="Polly",
    )
    assert "Russell" in rev_msg
    assert "russell.md" in rev_msg
    assert "OK Russell" in rev_msg
