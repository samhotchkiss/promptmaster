"""Inbox delivery — ensures agent-targeted messages reach their tmux sessions.

Runs as an async job on the heartbeat cycle. For each open message where
`to` is an agent (not "user"), delivers the message content to the agent's
tmux session via send_input. Tracks delivery state so failed deliveries
get retried on the next cycle.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pollypm.inbox_v2 import list_messages, mark_delivered, read_message

if TYPE_CHECKING:
    from pollypm.models import PollyPMConfig

logger = logging.getLogger(__name__)

# Don't re-deliver within this window
_DELIVERY_COOLDOWN_SECONDS = 120

# Re-nudge if agent has been idle this long after delivery
_IDLE_REDELIVERY_SECONDS = 180

# Map recipient names to session names
_RECIPIENT_SESSION_MAP = {
    "polly": "operator",
    "operator": "operator",
}


def _resolve_session_name(recipient: str) -> str:
    """Map a recipient name to a tmux session name."""
    return _RECIPIENT_SESSION_MAP.get(recipient, recipient)


def deliver_pending_messages(config: PollyPMConfig) -> dict[str, int]:
    """Deliver all pending messages to their target agents.

    Returns counts: delivered, failed, skipped.
    """
    from pollypm.supervisor import Supervisor

    counts = {"delivered": 0, "failed": 0, "skipped": 0}
    root = config.project.root_dir

    messages = list_messages(root, status="open")
    pending = [
        m for m in messages
        if m.to and m.to != "user" and m.delivery_state in ("pending", "failed")
    ]

    if not pending:
        return counts

    sup = Supervisor(config)

    for msg in pending:
        # Rate limit — don't re-deliver within cooldown
        if msg.last_delivered_at:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(msg.last_delivered_at)).total_seconds()
                if age < _DELIVERY_COOLDOWN_SECONDS:
                    counts["skipped"] += 1
                    continue
            except (ValueError, TypeError):
                pass

        session_name = _resolve_session_name(msg.to)

        # Read the latest entry from the thread
        # Keep the delivery payload short — just a notification to check inbox.
        # The agent reads the full content via pm mail when ready.
        payload = f"[Inbox] New message: {msg.subject[:60]} — run: pm mail {msg.id}"

        try:
            sup.send_input(session_name, payload, owner="pollypm", force=True)
            mark_delivered(root, msg.id, state="delivered")
            counts["delivered"] += 1
            logger.info("Delivered inbox message %s to %s", msg.id[:30], session_name)
        except Exception as exc:  # noqa: BLE001
            mark_delivered(root, msg.id, state="failed")
            counts["failed"] += 1
            logger.warning("Failed to deliver %s to %s: %s", msg.id[:30], session_name, exc)

    sup.store.close()
    return counts


def check_active_work(config: PollyPMConfig) -> dict[str, int]:
    """Check that delivered messages are being actively worked on.

    If an agent has been idle since delivery, reset to pending for re-delivery.
    """
    from pollypm.storage.state import StateStore

    counts = {"requeued": 0, "active": 0}
    root = config.project.root_dir

    messages = list_messages(root, status="open")
    delivered = [
        m for m in messages
        if m.to and m.to != "user" and m.delivery_state == "delivered"
    ]

    if not delivered:
        return counts

    store = StateStore(config.project.state_db)

    for msg in delivered:
        session_name = _resolve_session_name(msg.to)
        rt = store.get_session_runtime(session_name)

        if rt and rt.status in ("healthy", "needs_followup"):
            counts["active"] += 1
            continue

        # Agent is idle — check how long since delivery
        if msg.last_delivered_at:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(msg.last_delivered_at)).total_seconds()
                if age > _IDLE_REDELIVERY_SECONDS:
                    mark_delivered(root, msg.id, state="pending")
                    counts["requeued"] += 1
                    logger.info("Requeued %s — agent %s idle %ds after delivery", msg.id[:30], session_name, int(age))
                else:
                    counts["active"] += 1
            except (ValueError, TypeError):
                counts["active"] += 1
        else:
            counts["active"] += 1

    store.close()
    return counts


def deliver_single_message(config: PollyPMConfig, msg_id: str) -> bool:
    """Deliver a single message immediately. Returns True on success.

    Called from cockpit UI reply and CLI reply for instant delivery
    instead of waiting for the next heartbeat cycle.
    """
    from pollypm.supervisor import Supervisor

    root = config.project.root_dir
    messages = list_messages(root, status="open")
    msg = next((m for m in messages if m.id == msg_id), None)

    if msg is None or msg.to == "user" or msg.to == "":
        return False

    session_name = _resolve_session_name(msg.to)
    payload = f"[Inbox] New message: {msg.subject[:60]} — run: pm mail {msg_id}"

    sup = Supervisor(config)
    try:
        sup.send_input(session_name, payload, owner="pollypm", force=True)
        mark_delivered(root, msg_id, state="delivered")
        logger.info("Immediate delivery of %s to %s", msg_id[:30], session_name)
        return True
    except Exception as exc:  # noqa: BLE001
        mark_delivered(root, msg_id, state="failed")
        logger.warning("Immediate delivery failed for %s to %s: %s", msg_id[:30], session_name, exc)
        return False
    finally:
        sup.store.close()
