"""Tests for the per-alert recovery action map (#989)."""

from __future__ import annotations

import pytest

from pollypm.cockpit_alert_actions import (
    AlertActionPlan,
    recovery_actions_for,
    task_id_from_alert_type,
)


def _kinds(plans: list[AlertActionPlan]) -> list[str]:
    return [plan.kind for plan in plans]


def test_recovery_limit_offers_resume_restart_and_account_repair() -> None:
    plans = recovery_actions_for(
        "recovery_limit",
        session_name="worker_demo",
    )

    # The user-named recovery actions land first (in priority order),
    # with the universal Acknowledge fallback at the end.
    assert _kinds(plans) == [
        "resume_recovery",
        "restart_session",
        "route_settings_accounts",
        "acknowledge",
    ]
    # The session_name plumbs through to the resume/restart plans so
    # the Textual handler can act on it without re-querying the rail.
    resume = plans[0]
    assert resume.session_name == "worker_demo"
    assert resume.label == "Resume auto-recovery"


def test_recovery_limit_without_session_only_offers_acknowledge() -> None:
    """Defensive: a missing session_name must not crash the action builder."""
    plans = recovery_actions_for("recovery_limit")
    assert _kinds(plans) == ["acknowledge"]


def test_pane_permission_prompt_offers_view_and_acknowledge() -> None:
    plans = recovery_actions_for(
        "pane:permission_prompt",
        session_name="architect_demo",
        project_key="demo",
    )

    assert _kinds(plans) == ["view_pane", "acknowledge"]
    assert plans[0].session_name == "architect_demo"


def test_pane_stuck_on_error_offers_view_restart_and_acknowledge() -> None:
    plans = recovery_actions_for(
        "pane:stuck_on_error",
        session_name="worker_demo",
    )

    assert _kinds(plans) == ["view_pane", "restart_session", "acknowledge"]


def test_no_session_for_assignment_routes_to_inbox() -> None:
    plans = recovery_actions_for(
        "no_session_for_assignment:demo/3",
        project_key="demo",
        task_id="demo/3",
    )

    assert _kinds(plans) == ["route_inbox", "acknowledge"]
    assert plans[0].project_key == "demo"
    assert plans[0].task_id == "demo/3"


def test_plan_missing_routes_to_chat_pm() -> None:
    plans = recovery_actions_for(
        "plan_missing",
        project_key="demo",
        session_name="plan_gate-demo",
    )

    assert _kinds(plans) == ["route_chat_pm", "acknowledge"]
    assert plans[0].project_key == "demo"


def test_plan_missing_without_project_only_offers_acknowledge() -> None:
    plans = recovery_actions_for("plan_missing")
    assert _kinds(plans) == ["acknowledge"]


def test_unknown_alert_type_falls_back_to_acknowledge() -> None:
    """Anything unmapped still gets the universal Acknowledge button."""
    plans = recovery_actions_for("never_heard_of_it", session_name="worker_demo")
    assert _kinds(plans) == ["acknowledge"]


def test_acknowledge_is_appended_only_once() -> None:
    """Avoid double-acknowledge for families that already include it explicitly."""
    plans = recovery_actions_for(
        "pane:permission_prompt",
        session_name="architect_demo",
    )
    assert _kinds(plans).count("acknowledge") == 1


@pytest.mark.parametrize(
    ("alert_type", "expected"),
    [
        ("no_session_for_assignment:demo/3", "demo/3"),
        ("stuck_on_task:demo/9", "demo/9"),
        ("recovery_limit", None),
        ("pane:permission_prompt", None),
        ("", None),
    ],
)
def test_task_id_from_alert_type(alert_type: str, expected: str | None) -> None:
    assert task_id_from_alert_type(alert_type) == expected
