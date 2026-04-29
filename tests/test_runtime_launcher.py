import base64
import json
from pathlib import Path

import pytest

from pollypm import runtime_launcher


def _payload(value: dict[str, object]) -> str:
    raw = json.dumps(value).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_decode_payload_rejects_non_object_json() -> None:
    encoded = _payload(["not", "a", "mapping"])

    with pytest.raises(SystemExit, match="invalid launcher payload"):
        runtime_launcher._decode_payload(encoded)


def test_main_prefers_resume_command_when_resume_marker_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resume_marker = tmp_path / "markers" / "worker.resume"
    resume_marker.parent.mkdir(parents=True)
    resume_marker.touch()
    codex_home = tmp_path / "codex-home"
    # Use absolute-path argv values so the launcher's #965 binary
    # resolver short-circuits without consulting the test machine's
    # PATH (where "resume-bin"/"fresh-bin" do not exist).
    resume_bin = tmp_path / "resume-bin"
    fresh_bin = tmp_path / "fresh-bin"
    payload = _payload(
        {
            "cwd": str(tmp_path),
            "env": {"EXAMPLE": "1"},
            "argv": [str(fresh_bin), "--fresh"],
            "resume_argv": [str(resume_bin), "--resume"],
            "resume_marker": str(resume_marker),
            "fresh_launch_marker": str(tmp_path / "markers" / "worker.fresh"),
            "codex_home": str(codex_home),
        }
    )
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake_execvpe(program: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append((program, argv, env))
        raise SystemExit(0)

    monkeypatch.setattr(runtime_launcher.os, "execvpe", fake_execvpe)

    with pytest.raises(SystemExit, match="0"):
        runtime_launcher.main(["runtime_launcher", payload])

    assert len(calls) == 1
    assert calls[0][0] == str(resume_bin)
    assert calls[0][1] == [str(resume_bin), "--resume"]
    assert calls[0][2]["EXAMPLE"] == "1"
    assert not (tmp_path / "markers" / "worker.fresh").exists()


def test_main_resolves_relative_argv_via_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#965 — when ``argv[0]`` is bare (e.g. ``codex``), the launcher
    resolves it against ``exec_env['PATH']`` so ``os.execvpe`` receives
    an absolute path and never hits the bare ``FileNotFoundError:
    /bin/codex`` traceback when tmux strips ``~/.npm-global/bin``.
    """
    fake_bin_dir = tmp_path / "fake-bin"
    fake_bin_dir.mkdir()
    fake_codex = fake_bin_dir / "codex"
    fake_codex.write_text("#!/bin/sh\nexec true\n")
    fake_codex.chmod(0o755)
    payload = _payload(
        {
            "cwd": str(tmp_path),
            "env": {"PATH": str(fake_bin_dir)},
            "argv": ["codex", "--whatever"],
        }
    )
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake_execvpe(program: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append((program, argv, env))
        raise SystemExit(0)

    monkeypatch.setattr(runtime_launcher.os, "execvpe", fake_execvpe)

    with pytest.raises(SystemExit, match="0"):
        runtime_launcher.main(["runtime_launcher", payload])

    assert len(calls) == 1
    program, argv, env = calls[0]
    assert program == str(fake_codex), f"expected absolute path, got {program!r}"
    assert argv == [str(fake_codex), "--whatever"]
    assert env["PATH"] == str(fake_bin_dir)


def test_main_surfaces_clear_error_when_binary_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#965 — when the agent binary is genuinely missing, the launcher
    raises ``SystemExit`` with a human-readable message naming the
    missing binary and the PATH searched, instead of bubbling the bare
    ``FileNotFoundError`` from ``execvpe``.
    """
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    payload = _payload(
        {
            "cwd": str(tmp_path),
            "env": {"PATH": str(empty_dir)},
            "argv": ["codex-not-installed", "--flag"],
        }
    )
    # Force exec_env's PATH to the empty dir by stripping the test
    # process' own PATH; otherwise os.environ.copy() in main() may
    # supply a directory that contains a real binary called
    # ``codex-not-installed``.
    monkeypatch.setenv("PATH", str(empty_dir))

    def fake_execvpe(*_args, **_kwargs) -> None:
        raise AssertionError("execvpe should not be reached when binary is missing")

    monkeypatch.setattr(runtime_launcher.os, "execvpe", fake_execvpe)

    with pytest.raises(SystemExit) as excinfo:
        runtime_launcher.main(["runtime_launcher", payload])

    msg = str(excinfo.value)
    assert "codex-not-installed" in msg
    assert "PATH searched" in msg
