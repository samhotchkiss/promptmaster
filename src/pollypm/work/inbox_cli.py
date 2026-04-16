"""CLI commands for the work-service-backed inbox view.

Exposes ``pm inbox`` and ``pm inbox show <task_id>``. The inbox is defined
entirely in terms of work-service queries — see :mod:`inbox_view` for the
membership rules.
"""

from __future__ import annotations

import json
from typing import Optional

import typer

from pollypm.work.cli import (
    _DB_OPTION,
    _JSON_OPTION,
    _PROJECT_OPTION,
    _print_task,
    _project_from_task_id,
    _svc,
    _task_to_dict,
    task_get,
)
from pollypm.work.inbox_view import inbox_tasks


inbox_app = typer.Typer(help="Work assigned to the user (work-service-backed).")


@inbox_app.callback(invoke_without_command=True)
def inbox_root(
    ctx: typer.Context,
    project: Optional[str] = _PROJECT_OPTION,
    db: str = _DB_OPTION,
    output_json: bool = _JSON_OPTION,
) -> None:
    """Show tasks waiting on the user.

    A task appears here when the flow's current node expects a human actor,
    or when the task's roles assign work to the ``user``.
    """
    if ctx.invoked_subcommand is not None:
        return

    svc = _svc(db, project=project)
    tasks = inbox_tasks(svc, project=project)

    if output_json:
        typer.echo(
            json.dumps(
                {
                    "assigned_count": len(tasks),
                    "tasks": [_task_to_dict(t) for t in tasks],
                },
                indent=2,
                default=str,
            )
        )
        return

    typer.echo(f"Inbox: {len(tasks)} assigned")
    if not tasks:
        typer.echo("No tasks waiting for you.")
        return

    typer.echo(f"{'ID':<20} {'Status':<14} {'Priority':<10} {'Title'}")
    typer.echo("-" * 70)
    for t in tasks:
        typer.echo(
            f"{t.task_id:<20} {t.work_status.value:<14} "
            f"{t.priority.value:<10} {t.title}"
        )


@inbox_app.command("show")
def inbox_show(
    task_id: str = typer.Argument(..., help="Task ID (project/number)"),
    db: str = _DB_OPTION,
    output_json: bool = _JSON_OPTION,
) -> None:
    """Show full details of an inbox task. Alias for ``pm task get``."""
    # Delegate to the existing task get implementation so behaviour stays
    # identical (context loading, JSON shape, ...).
    task_get(task_id=task_id, db=db, output_json=output_json)
