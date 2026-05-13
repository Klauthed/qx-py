"""``qx projections`` — inspect and manage projection checkpoints.

Commands::

    qx projections status             # show checkpoint position + lag for all projections
    qx projections rebuild <name>     # reset checkpoint and replay all events
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

app = typer.Typer(no_args_is_help=True)
console = Console()


def _get_db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=1)
    return url


@app.command()
def status(
    db_url: str = typer.Option(
        None,
        "--db-url",
        envvar="DATABASE_URL",
        help="Postgres connection URL (falls back to DATABASE_URL).",
    ),
) -> None:
    """Show checkpoint position and lag for every projection."""

    async def _run() -> None:
        from qx.projections.table import include_projection_tables  # noqa: PLC0415
        from sqlalchemy import MetaData, select, text  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

        url = db_url or _get_db_url()
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url, echo=False)
        metadata = MetaData()
        checkpoints_table = include_projection_tables(metadata)

        try:
            async with engine.connect() as conn:
                # Check if the table exists
                exists = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.tables"
                            " WHERE table_name = 'qx_projection_checkpoints'"
                        )
                    )
                ).first()
                if not exists:
                    console.print(
                        "[yellow]qx_projection_checkpoints table not found."
                        " Run migrations first.[/yellow]"
                    )
                    return

                rows = (
                    (
                        await conn.execute(
                            select(checkpoints_table).order_by(checkpoints_table.c.projection_name)
                        )
                    )
                    .mappings()
                    .all()
                )

                # Get the max sequence from the events table (if it exists)
                max_seq: int | None = None
                events_exists = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.tables"
                            " WHERE table_name = 'qx_aggregate_events'"
                        )
                    )
                ).first()
                if events_exists:
                    row = (
                        await conn.execute(
                            text("SELECT COALESCE(MAX(sequence), 0) FROM qx_aggregate_events")
                        )
                    ).first()
                    max_seq = int(row[0]) if row else 0

        finally:
            await engine.dispose()

        if not rows:
            console.print("[dim]No projections registered yet.[/dim]")
            return

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Projection")
        table.add_column("Checkpoint", justify="right")
        if max_seq is not None:
            table.add_column("Max Seq", justify="right")
            table.add_column("Lag", justify="right")
        table.add_column("Updated At")

        for r in rows:
            last_seq = int(r["last_sequence"])
            updated = r["updated_at"]
            updated_str = (
                updated.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if updated else "—"
            )
            if max_seq is not None:
                lag = max_seq - last_seq
                lag_str = (
                    f"[green]{lag}[/green]"
                    if lag == 0
                    else f"[yellow]{lag}[/yellow]"
                    if lag < 1000
                    else f"[red]{lag}[/red]"
                )
                table.add_row(
                    r["projection_name"],
                    str(last_seq),
                    str(max_seq),
                    lag_str,
                    updated_str,
                )
            else:
                table.add_row(r["projection_name"], str(last_seq), updated_str)

        console.print(table)

    asyncio.run(_run())


@app.command()
def rebuild(
    name: str = typer.Argument(..., help="Name of the projection to rebuild."),
    db_url: str = typer.Option(
        None,
        "--db-url",
        envvar="DATABASE_URL",
        help="Postgres connection URL (falls back to DATABASE_URL).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Reset a projection checkpoint to 0 so the runner replays all events on next tick.

    This does NOT replay events inline — it resets the checkpoint so the
    background ProjectionRunner picks up from sequence 0 on its next run.
    To force an immediate replay, restart the service after running this command.
    """
    if not yes:
        confirmed = typer.confirm(
            f"Reset checkpoint for projection [bold]{name}[/bold]? "
            "The runner will replay all events from the beginning."
        )
        if not confirmed:
            raise typer.Abort()

    async def _run() -> int:
        from qx.projections.table import include_projection_tables  # noqa: PLC0415
        from sqlalchemy import MetaData, text  # noqa: PLC0415
        from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

        url = db_url or _get_db_url()
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url, echo=False)
        metadata = MetaData()
        checkpoints_table = include_projection_tables(metadata)

        try:
            async with engine.begin() as conn:
                exists = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM qx_projection_checkpoints WHERE projection_name = :name"
                        ),
                        {"name": name},
                    )
                ).first()
                if exists is None:
                    console.print(
                        f"[yellow]Projection [bold]{name}[/bold] has no checkpoint row."
                        " It will be created at sequence 0 when the runner first runs.[/yellow]"
                    )

                stmt = (
                    pg_insert(checkpoints_table)
                    .values(
                        projection_name=name,
                        last_sequence=0,
                        updated_at=datetime.now(UTC),
                    )
                    .on_conflict_do_update(
                        index_elements=["projection_name"],
                        set_={
                            "last_sequence": 0,
                            "updated_at": datetime.now(UTC),
                        },
                    )
                )
                await conn.execute(stmt)

        finally:
            await engine.dispose()

        return 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(f"Resetting checkpoint for [bold]{name}[/bold]…", total=None)
        asyncio.run(_run())

    console.print(
        f"[green]✓[/green]  Projection [bold]{name}[/bold] checkpoint reset to 0."
        " Restart the service to trigger replay."
    )
