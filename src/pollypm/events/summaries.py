"""Structured-summary helpers for ``record_event`` payloads.

The event-emission sites across the codebase call
``StateStore.record_event(session_name, event_type, message)``. The
schema's ``message`` payload lands in the unified ``messages`` table,
so new emission sites should pack a JSON blob into ``message``
carrying at least ``summary`` + ``severity``; the activity_feed
projector decodes that blob back out.

This module owns the canonical packer. ``plugins_builtin/activity_feed``
re-exports the same name for plugin-side imports.

Lives in ``pollypm.events`` (not ``plugins_builtin/``) so the plugin
host, job runner, messaging, and version-check paths can package
structured payloads without depending on a specific plugin's private
module layout (#805).
"""

from __future__ import annotations

import json
from typing import Any


_KNOWN_SEVERITIES: frozenset[str] = frozenset({"critical", "recommendation", "routine"})


def activity_summary(
    *,
    summary: str,
    severity: str = "routine",
    verb: str | None = None,
    subject: str | None = None,
    project: str | None = None,
    **extra: Any,
) -> str:
    """Serialise a structured activity payload to a JSON string.

    Fields:

    * ``summary`` — one-sentence human-readable description (required).
    * ``severity`` — ``critical``/``recommendation``/``routine``.
      Unknown values are accepted but coerced to ``routine`` by the
      projector.
    * ``verb`` — short past-tense verb (``started``, ``committed``,
      ``blocked``). Optional; the projector falls back to ``event_type``.
    * ``subject`` — the thing the event is about (``task demo/5``,
      ``session worker-foo``). Optional.
    * ``project`` — project key for filtering in the cockpit.
    * ``**extra`` — additional structured fields preserved on the
      feed entry's ``payload`` dict.
    """
    body: dict[str, Any] = {"summary": str(summary)}
    if severity in _KNOWN_SEVERITIES:
        body["severity"] = severity
    else:
        body["severity"] = "routine"
    if verb:
        body["verb"] = str(verb)
    if subject:
        body["subject"] = str(subject)
    if project:
        body["project"] = str(project)
    for key, value in extra.items():
        if value is None:
            continue
        # JSON-serialisable scalars + small containers only. Objects
        # outside that pass through json.dumps' default handling, which
        # raises for non-serialisable values — callers should not pass
        # those in.
        body[key] = value
    return json.dumps(body, separators=(",", ":"))


__all__ = ["activity_summary"]
