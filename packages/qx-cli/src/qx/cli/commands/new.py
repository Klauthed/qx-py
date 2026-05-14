"""``qx new`` — scaffold new projects."""

from __future__ import annotations

from pathlib import Path

import typer
from qx.cli.templates import preview_tree, render_tree
from rich.console import Console

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def service(
    name: str = typer.Argument(..., help="Service name (kebab-case)."),
    target: Path = typer.Option(  # noqa: B008
        Path.cwd(),  # noqa: B008
        "--target",
        "-t",
        help="Directory to create the project in.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files."),
    slices: bool = typer.Option(
        False,
        "--slices/--no-slices",
        help="Use vertical-slice layout (feature-first) instead of layered.",
    ),
    domain: str = typer.Option(
        "",
        "--domain",
        "-d",
        help="Starter slice name for --slices (defaults to service name).",
    ),
) -> None:
    """Scaffold a new qx service.

    By default generates a layered service (application/domain/infrastructure/
    presentation). With --slices, generates a vertical-slice layout where each
    feature is self-contained under its own directory.
    """
    pkg_name = _snake(name)
    context = {
        "service_name": name,
        "service_pkg": pkg_name,
        "service_pascal": _pascal(name),
        "service_kebab": _kebab(name),
        "service_pkg_path": pkg_name.replace("_", "/"),
    }

    if slices:
        domain_snake = _snake(domain) if domain else pkg_name
        context["domain_snake"] = domain_snake
        context["domain_pascal"] = _pascal(domain_snake)
        context["domain_kebab"] = _kebab(domain_snake)
        template = "service_vs"
    else:
        template = "service"

    dest = target / context["service_kebab"]
    if dest.exists() and not force and any(dest.iterdir()):
        console.print(f"[red]error[/red] {dest} exists and is not empty (use --force to overwrite)")
        raise typer.Exit(1)
    dest.mkdir(parents=True, exist_ok=True)

    files = render_tree(
        "qx.cli.scaffolds",
        template,
        dest,
        context,
        overwrite=force,
    )
    console.rule("[bold green]Service scaffolded[/bold green]")
    preview_tree(files, dest)

    next_steps = (
        "\n[bold]Next steps:[/bold]\n"
        f"  cd {context['service_kebab']}\n"
        "  uv sync\n"
        "  qx dev up          # start Postgres · Redis · NATS · Grafana\n"
        "  uv run alembic upgrade head\n"
        f"  uv run uvicorn {pkg_name}.main:app --reload\n"
    )
    if slices:
        next_steps += (
            "\n[bold]Add more slices:[/bold]\n"
            "  qx generate slice <slice_name>\n"
            "  qx generate command <slice_name>/<CommandName>\n"
            "  qx generate query <slice_name>/<QueryName>\n"
        )
    console.print(next_steps)


def _snake(s: str) -> str:
    import re  # noqa: PLC0415

    s = re.sub(r"[\s\-]+", "_", s.strip())
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def _pascal(s: str) -> str:
    return "".join(p.capitalize() for p in _snake(s).split("_"))


def _kebab(s: str) -> str:
    return _snake(s).replace("_", "-")
