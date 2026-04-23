"""Tests for the in-session upgrade-notice injection (#718).

Uses fake supervisor + tmux client so no real session is touched. The
fake matches the duck-typed surface the module reads: ``plan_launches``
returns a list of objects with ``.session.role`` / ``.session.name`` /
``.window_name``; the tmux client exposes ``send_keys``.

The module is default-disabled in the production path as of #755 (see
:func:`pollypm.upgrade.inject_notice`); these tests exercise the helper
directly with ``allow_fixture_versions=True`` since the canonical
fixture pair ``("0.1.0", "0.2.0")`` is otherwise rejected (#756).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pollypm import upgrade_notice


@dataclass(slots=True)
class _FakeSession:
    name: str
    role: str
    project: str | None = None


@dataclass(slots=True)
class _FakeLaunch:
    session: _FakeSession
    window_name: str


class _FakeTmux:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def send_keys(self, target: str, text: str, press_enter: bool = True) -> None:
        self.calls.append((target, text, press_enter))


class _FakeConfigProject:
    tmux_session = "pollypm-test"


class _FakeConfig:
    class project:  # noqa: N801 — matches supervisor shape
        tmux_session = "pollypm-test"

    projects: dict = {}


class _FakeSupervisor:
    def __init__(self, launches: list[_FakeLaunch]) -> None:
        self._launches = launches
        self.tmux = _FakeTmux()
        self.config = _FakeConfig()

    def plan_launches(self) -> list[_FakeLaunch]:
        return self._launches


def _launches(specs: list[tuple[str, str, str]]) -> list[_FakeLaunch]:
    """Shorthand: [(session_name, role, window_name), ...]."""
    return [
        _FakeLaunch(_FakeSession(name=n, role=r), window_name=w)
        for n, r, w in specs
    ]


def _inject(sup, old="0.1.0", new="0.2.0", **kwargs):
    """Helper: call inject_system_update_notice with the fixture guard
    disabled, since tests use the canonical fixture version pair."""
    return upgrade_notice.inject_system_update_notice(
        old, new, supervisor=sup, allow_fixture_versions=True, **kwargs,
    )


# --------------------------------------------------------------------------- #
# build_notice — text shape
# --------------------------------------------------------------------------- #

def test_build_notice_contains_versions_and_path() -> None:
    text = upgrade_notice.build_notice("1.2.0", "1.3.2", "/abs/docs/worker-guide.md")
    assert "v1.2.0" in text
    assert "v1.3.2" in text
    assert "/abs/docs/worker-guide.md" in text
    assert "supersedes" in text.lower()
    assert "<system-update>" in text
    assert "</system-update>" in text


def test_build_notice_instructs_re_read() -> None:
    text = upgrade_notice.build_notice("0.1.0", "0.2.0", "/abs/docs/worker-guide.md")
    # Model must be told to re-read BEFORE next action.
    assert "re-read" in text.lower()
    assert "next action" in text.lower()


# --------------------------------------------------------------------------- #
# Fixture-leak guard (#756)
# --------------------------------------------------------------------------- #

def test_inject_rejects_fixture_versions_by_default() -> None:
    sup = _FakeSupervisor(_launches([("worker_demo", "worker", "worker-demo")]))
    with pytest.raises(upgrade_notice.FixtureLeakError):
        upgrade_notice.inject_system_update_notice(
            "0.1.0", "0.2.0", supervisor=sup,
        )
    # The call must raise BEFORE sending anything to tmux.
    assert sup.tmux.calls == []


def test_inject_allows_fixture_versions_with_explicit_opt_in() -> None:
    sup = _FakeSupervisor(_launches([("worker_demo", "worker", "worker-demo")]))
    results = upgrade_notice.inject_system_update_notice(
        "0.1.0", "0.2.0", supervisor=sup, allow_fixture_versions=True,
    )
    # No exception; call proceeded normally.
    assert len(results) == 1


def test_inject_accepts_non_fixture_versions_without_opt_in() -> None:
    sup = _FakeSupervisor(_launches([("worker_demo", "worker", "worker-demo")]))
    results = upgrade_notice.inject_system_update_notice(
        "1.0.0rc2", "1.1.0", supervisor=sup,
    )
    assert len(results) == 1  # real version pair, guard does not fire


# --------------------------------------------------------------------------- #
# inject_system_update_notice — per-role routing
# --------------------------------------------------------------------------- #

def test_inject_sends_to_worker_session() -> None:
    sup = _FakeSupervisor(_launches([
        ("worker_demo", "worker", "worker-demo"),
    ]))
    results = _inject(sup)
    assert len(results) == 1
    assert results[0].delivered is True
    assert len(sup.tmux.calls) == 1
    target, text, _ = sup.tmux.calls[0]
    assert target == "pollypm-test:worker-demo"
    # Path should resolve to an absolute worker-guide.md location.
    assert "worker-guide.md" in text


def test_inject_notice_uses_absolute_path() -> None:
    """Regression for #762/#763 — paths in the notice body must be
    absolute so sessions resolve them regardless of CWD."""
    sup = _FakeSupervisor(_launches([("worker_demo", "worker", "worker-demo")]))
    _inject(sup)
    _, text, _ = sup.tmux.calls[0]
    # Path fragment should not start with "src/" (that was the bug)
    # and should contain the absolute path separator prefix.
    assert "re-read your operating guide at /" in text


def test_inject_routes_polly_to_operator_guide() -> None:
    sup = _FakeSupervisor(_launches([("polly", "operator-pm", "polly")]))
    _inject(sup)
    _, text, _ = sup.tmux.calls[0]
    assert "polly-operator-guide.md" in text


def test_inject_routes_russell_to_reviewer_guide() -> None:
    sup = _FakeSupervisor(_launches([("russell", "reviewer", "russell")]))
    _inject(sup)
    _, text, _ = sup.tmux.calls[0]
    assert "russell.md" in text


def test_inject_routes_architect_to_existing_architect_guide() -> None:
    """Regression for #762 — architect path was pointing at a file
    that didn't exist. The resolver must land on a real file."""
    sup = _FakeSupervisor(_launches([("arch_notesy", "architect", "architect-notesy")]))
    _inject(sup)
    assert len(sup.tmux.calls) == 1
    _, text, _ = sup.tmux.calls[0]
    assert "architect.md" in text
    # Ensure the path in the notice actually refers to an extant file.
    import re
    match = re.search(r"guide at (/\S+?\.md)", text)
    assert match is not None, f"no .md path found in notice: {text!r}"
    from pathlib import Path
    resolved = Path(match.group(1))
    assert resolved.exists(), f"architect guide path in notice does not resolve: {resolved}"


def test_inject_skips_heartbeat_role() -> None:
    sup = _FakeSupervisor(_launches([
        ("heartbeat", "heartbeat-supervisor", "heartbeat"),
    ]))
    results = _inject(sup)
    assert len(results) == 1
    assert results[0].delivered is False
    assert "skipped" in results[0].reason
    assert sup.tmux.calls == []


def test_inject_delivers_to_mixed_fleet() -> None:
    sup = _FakeSupervisor(_launches([
        ("polly", "operator-pm", "polly"),
        ("russell", "reviewer", "russell"),
        ("worker_1", "worker", "worker-1"),
        ("worker_2", "worker", "worker-2"),
        ("heartbeat", "heartbeat-supervisor", "heartbeat"),
    ]))
    results = _inject(sup)
    assert len(results) == 5
    delivered = [r for r in results if r.delivered]
    skipped = [r for r in results if not r.delivered]
    assert len(delivered) == 4  # polly + russell + 2 workers
    assert len(skipped) == 1    # heartbeat
    assert len(sup.tmux.calls) == 4


def test_inject_reports_send_failures() -> None:
    class _BrokenTmux:
        def send_keys(self, *_args, **_kwargs):
            raise RuntimeError("tmux unavailable")

    sup = _FakeSupervisor(_launches([("worker_1", "worker", "worker-1")]))
    sup.tmux = _BrokenTmux()
    results = _inject(sup)
    assert len(results) == 1
    assert results[0].delivered is False
    assert "send failed" in results[0].reason


def test_inject_no_supervisor_returns_empty() -> None:
    """No live supervisor (not in tmux / config missing) → return []
    without crashing."""
    from pathlib import Path
    results = upgrade_notice.inject_system_update_notice(
        "0.1.0", "0.2.0",
        supervisor=None,
        config_path=Path("/nonexistent/pollypm.toml"),
        allow_fixture_versions=True,
    )
    assert results == []


def test_inject_unknown_role_defaults_to_worker_guide() -> None:
    sup = _FakeSupervisor(_launches([("pete", "polyglot", "pete")]))
    _inject(sup)
    _, text, _ = sup.tmux.calls[0]
    assert "worker-guide.md" in text


# --------------------------------------------------------------------------- #
# Project-local fork preference (#763)
# --------------------------------------------------------------------------- #

def test_inject_prefers_project_local_fork_when_present(tmp_path) -> None:
    """When the session's project has a forked role guide under
    ``<project>/.pollypm/project-guides/<role>.md``, the notice should
    point at that absolute path instead of the built-in."""
    project_path = tmp_path / "my_project"
    guides_dir = project_path / ".pollypm" / "project-guides"
    guides_dir.mkdir(parents=True)
    local_guide = guides_dir / "worker.md"
    local_guide.write_text("---\nforked_from: abc123\n---\n# local worker guide\n")

    class _ProjConfig:
        class project:
            tmux_session = "pollypm-test"

        projects: dict

    config = _ProjConfig()
    config.projects = {"my_project": type("P", (), {"path": project_path})()}

    class _Sup:
        def __init__(self):
            self.tmux = _FakeTmux()
            self.config = config

        def plan_launches(self):
            return [
                _FakeLaunch(
                    _FakeSession(name="worker_demo", role="worker", project="my_project"),
                    window_name="worker-demo",
                )
            ]

    sup = _Sup()
    results = upgrade_notice.inject_system_update_notice(
        "0.1.0", "0.2.0", supervisor=sup, allow_fixture_versions=True,
    )
    assert len(results) == 1
    assert results[0].delivered is True
    _, text, _ = sup.tmux.calls[0]
    assert str(local_guide.resolve()) in text


# --------------------------------------------------------------------------- #
# summarize()
# --------------------------------------------------------------------------- #

def test_summarize_counts_buckets_correctly() -> None:
    results = [
        upgrade_notice.NoticeResult("a", "worker", True, "sent"),
        upgrade_notice.NoticeResult("b", "worker", True, "sent"),
        upgrade_notice.NoticeResult("c", "heartbeat-supervisor", False, "skipped: heartbeat-supervisor"),
        upgrade_notice.NoticeResult("d", "worker", False, "send failed: TimeoutError"),
    ]
    notified, skipped, failed = upgrade_notice.summarize(results)
    assert (notified, skipped, failed) == (2, 1, 1)


def test_summarize_empty_list() -> None:
    assert upgrade_notice.summarize([]) == (0, 0, 0)
