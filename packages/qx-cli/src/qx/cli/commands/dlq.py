"""``qx dlq`` — inspect and replay dead-letter queue messages.

Commands::

    qx dlq list                        # show recent dead letters
    qx dlq replay <id>                 # re-publish a dead letter to its original NATS subject
"""

from __future__ import annotations

import asyncio
import os

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True)
console = Console()


def _get_db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        console.print("[red]DATABASE_URL is not set.[/red]")
        raise typer.Exit(code=1)
    return url


def _get_nats_url() -> str:
    return os.getenv("NATS_URL", "nats://localhost:4222")


@app.command("list")
def list_dead_letters(
    db_url: str = typer.Option(
        None,
        "--db-url",
        envvar="DATABASE_URL",
        help="Postgres connection URL.",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum rows to display."),
    event_name: str = typer.Option(
        None,
        "--event-name",
        "-e",
        help="Filter by event name (substring match).",
    ),
) -> None:
    """List recent dead-letter messages."""

    async def _run() -> None:
        from sqlalchemy import text  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

        url = (db_url or _get_db_url()).replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url, echo=False)

        try:
            async with engine.connect() as conn:
                exists = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.tables"
                            " WHERE table_name = 'qx_dead_letters'"
                        )
                    )
                ).first()
                if not exists:
                    console.print(
                        "[yellow]qx_dead_letters table not found."
                        " Run migrations or configure DeadLetterStore first.[/yellow]"
                    )
                    return

                filters = ""
                params: dict = {"limit": limit}
                if event_name:
                    filters = "WHERE event_name ILIKE :event_name"
                    params["event_name"] = f"%{event_name}%"

                rows = (
                    (
                        await conn.execute(
                            text(
                                f"""
                            SELECT id, event_name, delivered_count,
                                   last_error, failed_at, subject
                            FROM qx_dead_letters
                            {filters}
                            ORDER BY failed_at DESC
                            LIMIT :limit
                            """
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .all()
                )
        finally:
            await engine.dispose()

        if not rows:
            console.print("[dim]No dead letters found.[/dim]")
            return

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("ID", style="dim")
        table.add_column("Event Name")
        table.add_column("Deliveries", justify="right")
        table.add_column("Failed At")
        table.add_column("Last Error")

        for r in rows:
            failed_str = r["failed_at"].strftime("%Y-%m-%d %H:%M:%S") if r["failed_at"] else "—"
            error = (r["last_error"] or "")[:60]
            if len(r["last_error"] or "") > 60:
                error += "…"
            table.add_row(
                str(r["id"])[:8] + "…",
                r["event_name"],
                str(r["delivered_count"]),
                failed_str,
                error,
            )

        console.print(table)
        console.print(f"\n[dim]{len(rows)} row(s) shown (limit {limit})[/dim]")

    asyncio.run(_run())


@app.command()
def replay(
    id: str = typer.Argument(..., help="UUID of the dead letter row to replay."),
    db_url: str = typer.Option(
        None,
        "--db-url",
        envvar="DATABASE_URL",
        help="Postgres connection URL.",
    ),
    nats_url: str = typer.Option(
        None,
        "--nats-url",
        envvar="NATS_URL",
        help="NATS server URL (falls back to NATS_URL or nats://localhost:4222).",
    ),
    subject_override: str = typer.Option(
        None,
        "--subject",
        help="Override the NATS subject (default: use the original subject from the DB row).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Re-publish a dead letter to its original NATS subject.

    Fetches the stored payload from ``qx_dead_letters`` by ID and
    republishes it to the original NATS subject so the worker can
    attempt processing again.
    """

    async def _run() -> None:
        import json  # noqa: PLC0415

        import nats  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

        url = (db_url or _get_db_url()).replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url, echo=False)

        try:
            async with engine.connect() as conn:
                row = (
                    (
                        await conn.execute(
                            text(
                                "SELECT id, event_name, payload, headers, subject"
                                " FROM qx_dead_letters WHERE id = :id"
                            ),
                            {"id": id},
                        )
                    )
                    .mappings()
                    .first()
                )
        finally:
            await engine.dispose()

        if row is None:
            console.print(f"[red]Dead letter [bold]{id}[/bold] not found.[/red]")
            raise typer.Exit(code=1)

        target_subject = subject_override or row["subject"]
        if not target_subject:
            console.print("[red]Row has no stored subject. Use --subject to specify one.[/red]")
            raise typer.Exit(code=1)

        if not yes:
            confirmed = typer.confirm(
                f"Replay [bold]{row['event_name']}[/bold] → [bold]{target_subject}[/bold]?"
            )
            if not confirmed:
                raise typer.Abort()

        payload_bytes = (
            row["payload"].encode()
            if isinstance(row["payload"], str)
            else json.dumps(row["payload"]).encode()
        )

        # Reconstruct headers from stored headers dict, dropping None values.
        stored_headers: dict = {}
        if row["headers"]:
            raw = row["headers"] if isinstance(row["headers"], dict) else json.loads(row["headers"])
            stored_headers = {k: v for k, v in raw.items() if v is not None}

        nc = await nats.connect(nats_url or _get_nats_url())
        try:
            js = nc.jetstream()
            await js.publish(target_subject, payload_bytes, headers=stored_headers or None)
        finally:
            await nc.drain()

        console.print(
            f"[green]✓[/green]  Replayed [bold]{row['event_name']}[/bold]"
            f" → [bold]{target_subject}[/bold]"
        )

    asyncio.run(_run())
