"""Resume-token helpers for the Claude provider.

Two responsibilities:

1. **Latest session discovery** — given an account ``home`` and a project
   ``cwd``, find the UUID of the most recent Claude Code session for
   that working directory. Claude Code stores session transcripts at
   ``~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`` where
   ``encoded-cwd`` is the absolute cwd with ``/`` replaced by ``-``.

2. **Resume argv construction** — given a session UUID, build the
   ``claude`` argv that resumes it interactively under the same
   ``--dangerously-skip-permissions`` posture PollyPM uses for fresh
   architect launches.

Designed for the architect 2-hour idle-close flow: the supervisor
captures the latest UUID, kills the tmux window, persists the UUID
in ``architect_resume_tokens``, and uses :func:`resume_argv` to
relaunch when the project becomes active again.
"""

from __future__ import annotations

import json
from pathlib import Path


def _encoded_cwd(cwd: Path) -> str:
    """Return the directory name Claude Code uses to bucket sessions for ``cwd``.

    Claude Code encodes absolute paths by replacing ``/`` with ``-`` —
    e.g. ``/private/tmp/passgen`` → ``-private-tmp-passgen``. We resolve
    the path first so symlink games can't desync the bucket name.
    """
    resolved = str(cwd.resolve())
    return resolved.replace("/", "-")


def latest_session_id(home: Path, cwd: Path) -> str | None:
    """Return the newest Claude session UUID under ``home`` for project ``cwd``.

    ``home`` is the account home dir (the ``CLAUDE_CONFIG_DIR``
    parent). Returns ``None`` when:

    - the encoded-cwd bucket directory doesn't exist (no sessions yet)
    - the bucket exists but contains no ``*.jsonl`` files

    The newest file by mtime wins; its stem (filename without
    ``.jsonl``) is the session UUID Claude Code accepts via
    ``--resume``.
    """
    ids = session_ids(home, cwd)
    if not ids:
        return None
    return ids[0]


def session_ids(home: Path, cwd: Path) -> list[str]:
    """Return Claude transcript UUIDs for ``cwd`` newest-first.

    The UUIDs are the ``.stem`` values of transcript ``*.jsonl`` files
    under Claude's encoded-cwd bucket. Newest ``mtime`` wins so callers
    can compare a pre-launch snapshot against the post-launch bucket and
    detect the fresh transcript a new tmux window created.
    """
    bucket = home / ".claude" / "projects" / _encoded_cwd(cwd)
    if not bucket.is_dir():
        return []
    candidates = [p for p in bucket.iterdir() if p.suffix == ".jsonl"]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [candidate.stem for candidate in candidates]


def recorded_session_id(marker: Path) -> str | None:
    """Read a previously-recorded Claude session UUID from ``marker``.

    The marker is PollyPM-owned state under ``.pollypm/session-markers``.
    When it contains a non-empty line, that line is the exact Claude
    transcript UUID we should pass to ``claude --resume``.
    """
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


def transcript_path(home: Path, cwd: Path, session_id: str) -> Path:
    """Return the on-disk transcript path for ``session_id`` under ``home``."""
    return home / ".claude" / "projects" / _encoded_cwd(cwd) / f"{session_id}.jsonl"


def first_user_message(home: Path, cwd: Path, session_id: str) -> str | None:
    """Return the first user message content of ``session_id``'s transcript.

    Reads the transcript JSONL, scans top-down for the first record with
    ``type == "user"`` and a string ``message.content``, and returns that
    string. Returns ``None`` when the transcript is absent, malformed, or
    contains no user message.

    Used by :func:`transcript_matches_session` to verify a candidate
    resume UUID actually belongs to the session that's about to claim it
    — Claude control sessions sharing a single ``cwd``/auth-home all
    write to the same transcript bucket, so the only durable signal that
    transcript ``X`` belongs to session ``S`` is the ``Read
    .../control-prompts/<S>.md`` bootstrap landed by PollyPM as the
    first user message.
    """
    path = transcript_path(home, cwd, session_id)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") != "user":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    return content
                # Some Claude versions emit content as a list of parts.
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            text = part.get("text")
                            if isinstance(text, str):
                                return text
                        elif isinstance(part, str):
                            return part
                return None
    except OSError:
        return None
    return None


def transcript_matches_session(
    home: Path, cwd: Path, session_id: str, session_name: str,
) -> bool:
    """Return True iff ``session_id``'s transcript was bootstrapped for ``session_name``.

    PollyPM's first user message in a fresh control session is always
    ``Read /…/.pollypm/control-prompts/<session_name>.md, adopt it as
    your operating instructions, …``. We treat the presence of
    ``/control-prompts/<session_name>.md`` in the first user message as
    proof the transcript belongs to ``session_name`` — and the presence
    of ``/control-prompts/<other>.md`` (for any other configured
    session) as proof it belongs to a sibling.

    Conservative: if the transcript can't be read or has no user
    message, returns ``False`` (we can't prove ownership). Callers
    treat that as "don't trust this UUID for resume".
    """
    if not session_name:
        return False
    first = first_user_message(home, cwd, session_id)
    if first is None:
        return False
    needle = f"/control-prompts/{session_name}.md"
    return needle in first


def resume_argv(session_id: str, extra_args: list[str] | None = None) -> list[str]:
    """Return ``claude`` argv for resuming ``session_id`` interactively.

    Mirrors the fresh-launch posture PollyPM uses for architects: the
    permission prompts are pre-bypassed via
    ``--dangerously-skip-permissions`` (the architect runs in an
    isolated worktree and is sandboxed at the worktree level).

    ``extra_args`` is appended verbatim after the resume marker so
    callers can pin the agent profile, effort level, etc.
    """
    extra = list(extra_args) if extra_args else []
    return [
        "claude",
        "--dangerously-skip-permissions",
        "--resume",
        session_id,
        *extra,
    ]

__all__ = [
    "first_user_message",
    "latest_session_id",
    "recorded_session_id",
    "resume_argv",
    "session_ids",
    "transcript_matches_session",
    "transcript_path",
]
