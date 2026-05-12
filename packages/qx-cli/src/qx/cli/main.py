"""Qx CLI — scaffolding and developer tooling.

Subcommands::

    qx new service NAME            # scaffold a new service
    qx generate aggregate NAME     # add an aggregate to current service
    qx generate command NAME       # add a command + handler
    qx generate query NAME         # add a query + handler
    qx generate endpoint           # add an HTTP endpoint
    qx generate event NAME         # add an integration event
    qx dev up                      # start docker-compose stack
    qx dev down                    # stop the stack
    qx version                     # print framework version

Invoke as ``qx`` once the package is installed; ``uv run qx ...``
during development.
"""

from __future__ import annotations

import typer
from qx.cli.commands import dev, generate, new
from rich.console import Console

console = Console()

app = typer.Typer(
    name="qx",
    help="Qx framework CLI — scaffolding, code generation, and dev orchestration.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(new.app, name="new", help="Scaffold a new project from a template.")
app.add_typer(
    generate.app,
    name="generate",
    help="Generate code into an existing service (aggregate, command, query, ...).",
)
app.add_typer(dev.app, name="dev", help="Local development orchestration.")


@app.command()
def version() -> None:
    """Print the framework version."""
    from qx.core import __version__ as core_version  # noqa: PLC0415

    console.print(f"[bold]qx-python[/bold] [cyan]{core_version}[/cyan]")


if __name__ == "__main__":
    app()
