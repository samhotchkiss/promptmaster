"""``pm update`` command registration (#1079).

Thin Typer shim over :mod:`pollypm.update`. The flow lives there so the
cockpit keystroke (deliberately scoped out of #1079, follow-up issue)
can call the same entry point.
"""

from __future__ import annotations

import typer

from pollypm.cli_help import help_with_examples


def register_update_commands(app: typer.Typer) -> None:
    @app.command(
        help=help_with_examples(
            "Fetch origin/main, fast-forward local source, and reinstall PollyPM.",
            [
                ("pm update", "fetch + reset --hard + uv tool install --reinstall"),
                ("pm update --check-only", "report how many commits behind, don't mutate"),
            ],
            trailing=(
                "Use this when you installed PollyPM from a git checkout and want "
                "the latest fixes from origin/main without learning git. For users "
                "who installed via `uv tool install pollypm` (not from source), "
                "use `pm upgrade` instead."
            ),
        )
    )
    def update(
        check_only: bool = typer.Option(
            False,
            "--check-only",
            help="Report how many commits behind origin/main, don't fetch or install.",
        ),
    ) -> None:
        from pollypm.update import update as run_update

        result = run_update(check_only=check_only, emit=typer.echo)

        typer.echo("")
        if result.refused:
            typer.echo(f"status:    {result.message}")
            raise typer.Exit(code=1)

        if result.old_sha and result.new_sha and result.old_sha != result.new_sha:
            typer.echo(f"range:     {result.old_sha[:12]} → {result.new_sha[:12]}")
        elif result.old_sha:
            typer.echo(f"head:      {result.old_sha[:12]}")
        typer.echo(f"commits:   {result.count}")
        if result.commits:
            typer.echo("")
            typer.echo("-- commits --")
            for commit in result.commits:
                typer.echo(f"  {commit.sha[:12]}  {commit.subject}")
        typer.echo("")
        typer.echo(f"status:    {result.message}")

        if not result.ok:
            if result.stderr:
                typer.echo("")
                typer.echo(result.stderr)
            raise typer.Exit(code=1)
