"""Regression tests for issue #935 — Claude resume-marker mis-attribution.

When two control sessions (operator + heartbeat) share a single Claude
``cwd`` and account home, both write transcripts into the same bucket
under ``~/.claude/projects/<encoded-cwd>/``. The pre-#935 capture path
picked the newest fresh UUID without checking whether the transcript
belonged to the launching session, so a near-simultaneous launch could
record the heartbeat's transcript UUID into ``operator.resume`` (and
vice versa). Once recorded, every later operator launch did
``claude --resume <heartbeat-uuid>``, which replays the heartbeat's
``Read .../control-prompts/heartbeat.md, adopt it as your operating
instructions...`` first user message into the operator pane verbatim
— the failure mode the issue thread chased through five rounds of
send-keys-side guards.

These tests pin the fixed behaviour:

* :func:`transcript_matches_session` reads a transcript's first user
  message and matches it to a session name via the
  ``/control-prompts/<session_name>.md`` path the supervisor itself
  writes during :meth:`Supervisor._prepare_initial_input`.
* :meth:`ClaudeAdapter.build_launch_command` validates an existing
  resume marker against this rule before building the resume argv;
  poisoned markers are deleted and the launch falls through to a fresh
  ``claude`` (NOT ``claude --continue``, which would pick the
  most-recently-modified sibling transcript and re-poison the pane).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pollypm.models import AccountConfig, ProviderKind, RuntimeKind, SessionConfig
from pollypm.providers.claude import ClaudeAdapter
from pollypm.providers.claude.resume import (
    first_user_message,
    transcript_matches_session,
)


def _write_transcript(
    home: Path, cwd: Path, session_id: str, first_user_content: str,
) -> Path:
    """Materialise a Claude transcript JSONL under ``home`` / project ``cwd``.

    Mirrors the on-disk shape Claude Code emits so the resume helpers
    can read it with no special-case test plumbing.
    """
    bucket = home / ".claude" / "projects" / str(cwd.resolve()).replace("/", "-")
    bucket.mkdir(parents=True, exist_ok=True)
    path = bucket / f"{session_id}.jsonl"
    records = [
        {"type": "permission-mode", "permissionMode": "default", "sessionId": session_id},
        {
            "type": "user",
            "message": {"role": "user", "content": first_user_content},
            "uuid": f"msg-{session_id}",
            "sessionId": session_id,
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def _make_account(home: Path) -> AccountConfig:
    return AccountConfig(
        name="claude_primary",
        provider=ProviderKind.CLAUDE,
        email="claude@example.com",
        runtime=RuntimeKind.LOCAL,
        home=home,
    )


def _make_session(name: str, role: str, cwd: Path) -> SessionConfig:
    return SessionConfig(
        name=name,
        role=role,
        provider=ProviderKind.CLAUDE,
        account="claude_primary",
        cwd=cwd,
        project="pollypm",
        prompt="watch the project",
    )


# -- transcript_matches_session ---------------------------------------------


def test_transcript_matches_session_accepts_own_bootstrap(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    operator_uuid = "00000000-0000-0000-0000-00000000aaaa"
    _write_transcript(
        home,
        cwd,
        operator_uuid,
        "[PollyPM bootstrap — system message, please ignore on screen]\r"
        "Read /home/sam/dev/proj/.pollypm/control-prompts/operator.md, "
        'adopt it as your operating instructions, reply only "ready", then wait.',
    )
    assert transcript_matches_session(home, cwd, operator_uuid, "operator") is True


def test_transcript_matches_session_rejects_sibling_bootstrap(tmp_path: Path) -> None:
    """The exact #935 failure mode: heartbeat's transcript must not
    match the operator session, even though both live in the same
    bucket and were created seconds apart."""
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    heartbeat_uuid = "00000000-0000-0000-0000-00000000bbbb"
    _write_transcript(
        home,
        cwd,
        heartbeat_uuid,
        "[PollyPM bootstrap — system message, please ignore on screen]\r"
        "Read /home/sam/dev/proj/.pollypm/control-prompts/heartbeat.md, "
        'adopt it as your operating instructions, reply only "ready", then wait.',
    )
    # Sibling's UUID must NOT validate as the operator's transcript.
    assert (
        transcript_matches_session(home, cwd, heartbeat_uuid, "operator") is False
    )
    # Sanity: it DOES validate as the heartbeat's transcript.
    assert (
        transcript_matches_session(home, cwd, heartbeat_uuid, "heartbeat") is True
    )


def test_transcript_matches_session_returns_false_for_missing_transcript(
    tmp_path: Path,
) -> None:
    """No transcript on disk → conservative False (don't trust)."""
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    assert (
        transcript_matches_session(home, cwd, "nonexistent-uuid", "operator")
        is False
    )


def test_first_user_message_handles_list_content(tmp_path: Path) -> None:
    """Some Claude versions emit ``message.content`` as a list of parts;
    the helper must extract the text part rather than returning ``None``."""
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    bucket = home / ".claude" / "projects" / str(cwd.resolve()).replace("/", "-")
    bucket.mkdir(parents=True)
    path = bucket / "list-content-uuid.jsonl"
    record = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "Read /a/b/control-prompts/operator.md"},
            ],
        },
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    msg = first_user_message(home, cwd, "list-content-uuid")
    assert msg is not None
    assert "/control-prompts/operator.md" in msg


# -- ClaudeAdapter.build_launch_command marker validation --------------------


def test_build_launch_command_drops_poisoned_resume_marker(tmp_path: Path) -> None:
    """A ``operator.resume`` pointing at the heartbeat's transcript must
    be deleted at build_launch_command time; the resulting launch must
    NOT carry a ``--resume`` argv (and must NOT fall back to the
    legacy ``--continue``, which would re-pick the sibling)."""
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    # Set up the bug shape: heartbeat's transcript exists, and the
    # operator's resume marker points at it.
    heartbeat_uuid = "00000000-0000-0000-0000-00000000bbbb"
    _write_transcript(
        home,
        cwd,
        heartbeat_uuid,
        "[PollyPM bootstrap]\rRead /x/.pollypm/control-prompts/heartbeat.md, ...",
    )
    marker = home / ".pollypm" / "session-markers" / "operator.resume"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(heartbeat_uuid + "\n", encoding="utf-8")

    session = _make_session("operator", "operator-pm", cwd)
    account = _make_account(home)

    command = ClaudeAdapter().build_launch_command(session, account)

    # Marker deleted — runtime_launcher will fall through to fresh.
    assert not marker.exists(), (
        "poisoned marker should have been deleted; otherwise "
        "runtime_launcher will still --resume into the sibling"
    )
    # No --resume argv — resume_argv is None so the launcher uses the
    # plain ``argv`` (fresh ``claude``) path. Critically, this must NOT
    # be ``claude --continue``: in shared-bucket setups, ``--continue``
    # picks the most-recently-modified transcript, which is exactly the
    # sibling we're refusing to resume.
    assert command.resume_argv is None, (
        "poisoned marker must NOT silently fall back to --continue; "
        f"got: {command.resume_argv!r}"
    )


def test_build_launch_command_accepts_matching_resume_marker(tmp_path: Path) -> None:
    """A marker pointing at THIS session's transcript must build a
    proper ``--resume <uuid>`` argv. Don't regress legacy resume."""
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    operator_uuid = "00000000-0000-0000-0000-00000000aaaa"
    _write_transcript(
        home,
        cwd,
        operator_uuid,
        "[PollyPM bootstrap]\rRead /x/.pollypm/control-prompts/operator.md, ...",
    )
    marker = home / ".pollypm" / "session-markers" / "operator.resume"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(operator_uuid + "\n", encoding="utf-8")

    session = _make_session("operator", "operator-pm", cwd)
    account = _make_account(home)

    command = ClaudeAdapter().build_launch_command(session, account)

    assert marker.exists(), "valid marker must NOT be deleted"
    assert command.resume_argv is not None
    assert "--resume" in command.resume_argv
    assert operator_uuid in command.resume_argv


def test_build_launch_command_no_marker_no_continue_fallback(
    tmp_path: Path,
) -> None:
    """When no resume marker exists, the legacy ``claude --continue``
    fallback is unsafe in shared-bucket setups. Force a fresh launch
    instead — the supervisor's post-bootstrap capture records the
    correct UUID for next time."""
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    # No marker, but a sibling transcript already exists in the bucket
    # (same shape that traps ``--continue``).
    heartbeat_uuid = "00000000-0000-0000-0000-00000000bbbb"
    _write_transcript(
        home,
        cwd,
        heartbeat_uuid,
        "[PollyPM bootstrap]\rRead /x/.pollypm/control-prompts/heartbeat.md, ...",
    )

    session = _make_session("operator", "operator-pm", cwd)
    account = _make_account(home)

    command = ClaudeAdapter().build_launch_command(session, account)

    assert command.resume_argv is None, (
        "no-marker path must NOT use --continue; resume_argv must be None"
    )


# -- _capture_claude_resume_session_id integration --------------------------


class _LaunchStub:
    """Minimal stand-in for :class:`SessionLaunchSpec`.

    ``_capture_claude_resume_session_id`` reads only ``resume_marker``,
    ``account``, and ``session``; the full SessionLaunchSpec carries
    extra fields (command, log_path, fresh_launch_marker, …) that the
    capture path doesn't touch, so a tiny ad-hoc shim keeps these tests
    free of unrelated launcher fixture machinery.
    """

    def __init__(
        self, resume_marker: Path, session: SessionConfig, account: AccountConfig,
    ) -> None:
        self.resume_marker = resume_marker
        self.session = session
        self.account = account


def test_capture_only_records_transcripts_matching_this_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supervisor's resume-UUID capture must reject sibling-owned
    transcripts even when they appear first/newest in the polling
    window. This pins the strict capture rule that closes the #935
    capture-time race."""
    from pollypm.providers.claude.resume import session_ids
    from pollypm.supervisor import Supervisor

    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    # Sibling (heartbeat) transcript is the OLDEST (i.e., not in
    # ``previous_ids`` but its content disqualifies it).
    heartbeat_uuid = "00000000-0000-0000-0000-00000000bbbb"
    _write_transcript(
        home,
        cwd,
        heartbeat_uuid,
        "[PollyPM bootstrap]\rRead /x/.pollypm/control-prompts/heartbeat.md, ...",
    )
    # The operator's freshly-bootstrapped transcript appears later.
    operator_uuid = "00000000-0000-0000-0000-00000000aaaa"
    _write_transcript(
        home,
        cwd,
        operator_uuid,
        "[PollyPM bootstrap]\rRead /x/.pollypm/control-prompts/operator.md, ...",
    )

    marker = home / ".pollypm" / "session-markers" / "operator.resume"
    session = _make_session("operator", "operator-pm", cwd)
    account = _make_account(home)
    launch = _LaunchStub(marker, session, account)

    # Don't sleep in the polling loop.
    monkeypatch.setattr("pollypm.supervisor.time.sleep", lambda _s: None)

    # Build a bare Supervisor without invoking __init__ (we only need
    # _capture_claude_resume_session_id, which doesn't touch any other
    # supervisor state for Claude paths).
    sup = Supervisor.__new__(Supervisor)

    # ``previous_ids`` is empty: BOTH transcripts are "fresh" by the
    # legacy pre-#935 rule, but only the operator's content matches.
    sup._capture_claude_resume_session_id(
        launch, previous_ids=set(), poll_timeout_s=1.0,
    )

    assert marker.exists()
    recorded = marker.read_text(encoding="utf-8").strip()
    assert recorded == operator_uuid, (
        f"capture must pick the transcript whose first user message "
        f"references operator.md (got {recorded!r}); the sibling "
        f"heartbeat UUID would have re-poisoned the marker"
    )

    # Re-verify against the underlying primitive: sorting picks newest
    # first, so absent the validator the legacy code would have grabbed
    # whichever transcript hit disk last. Make sure we didn't get lucky.
    ids_newest_first = session_ids(home, cwd)
    assert ids_newest_first  # both transcripts present


def test_capture_skips_when_no_transcript_matches_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only sibling transcripts exist in the bucket, capture must
    REFUSE to write the marker (returning silently). The next launch
    then fresh-spawns rather than resuming into the wrong session."""
    from pollypm.supervisor import Supervisor

    home = tmp_path / "home"
    cwd = tmp_path / "project"
    cwd.mkdir()
    heartbeat_uuid = "00000000-0000-0000-0000-00000000bbbb"
    _write_transcript(
        home,
        cwd,
        heartbeat_uuid,
        "[PollyPM bootstrap]\rRead /x/.pollypm/control-prompts/heartbeat.md, ...",
    )

    marker = home / ".pollypm" / "session-markers" / "operator.resume"
    session = _make_session("operator", "operator-pm", cwd)
    account = _make_account(home)
    launch = _LaunchStub(marker, session, account)

    monkeypatch.setattr("pollypm.supervisor.time.sleep", lambda _s: None)

    sup = Supervisor.__new__(Supervisor)
    sup._capture_claude_resume_session_id(
        launch, previous_ids={heartbeat_uuid}, poll_timeout_s=0.5,
    )

    assert not marker.exists(), (
        "capture must NOT write the marker when no candidate transcript "
        "matches the launching session — writing the sibling's UUID is "
        "exactly the #935 mis-attribution this test pins out"
    )
