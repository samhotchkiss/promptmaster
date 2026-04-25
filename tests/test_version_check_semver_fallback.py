"""Cycle 93: ``_fetch_latest_version`` git-ls-remote fallback semver sort.

The heartbeat-driven version check ``_fetch_latest_version`` falls
back to ``git ls-remote`` when ``gh`` is unavailable. Before cycle 93
that fallback ran ``sorted(tags)[-1]`` — at the v1.10 line that
picked ``1.9.0`` over ``1.10.0``, and a stray non-semver tag like
``nightly`` would sort to the top. Same shape as cycle 92's
``pm upgrade`` fix; this file pins the heartbeat surface separately.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from pollypm.version_check import _fetch_latest_version


def _fake_proc(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )


def _ls_remote_stdout(versions: list[str]) -> str:
    """Render a ``git ls-remote --tags`` style stdout for ``versions``."""
    return "\n".join(
        f"abc123\trefs/tags/v{v}" for v in versions
    )


def test_fetch_latest_picks_semver_max_when_gh_unavailable() -> None:
    """When ``gh`` returns non-zero and the git fallback fires, the
    picked latest must be the actual semver-newest tag (not the
    lexicographic-last one).
    """
    def fake_run(cmd, *_a, **_kw):
        if cmd[:1] == ["gh"]:
            # gh fails (e.g. unauthenticated, no network) — force the
            # fallback path.
            return _fake_proc("", returncode=1)
        if cmd[:2] == ["git", "ls-remote"]:
            return _fake_proc(
                _ls_remote_stdout(["1.0.0", "1.1.0", "1.9.0", "1.10.0"]),
            )
        raise AssertionError(f"unexpected subprocess: {cmd!r}")

    with patch("subprocess.run", side_effect=fake_run):
        latest = _fetch_latest_version()
    # Lexicographic sort would have returned ``1.9.0``.
    assert latest == "1.10.0"


def test_fetch_latest_demotes_unparseable_tags() -> None:
    """A stray non-PEP-440 tag (``nightly``) must not masquerade as
    latest — the semver sort key demotes it below all parseable tags."""
    def fake_run(cmd, *_a, **_kw):
        if cmd[:1] == ["gh"]:
            return _fake_proc("", returncode=1)
        if cmd[:2] == ["git", "ls-remote"]:
            return _fake_proc(
                "abc123\trefs/tags/nightly\n"
                + _ls_remote_stdout(["1.0.0", "1.1.0"]),
            )
        raise AssertionError(f"unexpected subprocess: {cmd!r}")

    with patch("subprocess.run", side_effect=fake_run):
        latest = _fetch_latest_version()
    assert latest == "1.1.0"


def test_too_soon_to_check_handles_corrupt_non_dict_marker(tmp_path) -> None:
    """Cycle 94: a marker file that parses to a non-dict (list, null, str)
    must not raise — it should be treated as "no marker yet" so the
    next version check still fires.
    """
    from pollypm.version_check import _too_soon_to_check

    marker = tmp_path / "version_check.json"
    marker.write_text('"this is a string, not a dict"')
    assert _too_soon_to_check(tmp_path) is False

    marker.write_text("[1, 2, 3]")
    assert _too_soon_to_check(tmp_path) is False

    marker.write_text("null")
    assert _too_soon_to_check(tmp_path) is False


def test_already_notified_handles_corrupt_non_dict_marker(tmp_path) -> None:
    """Symmetric defense for ``_already_notified``."""
    from pollypm.version_check import _already_notified

    marker = tmp_path / "version_check.json"
    marker.write_text("[1, 2, 3]")
    assert _already_notified(tmp_path, "1.0.0") is False

    marker.write_text('"oops"')
    assert _already_notified(tmp_path, "1.0.0") is False


def test_record_check_handles_corrupt_non_dict_marker(tmp_path) -> None:
    """``_record_check`` must overwrite a corrupted marker rather than
    raising ``TypeError`` on ``existing["checked_at"] = ...``."""
    import json

    from pollypm.version_check import _record_check

    marker = tmp_path / "version_check.json"
    marker.write_text("[1, 2, 3]")
    _record_check(tmp_path)
    # The file is now a fresh dict carrying just the checked_at field.
    parsed = json.loads(marker.read_text())
    assert isinstance(parsed, dict)
    assert "checked_at" in parsed
