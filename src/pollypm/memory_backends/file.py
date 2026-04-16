from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pollypm.memory_backends.base import MemoryBackend, MemoryEntry, MemorySummary

if TYPE_CHECKING:
    from pollypm.storage.state import StateStore


class _PluginHook(Protocol):
    """Narrow subset of ExtensionHost the file backend consumes.

    Keeping this as a structural protocol (not an import of ExtensionHost)
    is what keeps memory_backends from depending on plugin_host. The caller
    may pass an ExtensionHost, a test stub, or ``None`` (no plugin hooks).
    """

    def run_filters(self, hook_name: str, payload: object, *, metadata: dict | None = ...) -> object: ...

    def run_observers(self, hook_name: str, payload: object, *, metadata: dict | None = ...) -> object: ...


class _NullHook:
    def run_filters(self, hook_name, payload, *, metadata=None):
        # Mirror HookFilterResult shape minimally: allow-through.
        class _AllowResult:
            action = "allow"
            payload = None
            reason = None

        result = _AllowResult()
        result.payload = payload
        return result

    def run_observers(self, hook_name, payload, *, metadata=None):
        return []


class FileMemoryBackend(MemoryBackend):
    """File-backed memory store.

    Dependencies are injected via the constructor:

    * ``project_path`` — resolved project root used for file placement.
    * ``state_store`` — already-constructed StateStore handle.
    * ``plugins`` — filter/observer dispatcher (ExtensionHost-shaped);
      defaults to a no-op if omitted, so tests don't need a plugin host.
    * ``memory_root`` / ``artifacts_root`` — optional pre-resolved output
      directories. When omitted, the backend uses ``<project_path>/.pollypm``
      conventions so the happy path stays the same.

    The backend no longer imports ``plugin_host``, ``projects``, or
    constructs its own ``StateStore`` — those are the caller's concern (see
    :func:`pollypm.memory_backends.get_memory_backend`).
    """

    def __init__(
        self,
        project_path: Path,
        *,
        state_store: "StateStore | None" = None,
        plugins: _PluginHook | None = None,
        memory_root: Path | None = None,
        artifacts_root: Path | None = None,
        state_db: Path | None = None,
    ) -> None:
        self._project_path = project_path.expanduser().resolve()
        # Back-compat: if callers pass state_db/no state_store, construct one.
        # This keeps FileMemoryBackend(tmp_path) working for direct users,
        # but new code is expected to inject via get_memory_backend().
        if state_store is None:
            from pollypm.storage.state import StateStore

            self._state_db = state_db or (self._project_path / ".pollypm-state" / "state.db")
            self._state_store = StateStore(self._state_db)
        else:
            self._state_db = state_db
            self._state_store = state_store
        self._plugins: _PluginHook = plugins if plugins is not None else _NullHook()
        # Conventional layout under .pollypm/ — callers may override.
        dossier_root = self._project_path / ".pollypm"
        self._memory_root = memory_root or (dossier_root / "memory")
        self._artifacts_root = artifacts_root or (dossier_root / "artifacts")

    # Exposed read-only accessors for callers that need the same handles.
    # Tests and a few callers need the store and project path; exposing
    # them as properties keeps the ivars private while preserving the
    # existing read surface (no direct writes from outside).
    @property
    def project_path(self) -> Path:
        return self._project_path

    @property
    def store(self) -> "StateStore":
        return self._state_store

    def root(self) -> Path:
        return self._project_path

    def exists(self) -> bool:
        # Historically tied to the dossier dir; the backend can only answer
        # whether its own memory root exists without reaching into projects.
        return self._memory_root.parent.exists()

    def ensure_memory(self) -> Path:
        self._memory_root.mkdir(parents=True, exist_ok=True)
        (self._artifacts_root / "memory").mkdir(parents=True, exist_ok=True)
        return self._memory_root

    def write_entry(
        self,
        *,
        scope: str,
        title: str,
        body: str,
        kind: str = "note",
        tags: list[str] | None = None,
        source: str = "manual",
    ) -> MemoryEntry:
        payload = {
            "scope": scope,
            "title": title,
            "body": body,
            "kind": kind,
            "tags": list(tags or []),
            "source": source,
        }
        result = self._plugins.run_filters("memory.before_write", payload, metadata={"scope": scope, "kind": kind})
        action = getattr(result, "action", "allow")
        if action == "deny":
            raise PermissionError(getattr(result, "reason", None) or "Memory write denied by plugin")
        mutated = getattr(result, "payload", None)
        payload = mutated if isinstance(mutated, dict) else payload

        scope = str(payload.get("scope", scope))
        title = str(payload.get("title", title))
        body = str(payload.get("body", body))
        kind = str(payload.get("kind", kind))
        tags = [str(tag) for tag in payload.get("tags", tags or [])]
        source = str(payload.get("source", source))

        self.ensure_memory()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        slug = _slugify(title)[:80]  # Truncate to avoid "File name too long" errors
        scope_dir = self._memory_root / scope
        scope_dir.mkdir(parents=True, exist_ok=True)
        file_path = scope_dir / f"{stamp}-{slug}.md"
        summary_path = file_path
        content = _render_entry(scope=scope, title=title, body=body, kind=kind, tags=tags, source=source)
        file_path.write_text(content)

        record = self._state_store.record_memory_entry(
            scope=scope,
            kind=kind,
            title=title,
            body=body,
            tags=tags,
            source=source,
            file_path=str(file_path),
            summary_path=str(summary_path),
        )
        entry = MemoryEntry(
            entry_id=record.entry_id,
            scope=record.scope,
            kind=record.kind,
            title=record.title,
            body=record.body,
            tags=record.tags,
            source=record.source,
            file_path=Path(record.file_path),
            summary_path=Path(record.summary_path),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self._plugins.run_observers("memory.after_write", entry, metadata={"scope": scope, "kind": kind})
        return entry

    def list_entries(
        self,
        *,
        scope: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        entries = self._state_store.list_memory_entries(scope=scope, kind=kind, limit=limit)
        return [
            MemoryEntry(
                entry_id=item.entry_id,
                scope=item.scope,
                kind=item.kind,
                title=item.title,
                body=item.body,
                tags=item.tags,
                source=item.source,
                file_path=Path(item.file_path),
                summary_path=Path(item.summary_path),
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in entries
        ]

    def read_entry(self, entry_id: int) -> MemoryEntry | None:
        entry = self._state_store.get_memory_entry(entry_id)
        if entry is None:
            return None
        memory_entry = MemoryEntry(
            entry_id=entry.entry_id,
            scope=entry.scope,
            kind=entry.kind,
            title=entry.title,
            body=entry.body,
            tags=entry.tags,
            source=entry.source,
            file_path=Path(entry.file_path),
            summary_path=Path(entry.summary_path),
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )
        self._plugins.run_observers("memory.after_read", memory_entry, metadata={"scope": entry.scope, "kind": entry.kind})
        return memory_entry

    def summarize(self, scope: str, *, limit: int = 20) -> str:
        entries = self.list_entries(scope=scope, limit=limit)
        summary = _summarize_entries(scope, entries)
        self._plugins.run_observers("memory.after_summarize", summary, metadata={"scope": scope, "entry_count": len(entries)})
        return summary

    def compact(self, scope: str, *, limit: int = 50) -> MemorySummary:
        entries = self.list_entries(scope=scope, limit=limit)
        summary_text = _summarize_entries(scope, entries)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        summary_dir = self._artifacts_root / "memory" / scope
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / f"{stamp}.md"
        summary_path.write_text(summary_text)
        record = self._state_store.record_memory_summary(
            scope=scope,
            summary_text=summary_text,
            summary_path=str(summary_path),
            entry_count=len(entries),
        )
        summary = MemorySummary(
            summary_id=record.summary_id,
            scope=record.scope,
            summary_text=record.summary_text,
            summary_path=Path(record.summary_path),
            entry_count=record.entry_count,
            created_at=record.created_at,
        )
        self._plugins.run_observers("memory.after_compact", summary, metadata={"scope": scope, "entry_count": len(entries)})
        return summary


def _render_entry(*, scope: str, title: str, body: str, kind: str, tags: list[str], source: str) -> str:
    tag_line = ", ".join(tags) if tags else "none"
    return "\n".join(
        [
            f"# {title}",
            "",
            f"- Scope: `{scope}`",
            f"- Kind: `{kind}`",
            f"- Source: `{source}`",
            f"- Tags: {tag_line}",
            "",
            body.rstrip(),
            "",
        ]
    )


def _summarize_entries(scope: str, entries: list[MemoryEntry]) -> str:
    lines = [
        f"# Memory Summary: {scope}",
        "",
        f"- Entries: {len(entries)}",
        "",
    ]
    if not entries:
        lines.append("No memory entries recorded yet.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Recent Entries")
    lines.append("")
    for entry in entries[:10]:
        snippet = " ".join(entry.body.split())[:140]
        lines.append(f"- {entry.title}: {snippet}")
    lines.append("")
    return "\n".join(lines)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "memory"
