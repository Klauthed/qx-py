"""``qx dev`` — local development orchestration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(no_args_is_help=True)
console = Console()


def _find_compose_files() -> list[Path]:
    """Locate the base compose file and any service-level override.

    Returns a list of ``-f <path>`` values — base first, override second
    (if present). Docker Compose merges them in order.

    Search order for the base file:
      1. ``./deploy/docker-compose.yaml``
      2. ``./docker-compose.yaml``
      3. Walk up looking for ``deploy/docker-compose.yaml``

    A ``docker-compose.override.yaml`` in CWD is automatically appended when
    found, allowing per-service extensions of the shared stack.
    """
    cwd = Path.cwd()
    base: Path | None = None

    candidates = [
        cwd / "deploy" / "docker-compose.yaml",
        cwd / "docker-compose.yaml",
    ]
    for c in candidates:
        if c.exists():
            base = c
            break

    if base is None:
        for parent in cwd.parents:
            c = parent / "deploy" / "docker-compose.yaml"
            if c.exists():
                base = c
                break

    if base is None:
        raise typer.BadParameter(
            "Could not find a docker-compose.yaml. Run from a qx project tree."
        )

    files = [base]
    override = cwd / "docker-compose.override.yaml"
    if override.exists():
        files.append(override)
        console.print(f"[dim]using override: {override}[/dim]")

    return files


def _docker_compose_cmd() -> list[str]:
    """Pick whichever compose flavor is available."""
    if shutil.which("docker"):
        return ["docker", "compose"]
    if shutil.which("podman"):
        return ["podman-compose"]
    raise typer.BadParameter("Neither docker nor podman found on PATH.")


def _compose_file_flags() -> list[str]:
    """Build the ``-f file [-f override]`` flags for all compose subcommands."""
    flags: list[str] = []
    for f in _find_compose_files():
        flags += ["-f", str(f)]
    return flags


@app.command()
def up(
    detach: bool = typer.Option(True, "--detach/--no-detach", "-d/-n"),
    service: str = typer.Argument("", help="Bring up a specific service only (omit for all)."),
) -> None:
    """Start the local infrastructure stack (+ service override if present)."""
    cmd = [*_docker_compose_cmd(), *_compose_file_flags(), "up"]
    if detach:
        cmd.append("-d")
    if service:
        cmd.append(service)
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, check=False)


@app.command()
def down(
    volumes: bool = typer.Option(False, "--volumes", "-v", help="Also remove volumes."),
) -> None:
    """Stop the local infrastructure stack."""
    cmd = [*_docker_compose_cmd(), *_compose_file_flags(), "down"]
    if volumes:
        cmd.append("-v")
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, check=False)


@app.command()
def logs(
    service: str = typer.Argument("", help="Specific service to tail (omit for all)."),
    tail: int = typer.Option(100, "--tail", "-n", help="Number of lines to show from the end."),
) -> None:
    """Tail logs from the local stack."""
    cmd = [*_docker_compose_cmd(), *_compose_file_flags(), "logs", "-f", "--tail", str(tail)]
    if service:
        cmd.append(service)
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, check=False)


@app.command()
def status() -> None:
    """Show the running containers."""
    cmd = [*_docker_compose_cmd(), *_compose_file_flags(), "ps"]
    subprocess.run(cmd, check=False)


@app.command()
def restart(
    service: str = typer.Argument(..., help="Service name to restart."),
) -> None:
    """Restart a specific container in the stack."""
    cmd = [*_docker_compose_cmd(), *_compose_file_flags(), "restart", service]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, check=False)
