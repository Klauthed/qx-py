"""``qx doctor`` — environment health check.

Validates that all tools and services required to develop with qx are
reachable. Exits non-zero if any required component is missing.
"""

from __future__ import annotations

import importlib.metadata
import shutil
import socket
import subprocess
import sys
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()

_QX_PACKAGES = [
    "qx-core",
    "qx-di",
    "qx-cqrs",
    "qx-db",
    "qx-http",
    "qx-observability",
    "qx-events",
    "qx-worker",
    "qx-cache",
    "qx-auth",
    "qx-grpc",
    "qx-search",
    "qx-testing",
    "qx-cli",
    "qx-devtools",
]


def _ok(msg: str) -> str:
    return f"[green]✓[/green]  {msg}"


def _fail(msg: str) -> str:
    return f"[red]✗[/red]  {msg}"


def _warn(msg: str) -> str:
    return f"[yellow]![/yellow]  {msg}"


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@app.callback(invoke_without_command=True)
def doctor(
    connectivity: bool = typer.Option(
        False, "--connectivity", "-c", help="Also probe Postgres / Redis / NATS TCP ports."
    ),
    fix: bool = typer.Option(
        False, "--fix", help="Print shell commands to resolve detected issues."
    ),
) -> None:
    """Check that your development environment is correctly set up."""
    failures: list[str] = []
    fix_commands: list[str] = []

    console.rule("[bold]qx doctor[/bold]")

    # ── Python ────────────────────────────────────────────────────────────────
    console.print("\n[bold]Python[/bold]")
    major, minor = sys.version_info[:2]
    ver_str = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= (3, 14):
        console.print(_ok(f"Python {ver_str}"))
    else:
        msg = f"Python {ver_str} — need ≥ 3.14"
        console.print(_fail(msg))
        failures.append(msg)
        fix_commands.append("# Install Python 3.14 via uv:\nuv python install 3.14")

    # ── Tools ─────────────────────────────────────────────────────────────────
    console.print("\n[bold]Tools[/bold]")

    for tool in ("uv", "git", "docker", "ruff", "mypy"):
        path = shutil.which(tool)
        if path:
            try:
                out = subprocess.run(
                    [tool, "--version"], capture_output=True, text=True, timeout=5
                )
                ver = (out.stdout or out.stderr).strip().splitlines()[0]
                console.print(_ok(f"{tool}  [dim]{ver}[/dim]"))
            except Exception:
                console.print(_ok(f"{tool}  [dim]{path}[/dim]"))
        else:
            if tool == "uv":
                msg = f"{tool} not found on PATH"
                console.print(_fail(msg))
                failures.append(msg)
                fix_commands.append(
                    "# Install uv:\ncurl -LsSf https://astral.sh/uv/install.sh | sh"
                )
            elif tool == "git":
                msg = f"{tool} not found on PATH"
                console.print(_fail(msg))
                failures.append(msg)
                fix_commands.append("# Install git:\nbrew install git  # macOS\n# or: apt install git")
            elif tool == "docker":
                msg = f"{tool} not found on PATH"
                console.print(_fail(msg))
                failures.append(msg)
                fix_commands.append(
                    "# Install Docker Desktop:\nhttps://www.docker.com/products/docker-desktop/"
                )
            else:
                console.print(_warn(f"{tool} not found (optional for dev)"))

    # Docker daemon reachable?
    if shutil.which("docker"):
        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                console.print(_ok("Docker daemon running"))
            else:
                msg = "Docker daemon not running (run: docker desktop start)"
                console.print(_warn(msg))
        except Exception:
            console.print(_warn("Docker daemon check timed out"))

    # ── qx packages ───────────────────────────────────────────────────────────
    console.print("\n[bold]qx packages[/bold]")
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Package")
    table.add_column("Version")
    table.add_column("Status")

    for pkg in _QX_PACKAGES:
        try:
            ver = importlib.metadata.version(pkg)
            table.add_row(pkg, ver, "[green]installed[/green]")
        except importlib.metadata.PackageNotFoundError:
            table.add_row(pkg, "—", "[yellow]not installed[/yellow]")

    console.print(table)

    # ── Env vars (service context) ────────────────────────────────────────────
    import os  # noqa: PLC0415

    console.print("\n[bold]Environment variables[/bold]")
    env_checks = {
        "DATABASE_URL": True,
        "REDIS_URL": False,
        "NATS_URL": False,
        "SECRET_KEY": False,
        "OTEL_EXPORTER_OTLP_ENDPOINT": False,
    }
    for var, required in env_checks.items():
        val = os.getenv(var)
        if val:
            display = val if "SECRET" not in var else "***"
            console.print(_ok(f"{var}  [dim]{display}[/dim]"))
        elif required:
            msg = f"{var} not set"
            console.print(_warn(msg))
        else:
            console.print(f"   [dim]{var} not set (optional)[/dim]")

    # ── Connectivity (opt-in) ─────────────────────────────────────────────────
    if connectivity:
        console.print("\n[bold]Connectivity[/bold]")
        probes: list[tuple[str, str, int]] = []

        db_url = os.getenv("DATABASE_URL", "postgresql://localhost:5432")
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        nats_url = os.getenv("NATS_URL", "nats://localhost:4222")

        for label, raw in [("Postgres", db_url), ("Redis", redis_url), ("NATS", nats_url)]:
            try:
                parsed = urlparse(raw)
                host = parsed.hostname or "localhost"
                port = parsed.port or (
                    5432 if "postgres" in label.lower()
                    else 6379 if "redis" in label.lower()
                    else 4222
                )
                probes.append((label, host, port))
            except Exception:
                pass

        for label, host, port in probes:
            if _tcp_reachable(host, port):
                console.print(_ok(f"{label}  {host}:{port}"))
            else:
                console.print(_warn(f"{label}  {host}:{port} unreachable (is the stack running? qx dev up)"))

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    if failures:
        console.rule(f"[red]{len(failures)} issue(s) found[/red]")
        if fix and fix_commands:
            console.print("\n[bold]Suggested fixes:[/bold]\n")
            for cmd in fix_commands:
                console.print(f"[dim]{cmd}[/dim]\n")
        elif failures:
            console.print("[dim]Run [bold]qx doctor --fix[/bold] for suggested remediation commands.[/dim]")
        raise typer.Exit(code=1)
    else:
        console.rule("[green]All checks passed[/green]")
