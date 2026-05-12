"""``qx dev`` — local development orchestration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(no_args_is_help=True)
console = Console()


def _find_compose_file() -> Path:
    """Locate the framework's docker-compose.yaml.

    Searches: CWD, then walks up looking for ``deploy/docker-compose.yaml``.
    """
    cwd = Path.cwd()
    candidates = [
        cwd / "deploy" / "docker-compose.yaml",
        cwd / "docker-compose.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Walk up
    for parent in cwd.parents:
        c = parent / "deploy" / "docker-compose.yaml"
        if c.exists():
            return c
    raise typer.BadParameter(
        "Could not find a docker-compose.yaml. Run from a qx project tree."
    )


def _docker_compose_cmd() -> list[str]:
    """Pick whichever compose flavor is available."""
    if shutil.which("docker"):
        return ["docker", "compose"]
    if shutil.which("podman"):
        return ["podman-compose"]
    raise typer.BadParameter("Neither docker nor podman found on PATH.")


@app.command()
def up(
    detach: bool = typer.Option(True, "--detach/--no-detach", "-d/-n"),
) -> None:
    """Start the local infrastructure stack."""
    compose = _find_compose_file()
    cmd = [*_docker_compose_cmd(), "-f", str(compose), "up"]
    if detach:
        cmd.append("-d")
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, check=False)


@app.command()
def down() -> None:
    """Stop the local infrastructure stack."""
    compose = _find_compose_file()
    cmd = [*_docker_compose_cmd(), "-f", str(compose), "down"]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, check=False)


@app.command()
def logs(
    service: str = typer.Argument("", help="Specific service to tail (omit for all)."),
) -> None:
    """Tail logs from the local stack."""
    compose = _find_compose_file()
    cmd = [*_docker_compose_cmd(), "-f", str(compose), "logs", "-f"]
    if service:
        cmd.append(service)
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, check=False)


@app.command()
def status() -> None:
    """Show the running containers."""
    compose = _find_compose_file()
    cmd = [*_docker_compose_cmd(), "-f", str(compose), "ps"]
    subprocess.run(cmd, check=False)
