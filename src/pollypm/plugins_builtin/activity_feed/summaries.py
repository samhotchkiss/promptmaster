"""Backward-compatible re-export of ``activity_summary``.

The canonical implementation lives in ``pollypm.events.summaries`` so
core (non-plugin) callers — plugin host, job runner, messaging,
version-check — can package structured payloads without importing a
plugin's private module layout (#805). This stub exists so existing
plugin-side imports keep working.

Prefer ``pollypm.events.activity_summary`` for new code.
"""

from __future__ import annotations

from pollypm.events.summaries import activity_summary

__all__ = ["activity_summary"]
