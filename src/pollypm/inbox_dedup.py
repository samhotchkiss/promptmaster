"""Inbox dedup-key collapsing for repeated meta-reports (#1013, sub-bug C).

Pre-#1013 every ``pm notify`` call inserted a fresh row, even when the
producer was raising the *same* alert pattern repeatedly. The user's
verified inbox had 12 separate "Nth suspected fake RECOVERY MODE
injection" rows for one ongoing pattern. The fix is producer-side
collapsing keyed on a stable string identifier the caller chooses.

This module owns the dedup lookup + count-update logic. Storage is
intentionally schema-light: the dedup state lives in the message's
``payload_json`` blob (``dedup_key``, ``count``, ``last_seen``) so we
don't migrate the unified ``messages`` table for a single sub-bug. If
dedup proves load-bearing post-v1, promoting these to first-class
columns is a clean follow-up.

Contract:

* ``find_open_dedup_message(store, dedup_key, recipient)`` — return
  the open message row whose ``payload.dedup_key`` matches, or ``None``.
* ``bump_dedup_message(store, row, *, subject, body, payload, ...)`` —
  refresh the existing row's count, last_seen, subject, body, and
  payload. Caller decides what to keep stable across repeats (typically:
  scope, sender, recipient stay; subject/body update).
* ``initial_dedup_payload(payload, dedup_key, now)`` — annotate a
  fresh insert's payload with ``count=1`` and ``last_seen``. Producer
  calls this before passing payload to ``enqueue_message`` so even the
  first row is dedup-ready.

The shape (``count``, ``last_seen``) matches the issue's suggested
listing format ("9x - last seen 2 days ago").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def find_open_dedup_message(
    store,
    dedup_key: str,
    *,
    recipient: str = "user",
) -> dict[str, Any] | None:
    """Return the live notify whose ``payload.dedup_key`` matches.

    "Live" here means any non-closed state — ``open`` (immediate) and
    ``staged`` (digest) both qualify so digest-tier callers can also
    collapse. Returns ``None`` when no match exists (or when
    ``dedup_key`` is empty — empty keys never collapse). Best-effort:
    a query failure is logged + treated as "no match" so the caller
    falls back to insert-as-new.
    """
    if not dedup_key:
        return None
    try:
        rows = store.query_messages(
            type="notify",
            state=["open", "staged"],
            recipient=recipient,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "dedup lookup failed for key=%r", dedup_key, exc_info=True,
        )
        return None
    for row in rows:
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if str(payload.get("dedup_key") or "") == dedup_key:
            return row
    return None


def initial_dedup_payload(
    payload: dict[str, Any],
    dedup_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Annotate a fresh-insert payload with dedup state.

    Returns a NEW dict — caller's input is left unchanged. ``count`` is
    seeded to 1 so the listing-side format ("9x - last seen ...") only
    needs to render when count > 1.
    """
    if not dedup_key:
        return dict(payload)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    out = dict(payload)
    out["dedup_key"] = dedup_key
    out["count"] = 1
    out["last_seen"] = stamp
    return out


def bump_dedup_message(
    store,
    row: dict[str, Any],
    *,
    subject: str,
    body: str,
    payload: dict[str, Any],
    labels: list[str] | None = None,
    tier: str | None = None,
    now: datetime | None = None,
) -> int:
    """Increment the existing row's count + refresh subject/body/payload.

    Returns the message id so callers can keep the same return shape as
    ``enqueue_message`` (which returns the inserted id). The producer
    keeps the existing row open — repeats stack onto a single inbox
    entry rather than spawning a new one.
    """
    msg_id = int(row.get("id"))
    existing_payload = row.get("payload") or {}
    if not isinstance(existing_payload, dict):
        existing_payload = {}
    prev_count = int(existing_payload.get("count") or 1)
    stamp = (now or datetime.now(timezone.utc)).isoformat()

    merged_payload = {
        **existing_payload,
        **payload,
        "count": prev_count + 1,
        "last_seen": stamp,
    }
    # Caller-provided dedup_key wins — it's the lookup key after all,
    # but we never want to drop it during a refresh.
    if "dedup_key" not in merged_payload and "dedup_key" in existing_payload:
        merged_payload["dedup_key"] = existing_payload["dedup_key"]

    fields: dict[str, Any] = {
        "subject": subject,
        "body": body,
        "payload": merged_payload,
    }
    if labels is not None:
        fields["labels"] = list(labels)
    if tier is not None:
        fields["tier"] = tier

    store.update_message(msg_id, **fields)
    return msg_id


def format_dedup_suffix(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """Render a ``9x - last seen 2d ago`` style suffix when count > 1.

    Returns the empty string when the payload carries no dedup state
    or when count is 1 (so the listing renderer can unconditionally
    concat without a special-case branch).
    """
    if not isinstance(payload, dict):
        return ""
    count = payload.get("count")
    if not isinstance(count, int) or count <= 1:
        return ""
    last_seen_raw = payload.get("last_seen")
    age_phrase = ""
    if isinstance(last_seen_raw, str) and last_seen_raw:
        try:
            last_seen = datetime.fromisoformat(last_seen_raw)
        except ValueError:
            last_seen = None
        if last_seen is not None:
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            reference = now or datetime.now(timezone.utc)
            delta = reference - last_seen
            seconds = int(delta.total_seconds())
            if seconds < 60:
                age_phrase = "just now"
            elif seconds < 3600:
                minutes = seconds // 60
                age_phrase = f"{minutes}m ago"
            elif seconds < 86_400:
                hours = seconds // 3600
                age_phrase = f"{hours}h ago"
            else:
                days = seconds // 86_400
                age_phrase = f"{days}d ago"
    if age_phrase:
        return f"{count}x - last seen {age_phrase}"
    return f"{count}x"


__all__ = [
    "bump_dedup_message",
    "find_open_dedup_message",
    "format_dedup_suffix",
    "initial_dedup_payload",
]
