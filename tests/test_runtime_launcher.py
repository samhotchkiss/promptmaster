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
    payload = _payload(
        {
            "cwd": str(tmp_path),
            "env": {"EXAMPLE": "1"},
            "argv": ["fresh-bin", "--fresh"],
            "resume_argv": ["resume-bin", "--resume"],
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
    assert calls[0][0] == "resume-bin"
    assert calls[0][1] == ["resume-bin", "--resume"]
    assert calls[0][2]["EXAMPLE"] == "1"
    assert not (tmp_path / "markers" / "worker.fresh").exists()
