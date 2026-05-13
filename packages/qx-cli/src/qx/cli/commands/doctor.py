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

_TOOL_FIXES: dict[str, str] = {
    "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "git": "brew install git  # macOS\n# or: apt install git",
    "docker": "# Install Docker Desktop:\nhttps://www.docker.com/products/docker-desktop/",
}


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


def _check_python(failures: list[str], fix_commands: list[str]) -> None:
    major, minor, micro = sys.version_info[:3]
    ver_str = f"{major}.{minor}.{micro}"
    console.print("\n[bold]Python[/bold]")
    if (major, minor) >= (3, 14):
        console.print(_ok(f"Python {ver_str}"))
    else:
        msg = f"Python {ver_str} — need ≥ 3.14"
        console.print(_fail(msg))
        failures.append(msg)
        fix_commands.append("# Install Python 3.14 via uv:\nuv python install 3.14")


def _check_tools(failures: list[str], fix_commands: list[str]) -> None:
    console.print("\n[bold]Tools[/bold]")
    for tool in ("uv", "git", "docker", "ruff", "mypy"):
        path = shutil.which(tool)
        if path:
            try:
                out = subprocess.run(
                    [tool, "--version"], capture_output=True, text=True, timeout=5, check=False
                )
                ver = (out.stdout or out.stderr).strip().splitlines()[0]
                console.print(_ok(f"{tool}  [dim]{ver}[/dim]"))
            except Exception:
                console.print(_ok(f"{tool}  [dim]{path}[/dim]"))
        elif tool in _TOOL_FIXES:
            msg = f"{tool} not found on PATH"
            console.print(_fail(msg))
            failures.append(msg)
            fix_commands.append(f"# Install {tool}:\n{_TOOL_FIXES[tool]}")
        else:
            console.print(_warn(f"{tool} not found (optional for dev)"))

    if shutil.which("docker"):
        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, text=True, timeout=5, check=False
            )
            if result.returncode == 0:
                console.print(_ok("Docker daemon running"))
            else:
                console.print(_warn("Docker daemon not running (run: docker desktop start)"))
        except Exception:
            console.print(_warn("Docker daemon check timed out"))


def _check_packages() -> None:
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


def _check_env() -> None:
    import os  # noqa: PLC0415

    console.print("\n[bold]Environment variables[/bold]")
    env_vars = {
        "DATABASE_URL": True,
        "REDIS_URL": False,
        "NATS_URL": False,
        "SECRET_KEY": False,
        "OTEL_EXPORTER_OTLP_ENDPOINT": False,
    }
    for var, required in env_vars.items():
        val = os.getenv(var)
        if val:
            display = "***" if "SECRET" in var else val
            console.print(_ok(f"{var}  [dim]{display}[/dim]"))
        elif required:
            console.print(_warn(f"{var} not set"))
        else:
            console.print(f"   [dim]{var} not set (optional)[/dim]")


def _check_connectivity() -> None:
    import os  # noqa: PLC0415

    console.print("\n[bold]Connectivity[/bold]")
    defaults = {
        "DATABASE_URL": "postgresql://localhost:5432",
        "REDIS_URL": "redis://localhost:6379",
        "NATS_URL": "nats://localhost:4222",
    }
    default_ports = {"postgres": 5432, "redis": 6379, "nats": 4222}
    labels = {"DATABASE_URL": "Postgres", "REDIS_URL": "Redis", "NATS_URL": "NATS"}

    for env_var, label in labels.items():
        raw = os.getenv(env_var, defaults[env_var])
        try:
            parsed = urlparse(raw)
            host = parsed.hostname or "localhost"
            key = label.lower()
            port = parsed.port or next(v for k, v in default_ports.items() if k in key)
        except Exception:
            continue
        if _tcp_reachable(host, port):
            console.print(_ok(f"{label}  {host}:{port}"))
        else:
            console.print(
                _warn(f"{label}  {host}:{port} unreachable (is the stack running? qx dev up)")
            )


_MIN_VERSIONS: dict[str, str] = {
    "qx-core": "0.2.0",
    "qx-cqrs": "0.2.0",
    "qx-db": "0.2.0",
    "qx-http": "0.2.0",
    "qx-events": "0.2.0",
    "qx-worker": "0.2.0",
    "qx-cli": "0.2.0",
}


def _check_version_mismatches() -> None:
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    from packaging.version import Version  # noqa: PLC0415

    console.print("\n[bold]Version requirements[/bold]")
    found_mismatch = False
    for pkg, min_ver in _MIN_VERSIONS.items():
        try:
            installed = version(pkg)
            if Version(installed) < Version(min_ver):
                console.print(_fail(f"{pkg} {installed} < {min_ver} (minimum required)"))
                found_mismatch = True
            else:
                console.print(_ok(f"{pkg} {installed} >= {min_ver}"))
        except PackageNotFoundError:
            pass  # already reported in _check_packages

    if not found_mismatch:
        console.print(_ok("All installed qx packages meet minimum version requirements"))


@app.callback(invoke_without_command=True)
def doctor(
    no_connectivity: bool = typer.Option(
        False,
        "--no-connectivity",
        help="Skip Postgres / Redis / NATS TCP reachability probes.",
    ),
    fix: bool = typer.Option(
        False, "--fix", help="Print shell commands to resolve detected issues."
    ),
) -> None:
    """Check that your development environment is correctly set up."""
    failures: list[str] = []
    fix_commands: list[str] = []

    console.rule("[bold]qx doctor[/bold]")
    _check_python(failures, fix_commands)
    _check_tools(failures, fix_commands)
    _check_packages()
    _check_env()
    _check_version_mismatches()

    if not no_connectivity:
        _check_connectivity()

    console.print()
    if failures:
        console.rule(f"[red]{len(failures)} issue(s) found[/red]")
        if fix and fix_commands:
            console.print("\n[bold]Suggested fixes:[/bold]\n")
            for cmd in fix_commands:
                console.print(f"[dim]{cmd}[/dim]\n")
        else:
            console.print(
                "[dim]Run [bold]qx doctor --fix[/bold] for suggested remediation commands.[/dim]"
            )
        raise typer.Exit(code=1)
    else:
        console.rule("[green]All checks passed[/green]")
