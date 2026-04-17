"""Advisor inbox auto-close sweep.

Runs every 12 hours. Closes advisor_insight inbox entries that have had
no user action for 7 days. ad01 ships this as a stub; ad05 wires it to
the real inbox-close path once the inbox integration lands.
"""
from __future__ import annotations

from typing import Any


def advisor_autoclose_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Stub auto-close sweep. Real implementation in ad05."""
    return {"fired": False, "reason": "stub"}
