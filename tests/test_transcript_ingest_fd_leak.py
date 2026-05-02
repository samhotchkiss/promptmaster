"""Regression for #1032 — transcript ingestion fd leak under failure paths.

#1019 fixed StateStore-related fd leaks but missed the
``tempfile.mkstemp`` / ``os.fdopen`` pair inside
``pollypm.atomic_io._atomic_write`` (called by
``transcript_ingest._save_cursor_state``). If anything between
``mkstemp`` returning a raw fd and the ``with os.fdopen(fd, ...)`` block
adopting it raises (e.g., ``os.fdopen`` itself, or — once #1019 hardened
it — a synthetic fault injected mid-rename), the fd would dangle. Under
the recurring ``sync_transcripts_once`` heartbeat that single-fd leak
compounded into EMFILE storms (issue #1032 logged 519 occurrences in a
single session).

These tests run ``sync_transcripts_once`` ~100× with two fault
injections — a mid-rename fault and a synthetic ``os.fdopen`` failure —
and assert the parent process's fd count does not grow. The fd-count
sample uses ``os.listdir('/dev/fd')`` on POSIX; on platforms without
``/dev/fd`` the test is skipped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pollypm import atomic_io, transcript_ingest
from pollypm.config import write_config
from pollypm.models import (
    AccountConfig,
    KnownProject,
    PollyPMConfig,
    PollyPMSettings,
    ProjectKind,
    ProjectSettings,
    ProviderKind,
    SessionConfig,
)
from pollypm.transcript_ingest import sync_transcripts_once


_DEV_FD = Path("/dev/fd")


def _fd_count() -> int:
    # listdir itself opens an fd to read the directory; we sample twice
    # below and take the same call shape on both samples so any constant
    # bias cancels.
    return len(os.listdir(_DEV_FD))


def _config(tmp_path: Path) -> PollyPMConfig:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    config = PollyPMConfig(
        project=ProjectSettings(
            name="pollypm",
            root_dir=project_root,
            base_dir=project_root / ".pollypm",
            logs_dir=project_root / ".pollypm/logs",
            snapshots_dir=project_root / ".pollypm/snapshots",
            state_db=project_root / ".pollypm/state.db",
        ),
        pollypm=PollyPMSettings(controller_account="claude_main"),
        accounts={
            "claude_main": AccountConfig(
                name="claude_main",
                provider=ProviderKind.CLAUDE,
                home=project_root / ".pollypm/homes/claude_main",
            ),
        },
        sessions={
            "heartbeat": SessionConfig(
                name="heartbeat",
                role="heartbeat-supervisor",
                provider=ProviderKind.CLAUDE,
                account="claude_main",
                cwd=project_root,
            )
        },
        projects={
            "demo": KnownProject(
                key="demo",
                path=project_root,
                name="Demo",
                kind=ProjectKind.GIT,
            )
        },
    )
    write_config(config, project_root / "pollypm.toml", force=True)

    # Seed a tiny transcript so _save_cursor_state has work to do every
    # iteration — without input data, sync_transcripts_once short-circuits
    # before the cursor save and the leak path isn't exercised.
    claude_file = config.accounts["claude_main"].home / ".claude/projects/demo/session-a.jsonl"
    claude_file.parent.mkdir(parents=True, exist_ok=True)
    claude_file.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T00:00:00Z",
                "type": "user",
                "sessionId": "session-a",
                "cwd": str(project_root),
                "message": {"content": "ping"},
            }
        )
        + "\n"
    )
    return config


@pytest.mark.skipif(not _DEV_FD.exists(), reason="needs /dev/fd to sample fd count")
def test_sync_transcripts_once_does_not_leak_fds_on_rename_failure(tmp_path: Path, monkeypatch) -> None:
    """Inject a mid-rename failure in atomic_io and confirm fd count stays flat.

    Before #1032's fix, the rename failure was already cleanly handled
    (the ``with os.fdopen(...)`` block had closed the fd before rename
    fired). This case is therefore a baseline check that the happy-path
    cleanup still works under failure-path raises — if it ever regresses
    we want to catch it here, not in production logs.
    """
    config = _config(tmp_path)

    # First call seeds the cursor file so subsequent calls hit the
    # rename path with a real target.
    sync_transcripts_once(config)

    real_rename = os.rename

    def boom_rename(src, dst):  # pragma: no cover — invoked under monkeypatch
        # Only fail rewrites of the cursor-state file; let the rest of
        # the system (if any) use the real implementation.
        if str(dst).endswith(".ingestion-state.json"):
            # Simulate a transient rename failure (disk full, etc.).
            raise OSError("synthetic rename failure for fd-leak regression")
        return real_rename(src, dst)

    monkeypatch.setattr(atomic_io.os, "rename", boom_rename)

    baseline = _fd_count()
    for _ in range(100):
        # The rename failure propagates out of _save_cursor_state and
        # then sync_transcripts_once. We catch and continue — production
        # catches this in TranscriptIngestor._run.
        try:
            sync_transcripts_once(config)
        except OSError:
            pass
    after = _fd_count()

    # Allow a tiny slack (1) for incidental fds from the test runner —
    # but the leak we're guarding against would be ~100, not ~1.
    assert after - baseline <= 1, (
        f"fd count grew from {baseline} to {after} over 100 iterations "
        f"with rename-failure injection — likely fd leak in atomic_io / "
        f"transcript_ingest._save_cursor_state"
    )


@pytest.mark.skipif(not _DEV_FD.exists(), reason="needs /dev/fd to sample fd count")
def test_atomic_write_text_does_not_leak_fd_when_fdopen_raises(tmp_path: Path, monkeypatch) -> None:
    """The narrow regression for #1032: if ``os.fdopen`` itself raises
    after ``tempfile.mkstemp`` has handed us a raw fd, the fd must still
    be closed. Previously the ``with os.fdopen(fd, ...) as f:`` form
    leaked the fd in this case because the ``with`` only takes ownership
    once ``os.fdopen`` returns.
    """
    real_fdopen = os.fdopen
    fail_count = {"n": 0}

    def flaky_fdopen(fd, *args, **kwargs):
        fail_count["n"] += 1
        # Close the fd ourselves to simulate fdopen-raised-but-fd-was-leaked
        # would *not* be the real bug shape. Real bug: fdopen raises
        # without closing fd. So we raise without closing.
        raise OSError("synthetic fdopen failure for fd-leak regression")

    target = tmp_path / "data.txt"
    # Seed the file so the test exercises the overwrite path.
    atomic_io.atomic_write_text(target, "seed")

    monkeypatch.setattr(atomic_io.os, "fdopen", flaky_fdopen)

    baseline = _fd_count()
    for _ in range(100):
        try:
            atomic_io.atomic_write_text(target, "payload")
        except OSError:
            pass
    after = _fd_count()

    assert fail_count["n"] >= 100, "monkeypatch did not engage"
    assert after - baseline <= 1, (
        f"fd count grew from {baseline} to {after} over 100 iterations "
        f"with fdopen-failure injection — fd leak in atomic_io"
    )

    # Restore is automatic via monkeypatch fixture.
    _ = real_fdopen  # silence unused-warning
