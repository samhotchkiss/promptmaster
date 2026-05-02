"""Atomic file write utilities to prevent data loss on crash."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _atomic_write(path: Path, content: str) -> None:
    """Shared atomic-write core for text and JSON variants.

    Guarantees the fd returned by ``tempfile.mkstemp`` is closed on every
    exit path. The previous implementation called ``os.fdopen(fd, ...)``
    inline inside a ``with``, which leaks the fd if ``os.fdopen`` itself
    raises (e.g., low memory) before the ``with`` takes ownership. Under
    sustained ingestion-loop traffic that single-fd leak compounds into
    EMFILE (issue #1032).

    The fix: wrap the fd in a Python file object inside an outer
    try/finally so failure between ``mkstemp`` and ``with-entry`` still
    closes the fd. ``handle = None`` lets the finally distinguish the
    "fdopen succeeded — handle owns the fd" case from the "fdopen raised
    — we still own the raw fd" case.

    Always writes UTF-8 — see ``atomic_write_json`` for rationale.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    handle = None
    try:
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            # fdopen failed — fd was never adopted, so close it directly.
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        # From here, ``handle`` owns the fd; closing handle closes the fd.
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.rename(tmp_path, path)
    except BaseException:
        # Clean up temp file on any failure (rename failure, write failure,
        # or fdopen failure above). Errors here are swallowed — the temp
        # file may not exist if mkstemp itself somehow partially failed.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: object, *, indent: int = 2) -> None:
    """Write JSON to a file atomically using write-to-temp + rename.

    If the process crashes mid-write, the original file is untouched.
    On POSIX, os.rename() is atomic within the same filesystem.

    Always writes UTF-8 — a non-UTF-8 default locale (legacy Linux
    install, Windows CP-1252) would otherwise mangle emojis in commit
    messages, notify subjects, or any non-ASCII project name. JSON
    readers downstream (``json.loads(path.read_text())`` everywhere)
    expect UTF-8.
    """
    _atomic_write(path, json.dumps(data, indent=indent) + "\n")


def atomic_write_text(path: Path, content: str) -> None:
    """Write text to a file atomically.

    Always writes UTF-8 — see ``atomic_write_json`` for rationale.
    """
    _atomic_write(path, content)
