"""Tests for wg05 / #242: every `pm ... --help` carries an Examples section.

Typer's rich renderer collapses whitespace in the epilog, so examples
are embedded in the top-level help text itself (bullet format, which
Typer preserves). This module asserts that every documented subgroup's
--help output mentions 'Examples' and contains at least 2 copy-paste
invocations.

It also exercises the new `pm help worker` meta-command.
"""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from pollypm.cli import app


@pytest.fixture
def runner():
    return CliRunner()


# Every group listed here must carry an "Examples:" section with at
# least 2 `pm …` invocations. If a subgroup is deprecated or removed
# in the future, drop it here (and mention why in the commit).
_GROUPS_WITH_EXAMPLES = [
    (),  # top-level pm --help
    ("task",),
    ("session",),
    ("plugins",),
    ("rail",),
    ("project",),
    ("downtime",),
    ("advisor",),
    ("briefing",),
    ("memory",),
    ("activity",),
    ("jobs",),
    ("inbox",),
    ("flow",),
    ("alert",),
    ("heartbeat",),
    ("issue",),
    ("report",),
    ("itsalive",),
]


@pytest.mark.parametrize("group", _GROUPS_WITH_EXAMPLES)
def test_group_help_has_examples_section(runner, group):
    args = list(group) + ["--help"]
    result = runner.invoke(app, args)
    # Help should render (exit 0 for --help)
    assert result.exit_code == 0, (
        f"`pm {' '.join(args)}` help did not render:\n{result.output}"
    )
    # The literal word "Examples" — either "Examples:" or
    # "Examples (typical worker flow):" qualifies.
    assert "Examples" in result.output, (
        f"`pm {' '.join(args)}` --help has no Examples section:\n{result.output}"
    )
    # At least two `pm ` invocations (copy-paste-ready commands).
    pm_mentions = result.output.count("pm ")
    assert pm_mentions >= 2, (
        f"`pm {' '.join(args)}` --help has only {pm_mentions} "
        f"`pm ` references; expected >= 2 concrete examples:\n"
        f"{result.output}"
    )


# ---------------------------------------------------------------------------
# pm help worker — meta-command that prints the worker guide
# ---------------------------------------------------------------------------


def test_pm_help_worker_prints_guide(runner):
    result = runner.invoke(app, ["help", "worker"])
    assert result.exit_code == 0, result.output
    # Signature strings from docs/worker-guide.md
    assert "Worker Guide" in result.output
    assert "claim" in result.output
    assert "pm task done" in result.output
    assert "What NOT to do" in result.output


def test_pm_help_unknown_role_lists_available(runner):
    result = runner.invoke(app, ["help", "martian"])
    assert result.exit_code != 0
    assert "martian" in result.output
    # Must name the fix: what roles are available.
    assert "worker" in result.output


# ---------------------------------------------------------------------------
# Specific content checks for the most important groups.
# ---------------------------------------------------------------------------


def test_task_help_shows_typical_worker_flow(runner):
    result = runner.invoke(app, ["task", "--help"])
    assert result.exit_code == 0
    # The worker flow sequence should be readable top-to-bottom.
    assert "pm task next" in result.output
    assert "pm task get" in result.output
    assert "pm task claim" in result.output
    assert "pm task done" in result.output


def test_top_level_help_points_to_worker_guide(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # Users should see the help-worker meta-command prominently.
    assert "pm help worker" in result.output
