"""Cursor / read-loop hardening for the session-intelligence sweep.

Cycle 103: ``_read_new_events`` had an unreachable ``size < offset``
branch — the preceding ``size <= offset`` check swallowed both
"no new data" and "file truncated/rotated", parking the cursor past
EOF on truncation so the sweep never re-read the new file content.
"""

from __future__ import annotations

import json
from pathlib import Path

from pollypm.session_intelligence import _read_new_events


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def test_read_new_events_resets_cursor_when_file_truncated(tmp_path: Path) -> None:
    """Regression: a transcript file that shrinks below the cursor
    (rotation, manual truncation, replay-after-rewind) used to be
    swallowed by ``size <= offset`` — the cursor stayed pegged past
    EOF and every subsequent sweep returned no events.
    """
    project_root = tmp_path
    session_name = "worker_demo"
    events_path = project_root / ".pollypm" / "transcripts" / session_name / "events.jsonl"

    # Seed a "previous" file size that's larger than the current file
    # to simulate a truncation.
    _write_events(events_path, [{"type": "msg", "n": 1}])
    cursors = {f"{session_name}/events.jsonl": 10_000}

    events, new_offset = _read_new_events(project_root, session_name, cursors)

    assert events, "expected truncated-file rewind to surface the new events"
    assert events[0] == {"type": "msg", "n": 1}
    # New offset must reflect the rewound read — i.e., > 0 and <= file size.
    assert new_offset > 0
    assert new_offset <= events_path.stat().st_size


def test_read_new_events_returns_empty_when_no_new_data(tmp_path: Path) -> None:
    """Sanity: when ``size == offset`` the helper returns no events
    AND keeps the cursor unchanged so the caller's no-op skip path
    stays correct after the truncation fix."""
    project_root = tmp_path
    session_name = "worker_demo"
    events_path = project_root / ".pollypm" / "transcripts" / session_name / "events.jsonl"
    _write_events(events_path, [{"type": "msg", "n": 1}])
    size = events_path.stat().st_size
    cursors = {f"{session_name}/events.jsonl": size}

    events, new_offset = _read_new_events(project_root, session_name, cursors)

    assert events == []
    assert new_offset == size


def test_read_new_events_returns_only_new_lines(tmp_path: Path) -> None:
    """When the file grows past the cursor, only the new tail is
    returned. Guards against a regression where the truncation fix
    accidentally rewinds on every call."""
    project_root = tmp_path
    session_name = "worker_demo"
    events_path = project_root / ".pollypm" / "transcripts" / session_name / "events.jsonl"
    _write_events(events_path, [{"type": "msg", "n": 1}])
    initial_size = events_path.stat().st_size

    # Append a second event without truncating.
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "msg", "n": 2}) + "\n")

    cursors = {f"{session_name}/events.jsonl": initial_size}
    events, new_offset = _read_new_events(project_root, session_name, cursors)

    assert events == [{"type": "msg", "n": 2}]
    assert new_offset == events_path.stat().st_size
