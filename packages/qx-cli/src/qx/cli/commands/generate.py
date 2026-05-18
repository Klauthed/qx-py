"""``qx generate`` — code generation into an existing service.

Each subcommand expects the cwd to be a qx service directory
(containing a ``pyproject.toml`` and a Python package matching the service
name). The CLI infers the service package from ``pyproject.toml``.

Vertical-slice services support a ``<slice>/<Name>`` syntax:

    qx generate command user/CreateUser    # → user/commands/create_user.py
    qx generate query   user/GetUser       # → user/queries/get_user.py
    qx generate slice   payment            # → payment/ skeleton
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
    name: str = typer.Argument(
        ..., help="Command name. Use 'slice/Name' for vertical-slice services."
    ),
    aggregate_for: str = typer.Option(
        "",
        "--aggregate",
        "-a",
        help="Target aggregate (for layout hints, layered mode only).",
    ),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate a Command class and its handler.

    Layered:  qx generate command CreateUser
    VS:       qx generate command user/CreateUser
    """
    root, pkg = _service_package()
    if "/" in name:
        slice_name, artifact_name = _parse_slice(name)
        context = _names(artifact_name, pkg)
        context.update(_slice_names(slice_name))
        files = render_tree("qx.cli.scaffolds", "command_vs", root, context, overwrite=force)
    else:
        context = _names(name, pkg)
        context["aggregate"] = _names(aggregate_for, pkg) if aggregate_for else None
        files = render_tree("qx.cli.scaffolds", "command", root, context, overwrite=force)
    console.rule(f"[bold green]command {context['name_pascal']} generated[/bold green]")
    preview_tree(files, root)


@app.command()
def query(
    name: str = typer.Argument(
        ..., help="Query name. Use 'slice/Name' for vertical-slice services."
    ),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate a Query class and its handler.

    Layered:  qx generate query GetUser
    VS:       qx generate query user/GetUser
    """
    root, pkg = _service_package()
    if "/" in name:
        slice_name, artifact_name = _parse_slice(name)
        context = _names(artifact_name, pkg)
        context.update(_slice_names(slice_name))
        files = render_tree("qx.cli.scaffolds", "query_vs", root, context, overwrite=force)
    else:
        context = _names(name, pkg)
        files = render_tree("qx.cli.scaffolds", "query", root, context, overwrite=force)
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


@app.command(name="slice")
def generate_slice(
    name: str = typer.Argument(..., help="Slice name (e.g. 'payment', 'user')."),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Generate a full CRUD vertical slice.

    Creates entity, commands (Create/Update/Delete), queries (Get/List),
    SQLAlchemy mapping + repository, and a FastAPI router with all five endpoints.

    After generating:

      1. Mount the router in main.py:
             from <service>.presentation.<slice> import router as <slice>_router
             app.include_router(<slice>_router, prefix="/v1")

      2. Create the Alembic migration:
             uv run alembic revision --autogenerate -m "create_<slice>"
             uv run alembic upgrade head
    """
    root, pkg = _service_package()
    context = _slice_names(name)
    context["service_pkg"] = pkg
    sn = context["slice_name_snake"]
    # Combined names used in generated file names (avoids triple-underscore clash).
    context["create_slice_name_snake"] = f"create_{sn}"
    context["update_slice_name_snake"] = f"update_{sn}"
    context["delete_slice_name_snake"] = f"delete_{sn}"
    context["get_slice_name_snake"] = f"get_{sn}"
    context["list_slice_name_snakes"] = f"list_{sn}s"
    files = render_tree("qx.cli.scaffolds", "slice", root, context, overwrite=force)
    console.rule(f"[bold green]slice {context['slice_name_pascal']} generated[/bold green]")
    preview_tree(files, root)
    console.print(
        f"\n[bold]Next steps:[/bold]\n"
        f"  1. Mount the router in [cyan]main.py[/cyan]:\n"
        f"       from {pkg}.presentation.{sn} import router as {sn}_router\n"
        f"       app.include_router({sn}_router, prefix=\"/v1\")\n\n"
        f"  2. Run the Alembic migration:\n"
        f"       uv run alembic revision --autogenerate -m \"create_{sn}\"\n"
        f"       uv run alembic upgrade head\n"
    )


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


def _slice_names(slice_name: str) -> dict[str, Any]:
    """Build naming variants for a slice."""
    snake = _snake(slice_name)
    return {
        "slice_name_snake": snake,
        "slice_name_pascal": _pascal(slice_name),
        "slice_name_kebab": _kebab(slice_name),
    }


def _parse_slice(name: str) -> tuple[str, str]:
    """Split 'user/CreateUser' → ('user', 'CreateUser')."""
    parts = name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise typer.BadParameter(
            f"Invalid format '{name}'. Use '<slice>/<Name>' (e.g. 'user/CreateUser')."
        )
    return parts[0], parts[1]


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
