"""Shared inbox item adapters for the cockpit inbox screen.

Contract:
- Inputs: loaded config objects, Store message rows, and work-service tasks.
- Outputs: a single inbox-item surface that lets the Textual inbox render
  Store-backed notifications and task-backed inbox rows together.
- Side effects: opens and closes Store / SQLite readers while loading.
- Invariants: item ids are stable across refreshes, message rows stay
  read-only, and task-backed thread replies continue to flow through the
  work-service path unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pollypm.cockpit_inbox import _inbox_db_sources
from pollypm.store import SQLAlchemyStore
from pollypm.work.inbox_view import inbox_tasks
from pollypm.work.sqlite_service import SQLiteWorkService


def message_item_id(source_key: str, row_id: object) -> str:
    """Stable local id for a Store-backed inbox item."""
    return f"msg:{source_key}:{row_id}"


class InboxEntry:
    """Thin adapter so tasks and Store messages share one inbox surface."""

    def __init__(self, *, raw=None, **values: Any) -> None:
        self._raw = raw
        for key, value in values.items():
            setattr(self, key, value)

    def __getattr__(self, name: str):
        raw = self.__dict__.get("_raw")
        if raw is not None:
            return getattr(raw, name)
        raise AttributeError(name)


def task_to_inbox_entry(task, *, db_path: Path | None) -> InboxEntry:
    """Wrap a work-service task in the common inbox-item surface."""
    return InboxEntry(
        raw=task,
        source="task",
        task_id=task.task_id,
        message_id=None,
        message_type=None,
        tier=None,
        state="open",
        sender=getattr(task, "sender", None),
        project=getattr(task, "project", "") or "",
        title=getattr(task, "title", "") or "",
        description=getattr(task, "description", "") or "",
        created_at=getattr(task, "created_at", None),
        updated_at=getattr(task, "updated_at", None),
        priority=getattr(task, "priority", None),
        labels=list(getattr(task, "labels", []) or []),
        roles=getattr(task, "roles", {}) or {},
        created_by=getattr(task, "created_by", "") or "",
        payload={},
        recipient="user",
        scope=getattr(task, "project", "") or "",
        db_path=db_path,
    )


def message_row_to_inbox_entry(
    row: dict[str, Any], *, source_key: str, db_path: Path,
) -> InboxEntry:
    """Project one Store message row into the common inbox-item surface."""
    payload = row.get("payload") or {}
    scope = (row.get("scope") or "").strip()
    project = (
        payload.get("project")
        or scope
        or ("inbox" if source_key == "__workspace__" else source_key)
    )
    tier = row.get("tier") or "immediate"
    message_type = row.get("type") or "notify"
    priority = "high" if tier == "immediate" and message_type == "alert" else "normal"
    return InboxEntry(
        source="message",
        task_id=message_item_id(source_key, row.get("id")),
        message_id=row.get("id"),
        message_type=message_type,
        tier=tier,
        state=row.get("state") or "open",
        sender=row.get("sender") or "polly",
        project=project,
        title=row.get("subject") or "(no subject)",
        description=row.get("body") or "",
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at") or row.get("created_at"),
        priority=priority,
        labels=list(row.get("labels", []) or []),
        roles={},
        created_by=row.get("sender") or "polly",
        payload=payload,
        recipient=row.get("recipient") or "",
        scope=scope,
        db_path=db_path,
    )


def is_task_inbox_entry(item) -> bool:
    """True when the inbox item is backed by a work-service task row."""
    return getattr(item, "source", "task") == "task"


def load_inbox_entries(
    config,
    *,
    session_read_ids: set[str] | None = None,
) -> tuple[list[InboxEntry], set[str], dict[str, list]]:
    """Load Store-backed messages and task-backed inbox items together."""
    session_read_ids = session_read_ids or set()
    items: list[InboxEntry] = []
    unread: set[str] = set()
    replies_by_task: dict[str, list] = {}
    seen_task_ids: set[str] = set()
    for project_key, db_path, project_path in _inbox_db_sources(config):
        if not db_path.exists():
            continue
        source_key = project_key or "__workspace__"
        try:
            store = SQLAlchemyStore(f"sqlite:///{db_path}")
        except Exception:  # noqa: BLE001
            store = None
        if store is not None:
            try:
                try:
                    rows = store.query_messages(
                        recipient="user",
                        state="open",
                        type=["notify", "inbox_task", "alert"],
                    )
                except Exception:  # noqa: BLE001
                    rows = []
                for row in rows:
                    item = message_row_to_inbox_entry(
                        row,
                        source_key=source_key,
                        db_path=db_path,
                    )
                    items.append(item)
                    if item.task_id not in session_read_ids:
                        unread.add(item.task_id)
            finally:
                try:
                    store.close()
                except Exception:  # noqa: BLE001
                    pass
        try:
            svc = SQLiteWorkService(db_path=db_path, project_path=project_path)
        except Exception:  # noqa: BLE001
            continue
        try:
            try:
                project_tasks = inbox_tasks(svc, project=project_key)
            except Exception:  # noqa: BLE001
                project_tasks = []
            for task in project_tasks:
                if task.task_id in seen_task_ids:
                    continue
                seen_task_ids.add(task.task_id)
                items.append(task_to_inbox_entry(task, db_path=db_path))
                try:
                    rows = svc.get_context(task.task_id, entry_type="read", limit=1)
                except Exception:  # noqa: BLE001
                    rows = []
                if not rows:
                    unread.add(task.task_id)
                try:
                    replies = svc.list_replies(task.task_id)
                except Exception:  # noqa: BLE001
                    replies = []
                if replies:
                    replies_by_task[task.task_id] = replies
        finally:
            try:
                svc.close()
            except Exception:  # noqa: BLE001
                pass
    return items, unread, replies_by_task
