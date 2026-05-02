"""Tests for ``pm errors`` triage summary (#1040).

Three layers covered:

1. ``error_log_summary`` parsing and aggregation as plain functions
   (no Typer, no filesystem mocking), so the rollup math is anchored.
2. The CLI command's three modes — default summary, ``--raw`` (and
   the implicit raw triggers ``--tail/--follow/--grep``), and
   ``--fingerprint`` — against a real on-disk log fixture so the
   wire-up (option parsing, file reads, exit codes) is covered.
3. ``--since`` parsing and "no log yet" behavior so empty-state and
   bad-input cases stay intentional.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from pollypm import error_log_summary as els
from pollypm.cli_features.maintenance import register_maintenance_commands


def _build_app() -> typer.Typer:
    app = typer.Typer()
    register_maintenance_commands(app)
    return app


def _line(
    ts: str,
    level: str,
    name: str,
    message: str,
    *,
    tag: str = "cli/123",
) -> str:
    return f"{ts} {level} {tag} {name}: {message}"


# ---------------------------------------------------------------------------
# Layer 1: pure parsing / aggregation
# ---------------------------------------------------------------------------


def test_iter_records_parses_header_and_folds_traceback() -> None:
    block = (
        _line("2026-04-30 10:00:00,000", "ERROR", "pollypm.supervisor", "boom")
        + "\nTraceback (most recent call last):"
        + '\n  File "x.py", line 1, in <module>'
        + "\nValueError: boom"
        + "\n"
        + _line("2026-04-30 10:00:01,000", "WARNING", "pollypm.heartbeat", "tick")
    )
    records = list(els.iter_records(block.splitlines(keepends=True)))
    assert len(records) == 2
    err = records[0]
    assert err.level == "ERROR"
    assert err.name == "pollypm.supervisor"
    assert err.message == "boom"
    assert "Traceback" in err.raw
    assert "ValueError: boom" in err.raw
    assert records[1].level == "WARNING"
    # Continuation lines must NOT bleed into the next record's raw block.
    assert "Traceback" not in records[1].raw


def test_iter_records_skips_orphan_continuation_before_first_header() -> None:
    block = (
        "  File \"x.py\", line 1, in <module>"
        "\nthis should be ignored"
        "\n"
        + _line("2026-04-30 10:00:00,000", "ERROR", "pollypm.supervisor", "boom")
    )
    records = list(els.iter_records(block.splitlines(keepends=True)))
    assert len(records) == 1
    assert records[0].name == "pollypm.supervisor"


def test_fingerprint_matches_alert_signature() -> None:
    record = next(els.iter_records([
        _line("2026-04-30 10:00:00,000", "ERROR", "pollypm.heartbeat", "tick failed"),
    ]))
    expected = hashlib.sha1(
        b"pollypm.heartbeat\ntick failed"
    ).hexdigest()[:12]
    assert record.fingerprint == expected


def test_summarize_groups_by_source_and_recency() -> None:
    now = datetime(2026, 4, 30, 12, 0, 0)
    raw = [
        _line("2026-04-30 11:50:00,000", "ERROR", "pollypm.supervisor", "boom A"),
        _line("2026-04-30 11:55:00,000", "ERROR", "pollypm.supervisor", "boom B"),
        _line("2026-04-30 11:58:00,000", "ERROR", "pollypm.heartbeat", "tick"),
        _line("2026-04-30 11:30:00,000", "WARNING", "pollypm.misc", "ignored"),
        _line("2026-04-29 09:00:00,000", "ERROR", "pollypm.too_old", "stale"),
    ]
    records = list(els.iter_records(raw))
    summary = els.summarize(
        records, now=now, since=timedelta(hours=24)
    )
    sources = {row.source: row for row in summary.sources}
    assert sources["pollypm.supervisor"].count == 2
    assert sources["pollypm.heartbeat"].count == 1
    # WARNINGs aren't included in the default rollup.
    assert "pollypm.misc" not in sources
    # The "stale" record is just under 27 hours old — outside the 24h window.
    assert "pollypm.too_old" not in sources
    # Last-seen tracks the most recent timestamp for that source.
    assert sources["pollypm.supervisor"].last_seen == datetime(2026, 4, 30, 11, 55)


def test_summarize_promotes_recurring_fingerprints() -> None:
    now = datetime(2026, 4, 30, 12, 0, 0)
    # Same name+message appears 5x in the last hour -> qualifies. A
    # different rare error appears once -> drops below the threshold.
    raw = [
        _line(f"2026-04-30 11:{m:02d}:00,000", "ERROR", "pollypm.heartbeat",
              "HeartbeatRail tick failed; continuing")
        for m in (10, 20, 30, 40, 50)
    ] + [
        _line("2026-04-30 11:45:00,000", "ERROR", "pollypm.unique", "rare boom"),
    ]
    records = list(els.iter_records(raw))
    summary = els.summarize(records, now=now)
    assert len(summary.fingerprints) == 1
    fp = summary.fingerprints[0]
    assert fp.count == 5
    assert fp.source == "pollypm.heartbeat"
    assert fp.title.startswith("HeartbeatRail tick failed")


def test_summarize_respects_fingerprint_min_count_override() -> None:
    now = datetime(2026, 4, 30, 12, 0, 0)
    raw = [
        _line("2026-04-30 11:30:00,000", "ERROR", "pollypm.x", "twice"),
        _line("2026-04-30 11:45:00,000", "ERROR", "pollypm.x", "twice"),
    ]
    records = list(els.iter_records(raw))
    # Default threshold is 3 -> filtered out.
    assert els.summarize(records, now=now).fingerprints == []
    # With ``fingerprint_min_count=2`` it shows up.
    relaxed = els.summarize(records, now=now, fingerprint_min_count=2)
    assert len(relaxed.fingerprints) == 1


def test_find_fingerprint_records_returns_all_matches() -> None:
    raw = [
        _line("2026-04-30 11:00:00,000", "ERROR", "pollypm.x", "shared message"),
        _line("2026-04-30 11:05:00,000", "ERROR", "pollypm.x", "shared message"),
        _line("2026-04-30 11:10:00,000", "ERROR", "pollypm.x", "different"),
    ]
    records = list(els.iter_records(raw))
    target_fp = records[0].fingerprint
    matches = els.find_fingerprint_records(records, target_fp)
    assert len(matches) == 2


def test_parse_duration_accepts_h_d_w_m_s() -> None:
    assert els.parse_duration("1h") == timedelta(hours=1)
    assert els.parse_duration("24h") == timedelta(hours=24)
    assert els.parse_duration("7d") == timedelta(days=7)
    assert els.parse_duration("30m") == timedelta(minutes=30)
    assert els.parse_duration("2w") == timedelta(weeks=2)
    assert els.parse_duration("45s") == timedelta(seconds=45)


def test_parse_duration_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        els.parse_duration("")
    with pytest.raises(ValueError):
        els.parse_duration("forever")
    with pytest.raises(ValueError):
        # No unit -> reject. "1" would be ambiguous.
        els.parse_duration("1")


def test_format_age_picks_unit_by_magnitude() -> None:
    base = datetime(2026, 4, 30, 12, 0, 0)
    assert els.format_age(base, base - timedelta(seconds=12)) == "12s ago"
    assert els.format_age(base, base - timedelta(minutes=4)) == "4m ago"
    assert els.format_age(base, base - timedelta(hours=3)) == "3h ago"
    assert els.format_age(base, base - timedelta(days=5)) == "5d ago"


# ---------------------------------------------------------------------------
# Layer 2: CLI wiring
# ---------------------------------------------------------------------------


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_pm_errors_default_renders_summary(tmp_path: Path) -> None:
    log = tmp_path / "errors.log"
    # 4x supervisor (one fingerprint repeats >=3) + 1 heartbeat in the last hour.
    base = datetime.now() - timedelta(minutes=10)
    lines = []
    for i in range(4):
        ts = (base + timedelta(seconds=i * 30)).strftime("%Y-%m-%d %H:%M:%S,000")
        lines.append(_line(ts, "ERROR", "pollypm.supervisor", "boom"))
    ts = base.strftime("%Y-%m-%d %H:%M:%S,000")
    lines.append(_line(ts, "ERROR", "pollypm.heartbeat", "tick"))
    _write_log(log, lines)

    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: log):
        out = runner.invoke(app, ["errors"])
    assert out.exit_code == 0, out.output
    assert "ERROR sources" in out.output
    assert "pollypm.supervisor" in out.output
    assert "Active fingerprints" in out.output
    assert "pm errors --raw" in out.output
    assert "pm errors --fingerprint" in out.output


def test_pm_errors_no_log_returns_quiet_message(tmp_path: Path) -> None:
    missing = tmp_path / "nope.log"
    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: missing):
        out = runner.invoke(app, ["errors"])
    assert out.exit_code == 0, out.output
    assert "All quiet" in out.output


def test_pm_errors_raw_mode_dumps_file(tmp_path: Path, capfd) -> None:
    log = tmp_path / "errors.log"
    _write_log(
        log,
        [
            _line("2026-04-30 10:00:00,000", "ERROR", "pollypm.x", "alpha"),
            _line("2026-04-30 10:00:01,000", "ERROR", "pollypm.x", "beta"),
        ],
    )
    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: log):
        out = runner.invoke(app, ["errors", "--raw", "--tail", "0"])
    assert out.exit_code == 0, out.output
    captured = capfd.readouterr()
    # Raw mode shells out to ``cat``/``tail`` so stdout bypasses
    # Typer's CliRunner buffer; read it via ``capfd``.
    combined = out.output + captured.out
    assert "alpha" in combined
    assert "beta" in combined


def test_pm_errors_grep_implies_raw(tmp_path: Path, capfd) -> None:
    log = tmp_path / "errors.log"
    _write_log(
        log,
        [
            _line("2026-04-30 10:00:00,000", "ERROR", "pollypm.x", "needle here"),
            _line("2026-04-30 10:00:01,000", "ERROR", "pollypm.x", "haystack"),
        ],
    )
    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: log):
        out = runner.invoke(app, ["errors", "--grep", "needle", "--tail", "0"])
    assert out.exit_code == 0, out.output
    captured = capfd.readouterr()
    combined = out.output + captured.out
    assert "needle" in combined
    assert "haystack" not in combined


def test_pm_errors_since_invalid_returns_exit_2(tmp_path: Path) -> None:
    log = tmp_path / "errors.log"
    _write_log(log, [
        _line("2026-04-30 10:00:00,000", "ERROR", "pollypm.x", "boom"),
    ])
    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: log):
        out = runner.invoke(app, ["errors", "--since", "forever"])
    assert out.exit_code == 2, out.output


def test_pm_errors_since_widens_window(tmp_path: Path) -> None:
    log = tmp_path / "errors.log"
    # One record exactly 3 days old.
    old_ts = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S,000")
    _write_log(
        log,
        [_line(old_ts, "ERROR", "pollypm.olderror", "ancient")],
    )
    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: log):
        # 24h window -> shouldn't surface.
        out_default = runner.invoke(app, ["errors"])
        # 7d window -> should surface.
        out_wide = runner.invoke(app, ["errors", "--since", "7d"])
    assert "pollypm.olderror" not in out_default.output
    assert "pollypm.olderror" in out_wide.output


def test_pm_errors_fingerprint_expands_to_traceback(tmp_path: Path) -> None:
    log = tmp_path / "errors.log"
    target_message = "tick failed; continuing"
    target_name = "pollypm.heartbeat"
    fp = hashlib.sha1(
        f"{target_name}\n{target_message}".encode("utf-8")
    ).hexdigest()[:12]
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        _line("2026-04-30 10:00:00,000", "ERROR", target_name, target_message)
        + "\nTraceback (most recent call last):"
        + '\n  File "heartbeat.py", line 42, in tick'
        + "\nValueError: tick failed"
        + "\n"
        + _line("2026-04-30 10:01:00,000", "ERROR", "pollypm.x", "different"),
        encoding="utf-8",
    )
    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: log):
        out = runner.invoke(app, ["errors", "--fingerprint", fp])
    assert out.exit_code == 0, out.output
    assert fp in out.output
    assert "Traceback" in out.output
    assert "ValueError" in out.output


def test_pm_errors_fingerprint_unknown_returns_exit_1(tmp_path: Path) -> None:
    log = tmp_path / "errors.log"
    _write_log(log, [
        _line("2026-04-30 10:00:00,000", "ERROR", "pollypm.x", "boom"),
    ])
    app = _build_app()
    runner = CliRunner()
    with patch("pollypm.error_log.path", lambda: log):
        out = runner.invoke(app, ["errors", "--fingerprint", "deadbeef0000"])
    assert out.exit_code == 1, out.output
    assert "No records match" in out.output
