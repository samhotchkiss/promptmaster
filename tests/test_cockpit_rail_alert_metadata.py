"""Tests for the rail's alert-severity attachment + indicator palette (#989)."""

from __future__ import annotations

from dataclasses import dataclass

from pollypm.cockpit_rail import CockpitItem, PALETTE


@dataclass
class _FakeAlert:
    session_name: str
    alert_type: str
    severity: str
    message: str
    alert_id: int = 0


class _StubRouter:
    """Tiny stand-in exposing only the helpers ``_attach_alert_metadata`` reads."""

    _SILENT_ALERT_TYPES = frozenset({"suspected_loop"})

    _attach_alert_metadata = (
        __import__("pollypm.cockpit_rail", fromlist=["CockpitRouter"])
        .CockpitRouter._attach_alert_metadata
    )
    _alert_session_candidates = (
        __import__("pollypm.cockpit_rail", fromlist=["CockpitRouter"])
        .CockpitRouter._alert_session_candidates
    )


def _attach(item: CockpitItem, alerts: list[_FakeAlert]) -> CockpitItem:
    _StubRouter._attach_alert_metadata(_StubRouter(), item, alerts=alerts)
    return item


def test_attach_alert_metadata_picks_error_over_warn() -> None:
    item = CockpitItem(key="polly", label="Polly", state="! recovery limit")
    alerts = [
        _FakeAlert("operator", "pane:permission_prompt", "warn", "answer y/n"),
        _FakeAlert("operator", "recovery_limit", "error", "STOPPED after 23"),
    ]
    _attach(item, alerts)
    assert item.alert_severity == "error"
    assert item.alert_type == "recovery_limit"
    assert "STOPPED" in (item.alert_message or "")


def test_attach_alert_metadata_warn_severity_when_only_warn_open() -> None:
    item = CockpitItem(
        key="project:demo",
        label="demo",
        state="! pane:permission_prompt",
    )
    alerts = [
        _FakeAlert(
            "architect_demo",
            "pane:permission_prompt",
            "warn",
            "permission prompt waiting",
        ),
    ]
    _attach(item, alerts)
    assert item.alert_severity == "warn"
    assert item.alert_type == "pane:permission_prompt"


def test_attach_alert_metadata_skips_silent_alerts() -> None:
    item = CockpitItem(key="polly", label="Polly", state="idle")
    alerts = [
        _FakeAlert("operator", "suspected_loop", "warn", "noisy heartbeat"),
    ]
    _attach(item, alerts)
    assert item.alert_severity is None


def test_attach_alert_metadata_aggregates_project_session_alerts() -> None:
    """Worker, architect, and plan-gate alerts all roll up onto the project row."""
    item = CockpitItem(key="project:demo", label="demo", state="! plan missing")
    alerts = [
        _FakeAlert("plan_gate-demo", "plan_missing", "warn", "no plan yet"),
    ]
    _attach(item, alerts)
    assert item.alert_severity == "warn"
    assert item.alert_type == "plan_missing"


def test_attach_alert_metadata_unrelated_session_does_not_attach() -> None:
    item = CockpitItem(key="project:demo", label="demo", state="idle")
    alerts = [
        _FakeAlert("worker_other", "recovery_limit", "error", "unrelated"),
    ]
    _attach(item, alerts)
    assert item.alert_severity is None
    assert item.alert_message is None


def test_palette_has_distinct_warn_palette_keys() -> None:
    """Catch accidental palette-key removals — warn-tier badges depend on these."""
    assert "warn_bg" in PALETTE
    assert "warn_text" in PALETTE
    assert "warn_indicator" in PALETTE
    # Sanity: warn indicator is amber-ish, not red.
    r, g, b = PALETTE["warn_indicator"]
    assert r > 200 and g > 150 and b < 150


def test_attach_alert_metadata_matches_underscored_session_alias() -> None:
    """Session names are sanitized to underscores even when the project
    key uses hyphens (``worker_blackjack_trainer`` for project
    ``blackjack-trainer``). The candidate set must include both spellings
    so the rail attaches alerts to the right project row instead of
    silently missing them — exactly the live-state miss for
    ``worker_blackjack_trainer/needs_followup`` on 2026-04-29.
    """
    item = CockpitItem(
        key="project:blackjack-trainer",
        label="blackjack-trainer",
        state="! pane:stuck_on_error",
    )
    alerts = [
        _FakeAlert(
            "worker_blackjack_trainer",
            "pane:stuck_on_error",
            "warn",
            "stuck on error",
        ),
    ]
    _attach(item, alerts)
    assert item.alert_severity == "warn"
    assert item.alert_type == "pane:stuck_on_error"
