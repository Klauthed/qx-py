"""``qx new`` — scaffold new projects."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from qx.cli.templates import preview_tree, render_tree

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def service(
    name: str = typer.Argument(..., help="Service name (kebab-case)."),
    target: Path = typer.Option(
        Path.cwd(),
        "--target",
        "-t",
        help="Directory to create the project in.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files."),
) -> None:
    """Scaffold a new qx service.

    Generates a fully wired service with application/domain/infrastructure/
    presentation layers, a Dockerfile, alembic config, and a passing test.
    """
    pkg_name = _snake(name)
    context = {
        "service_name": name,
        "service_pkg": pkg_name,
        "service_pascal": _pascal(name),
        "service_kebab": _kebab(name),
        "service_pkg_path": pkg_name.replace("_", "/"),
    }
    dest = target / context["service_kebab"]
    if dest.exists() and not force:
        if any(dest.iterdir()):
            console.print(f"[red]error[/red] {dest} exists and is not empty (use --force to overwrite)")
            raise typer.Exit(1)
    dest.mkdir(parents=True, exist_ok=True)

    files = render_tree(
        "qx.cli.scaffolds",
        "service",
        dest,
        context,
        overwrite=force,
    )
    console.rule("[bold green]Service scaffolded[/bold green]")
    preview_tree(files, dest)
    console.print(
        "\n[bold]Next steps:[/bold]\n"
        f"  cd {context['service_kebab']}\n"
        "  uv sync\n"
        "  docker compose -f ../deploy/docker-compose.yaml up -d  # local Postgres/Redis/NATS\n"
        "  uv run alembic upgrade head\n"
        "  uv run uvicorn " + f"{pkg_name}.main:app --reload\n"
    )


def _snake(s: str) -> str:
    import re

    s = re.sub(r"[\s\-]+", "_", s.strip())
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def _pascal(s: str) -> str:
    return "".join(p.capitalize() for p in _snake(s).split("_"))


def _kebab(s: str) -> str:
    return _snake(s).replace("_", "-")
