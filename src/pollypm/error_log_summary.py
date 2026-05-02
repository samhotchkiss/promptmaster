"""Summarize ``~/.pollypm/errors.log`` for ``pm errors``'s default view.

The raw log file is a chronological stream of WARNING+ records from
every PollyPM process, often with multi-line tracebacks attached.
Tailing it directly lands users mid-stacktrace; piping to ``awk`` to
get a source-module rollup is a workaround they have to invent.

This module owns the file-side aggregation:

- Parse log lines into structured records (timestamp, level, source
  module, message) and skip continuation lines from tracebacks.
- Roll up by ``pollypm.<module>`` source for a "where errors come
  from" view, with the most recent occurrence per source.
- Deduplicate by the same fingerprint the alert pipeline uses
  (``sha1(name + "\\n" + normalized_message)[:12]``) so the hashes
  printed here match ``pm alerts``'s ``error_log/critical_error:<hash>``
  surface and a user can copy-paste either way.
- Resolve ``--since`` durations (``1h``, ``24h``, ``7d``).
- Expand one fingerprint to its full traceback for ``--fingerprint``.

The CLI command in ``cli_features/maintenance.py`` formats these
results — keeping the parsing here means we can unit-test it without
spinning up Typer or shelling out to ``tail``/``grep``.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

# Matches the ``_FORMAT`` defined in ``pollypm.error_log``:
#   ``%(asctime)s %(levelname)s %(process_tag)s %(name)s: %(message)s``
# ``asctime`` defaults to ``YYYY-MM-DD HH:MM:SS,mmm``. Process tag is
# ``<label>/<pid>`` (no spaces). Logger ``name`` is dotted.
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<tag>\S+) "
    r"(?P<name>[^:]+): "
    r"(?P<message>.*)$"
)

_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S,%f"

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_DURATION_UNITS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}

_MAX_MESSAGE_LENGTH = 280


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One parsed entry from ``~/.pollypm/errors.log``.

    ``raw`` is the full block — the header line plus any continuation
    (traceback) lines that followed it. ``--fingerprint`` reuses
    ``raw`` to print the original traceback verbatim.
    """

    timestamp: datetime
    level: str
    process_tag: str
    name: str  # logger name (e.g. ``pollypm.supervisor``)
    message: str
    raw: str

    @property
    def short_source(self) -> str:
        """Drop the leading ``pollypm.`` so display rows stay short."""
        if self.name.startswith("pollypm."):
            return self.name[len("pollypm."):]
        return self.name

    @property
    def fingerprint(self) -> str:
        """Match ``error_notifications._record_signature`` exactly.

        The alert pipeline emits ``error_log/critical_error:<hash>``
        for each ERROR+ record; printing the same hash here lets
        users copy-paste between ``pm alerts`` and ``pm errors``.
        """
        return _record_signature(self.name, self.message)


@dataclass
class SourceSummary:
    """One row in the "ERROR sources (last <since>)" rollup."""

    source: str  # ``pollypm.supervisor`` etc.
    count: int
    last_seen: datetime
    last_message: str


@dataclass
class FingerprintSummary:
    """One row in the "Active fingerprints" rollup."""

    fingerprint: str
    count: int
    title: str  # the (truncated) message text
    last_seen: datetime
    source: str


@dataclass
class ErrorSummary:
    """Result of summarizing the log for the default ``pm errors`` view."""

    sources: list[SourceSummary] = field(default_factory=list)
    fingerprints: list[FingerprintSummary] = field(default_factory=list)
    total_errors: int = 0
    window_label: str = "24h"
    fingerprint_window_label: str = "1h"


def parse_duration(spec: str) -> timedelta:
    """Parse ``1h``/``24h``/``7d``/``30m``/``2w`` into a ``timedelta``.

    Raises ``ValueError`` for empty or malformed strings. We bias
    toward strict parsing (no implicit unit) because the wrong
    interpretation of ``--since 1`` would silently widen or narrow
    the user's window.
    """
    if not spec:
        raise ValueError("empty duration")
    match = _DURATION_RE.match(spec)
    if not match:
        raise ValueError(f"could not parse duration: {spec!r}")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return timedelta(seconds=amount * _DURATION_UNITS[unit])


def format_age(now: datetime, then: datetime) -> str:
    """Render ``then`` as ``Xs ago`` / ``Xm ago`` / ``Xh ago`` / ``Xd ago``."""
    delta = now - then
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _record_signature(name: str, message: str) -> str:
    """Mirror ``error_notifications._record_signature``.

    Kept private + duplicated here (rather than imported) so this
    module stays free of the alert/store dependencies pulled in by
    ``error_notifications`` — ``pm errors`` should still work in
    environments where ``error_notifications`` can't import (e.g.
    a fresh checkout with no store).
    """
    payload = f"{name}\n{message}".encode("utf-8", "replace")
    return hashlib.sha1(payload).hexdigest()[:12]


def _normalize_message(message: str) -> str:
    """Mirror ``error_notifications._normalize_message`` exactly.

    Same reason as ``_record_signature`` — fingerprints have to match
    the alerts surface byte-for-byte.
    """
    text = " ".join((message or "").split())
    if len(text) <= _MAX_MESSAGE_LENGTH:
        return text
    return text[: _MAX_MESSAGE_LENGTH - 1] + "..."


def iter_records(lines: Iterable[str]) -> Iterator[LogRecord]:
    """Stream parsed records from raw log lines.

    Continuation lines (tracebacks, ``Traceback (most recent call last):``
    etc.) get folded into the preceding record's ``raw`` block so
    ``--fingerprint`` can print the full traceback. Lines before the
    first valid header are dropped — they belong to a record that
    rotated out.
    """
    pending: LogRecord | None = None
    pending_raw: list[str] = []
    for line in lines:
        # Strip just the trailing newline; preserve any leading
        # whitespace so traceback indentation stays intact.
        if line.endswith("\n"):
            line = line[:-1]
        match = _LINE_RE.match(line)
        if match:
            if pending is not None:
                yield _finalize(pending, pending_raw)
            try:
                ts = datetime.strptime(match.group("ts"), _TIMESTAMP_FMT)
            except ValueError:
                # Malformed timestamp — treat as continuation rather
                # than crash on a single corrupt line.
                if pending is not None:
                    pending_raw.append(line)
                continue
            message = _normalize_message(match.group("message"))
            pending = LogRecord(
                timestamp=ts,
                level=match.group("level"),
                process_tag=match.group("tag"),
                name=match.group("name"),
                message=message,
                raw="",  # filled in by ``_finalize``
            )
            pending_raw = [line]
        else:
            if pending is not None:
                pending_raw.append(line)
    if pending is not None:
        yield _finalize(pending, pending_raw)


def _finalize(record: LogRecord, raw_lines: list[str]) -> LogRecord:
    return LogRecord(
        timestamp=record.timestamp,
        level=record.level,
        process_tag=record.process_tag,
        name=record.name,
        message=record.message,
        raw="\n".join(raw_lines),
    )


def summarize(
    records: Iterable[LogRecord],
    *,
    now: datetime,
    since: timedelta = timedelta(hours=24),
    fingerprint_window: timedelta = timedelta(hours=1),
    fingerprint_min_count: int = 3,
    max_sources: int = 10,
    max_fingerprints: int = 10,
    levels: tuple[str, ...] = ("ERROR", "CRITICAL"),
    window_label: str = "24h",
    fingerprint_window_label: str = "1h",
) -> ErrorSummary:
    """Roll records up into the structures the CLI prints.

    ``levels`` defaults to ERROR+CRITICAL because WARNINGs are the
    bulk of the file and the user's question — "what's broken?" —
    is anchored on ERROR-and-up. ``--raw`` is still there for the
    full stream.
    """
    cutoff = now - since
    fingerprint_cutoff = now - fingerprint_window
    source_counts: dict[str, int] = defaultdict(int)
    source_last: dict[str, tuple[datetime, str]] = {}
    fingerprint_counts: dict[str, int] = defaultdict(int)
    fingerprint_last: dict[str, tuple[datetime, str, str]] = {}
    total = 0
    for record in records:
        if record.level not in levels:
            continue
        if record.timestamp < cutoff:
            continue
        total += 1
        source_counts[record.name] += 1
        prior = source_last.get(record.name)
        if prior is None or record.timestamp > prior[0]:
            source_last[record.name] = (record.timestamp, record.message)
        if record.timestamp >= fingerprint_cutoff:
            fp = record.fingerprint
            fingerprint_counts[fp] += 1
            prior_fp = fingerprint_last.get(fp)
            if prior_fp is None or record.timestamp > prior_fp[0]:
                fingerprint_last[fp] = (
                    record.timestamp,
                    record.message,
                    record.name,
                )

    sources = sorted(
        (
            SourceSummary(
                source=name,
                count=count,
                last_seen=source_last[name][0],
                last_message=source_last[name][1],
            )
            for name, count in source_counts.items()
        ),
        key=lambda row: (-row.count, -row.last_seen.timestamp()),
    )[:max_sources]

    fingerprints = sorted(
        (
            FingerprintSummary(
                fingerprint=fp,
                count=count,
                title=fingerprint_last[fp][1],
                last_seen=fingerprint_last[fp][0],
                source=fingerprint_last[fp][2],
            )
            for fp, count in fingerprint_counts.items()
            if count >= fingerprint_min_count
        ),
        key=lambda row: (-row.count, -row.last_seen.timestamp()),
    )[:max_fingerprints]

    return ErrorSummary(
        sources=sources,
        fingerprints=fingerprints,
        total_errors=total,
        window_label=window_label,
        fingerprint_window_label=fingerprint_window_label,
    )


def find_fingerprint_records(
    records: Iterable[LogRecord],
    fingerprint: str,
) -> list[LogRecord]:
    """Return every record whose fingerprint matches.

    ``--fingerprint`` accepts the bare hash (``d7a7d84090b2``) — we
    match case-insensitively against the 12-char prefix.
    """
    needle = fingerprint.lower().strip()
    return [r for r in records if r.fingerprint.lower() == needle]


def render_summary(summary: ErrorSummary, *, now: datetime) -> str:
    """Render an ``ErrorSummary`` to the multi-section text the CLI prints."""
    lines: list[str] = []
    lines.append(f"-- ERROR sources (last {summary.window_label}) --")
    if not summary.sources:
        lines.append("  (no ERROR/CRITICAL records in window)")
    else:
        most_recent = max(
            summary.sources,
            key=lambda row: row.last_seen,
        )
        for row in summary.sources:
            age = format_age(now, row.last_seen)
            marker = "  <- most recent" if row is most_recent else ""
            lines.append(
                f"  {row.count:>5}  {row.source:<40}  last {age}{marker}"
            )
    lines.append("")
    lines.append(
        "-- Active fingerprints "
        f"(>= 3 occurrences in last {summary.fingerprint_window_label}) --"
    )
    if not summary.fingerprints:
        lines.append("  (no recurring fingerprints in window)")
    else:
        for row in summary.fingerprints:
            title = row.title
            if len(title) > 60:
                title = title[:57] + "..."
            lines.append(
                f"  {row.fingerprint}  {title:<60} "
                f"{row.count}x in last {summary.fingerprint_window_label}"
            )
    lines.append("")
    lines.append("Use:")
    lines.append("  pm errors --raw                       literal log dump")
    lines.append("  pm errors --grep <substr>             filter raw view")
    lines.append("  pm errors --since 1h|24h|7d           change summary window")
    lines.append("  pm errors --fingerprint <hash>        expand one fingerprint")
    return "\n".join(lines)


def render_fingerprint(records: list[LogRecord], fingerprint: str) -> str:
    """Render the full traceback(s) for a fingerprint match."""
    if not records:
        return (
            f"No records match fingerprint {fingerprint!r}. "
            "Run `pm errors` to see active fingerprints, or "
            "`pm errors --raw` for the full log."
        )
    out: list[str] = []
    out.append(
        f"Fingerprint {fingerprint} -- {len(records)} occurrence"
        f"{'s' if len(records) != 1 else ''}:"
    )
    out.append(f"  source: {records[0].name}")
    out.append(f"  message: {records[0].message}")
    out.append("")
    out.append("Most recent occurrence (full traceback):")
    out.append(records[-1].raw)
    return "\n".join(out)


def read_log(path: Path) -> list[LogRecord]:
    """Convenience helper — parse the entire log file into records.

    The 40k-line / 2.9 MB file in the issue parses in well under a
    second, so a one-shot read is fine for the summary path.
    """
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as fp:
        return list(iter_records(fp))


__all__ = [
    "ErrorSummary",
    "FingerprintSummary",
    "LogRecord",
    "SourceSummary",
    "find_fingerprint_records",
    "format_age",
    "iter_records",
    "parse_duration",
    "read_log",
    "render_fingerprint",
    "render_summary",
    "summarize",
]
