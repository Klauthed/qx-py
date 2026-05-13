"""``qx generate`` — code generation into an existing service.

Each subcommand expects the cwd to be a qx service directory
(containing a ``pyproject.toml`` and a Python package matching the service
name). The CLI infers the service package from ``pyproject.toml``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import typer
from qx.cli.templates import preview_tree, render_tree
from rich.console import Console

app = typer.Typer(no_args_is_help=True)
console = Console()


def _service_package(start: Path | None = None) -> tuple[Path, str]:
    """Walk up to find ``pyproject.toml`` and infer the service package."""
    p = (start or Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        pp = candidate / "pyproject.toml"
        if pp.exists():
            try:
                data = tomllib.loads(pp.read_text())
            except Exception:
                continue
            name = data.get("project", {}).get("name", "")
            if not name:
                continue
            pkg = name.replace("-", "_")
            # Validate the package directory actually exists
            if (candidate / "src" / pkg).exists():
                return candidate, pkg
            if (candidate / pkg).exists():
                return candidate, pkg
    raise typer.BadParameter("Could not locate a qx service from current directory.")


@app.command()
def aggregate(
    name: str = typer.Argument(..., help="Aggregate name (PascalCase, e.g. 'Invoice')."),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate a new aggregate (domain entity + table mapping + repository + migration stub)."""
    import secrets  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    root, pkg = _service_package()
    context = _names(name, pkg)
    context["revision_id"] = secrets.token_hex(6)
    context["create_date"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    context["migration_name"] = f"create_{context['name_snake']}"
    files = render_tree(
        "qx.cli.scaffolds",
        "aggregate",
        root,
        context,
        overwrite=force,
    )
    console.rule(f"[bold green]aggregate {context['name_pascal']} generated[/bold green]")
    preview_tree(files, root)


@app.command()
def command(
    name: str = typer.Argument(..., help="Command name (e.g. 'CreateUser')."),
    aggregate_for: str = typer.Option(
        "",
        "--aggregate",
        "-a",
        help="Target aggregate (for layout hints).",
    ),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate a Command class and its handler."""
    root, pkg = _service_package()
    context = _names(name, pkg)
    context["aggregate"] = _names(aggregate_for, pkg) if aggregate_for else None
    files = render_tree(
        "qx.cli.scaffolds",
        "command",
        root,
        context,
        overwrite=force,
    )
    console.rule(f"[bold green]command {context['name_pascal']} generated[/bold green]")
    preview_tree(files, root)


@app.command()
def query(
    name: str = typer.Argument(..., help="Query name (e.g. 'GetUser')."),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate a Query class and its handler."""
    root, pkg = _service_package()
    context = _names(name, pkg)
    files = render_tree(
        "qx.cli.scaffolds",
        "query",
        root,
        context,
        overwrite=force,
    )
    console.rule(f"[bold green]query {context['name_pascal']} generated[/bold green]")
    preview_tree(files, root)


@app.command()
def esaggregate(
    name: str = typer.Argument(..., help="Aggregate name (PascalCase, e.g. 'Shipment')."),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate an event-sourced aggregate (EventSourcedAggregate subclass, no ORM mapping)."""
    root, pkg = _service_package()
    context = _names(name, pkg)
    files = render_tree(
        "qx.cli.scaffolds",
        "esaggregate",
        root,
        context,
        overwrite=force,
    )
    console.rule(
        f"[bold green]event-sourced aggregate {context['name_pascal']} generated[/bold green]"
    )
    preview_tree(files, root)


@app.command()
def event(
    name: str = typer.Argument(..., help="Event name (e.g. 'UserRegistered')."),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate an IntegrationEvent class."""
    root, pkg = _service_package()
    context = _names(name, pkg)
    files = render_tree(
        "qx.cli.scaffolds",
        "event",
        root,
        context,
        overwrite=force,
    )
    console.rule(f"[bold green]event {context['name_pascal']} generated[/bold green]")
    preview_tree(files, root)


@app.command()
def endpoint(
    path: str = typer.Argument(..., help="Route path (e.g. '/users')."),
    method: str = typer.Option("POST", "--method", "-m"),
    handler: str = typer.Option(..., "--handler", "-h", help="Command or Query name to dispatch."),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate an HTTP endpoint that dispatches a command/query."""
    root, pkg = _service_package()
    context = _names(handler, pkg)
    context.update(
        {
            "endpoint_path": path,
            "endpoint_method": method.upper(),
            "endpoint_method_lower": method.lower(),
        }
    )
    files = render_tree(
        "qx.cli.scaffolds",
        "endpoint",
        root,
        context,
        overwrite=force,
    )
    console.rule(f"[bold green]endpoint {method.upper()} {path} generated[/bold green]")
    preview_tree(files, root)


# ---- helpers ----


def _names(name: str, pkg: str) -> dict[str, Any]:
    """Build the standard naming variants for a generated artifact."""
    snake = _snake(name)
    return {
        "name_pascal": _pascal(name),
        "name_snake": snake,
        "name_kebab": _kebab(name),
        "name_upper": snake.upper(),
        "service_pkg": pkg,
    }


def _snake(s: str) -> str:
    import re  # noqa: PLC0415

    s = re.sub(r"[\s\-]+", "_", s.strip())
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _pascal(s: str) -> str:
    return "".join(p.capitalize() for p in _snake(s).split("_"))


def _kebab(s: str) -> str:
    return _snake(s).replace("_", "-")
