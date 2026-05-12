"""CLI tests — exercise the scaffold rendering."""

from __future__ import annotations

import ast
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from qx.cli.main import app
from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "qx-python" in result.stdout


def test_new_service_renders_valid_python(tmp_path: Path) -> None:
    """Scaffolding a service should produce files that parse as valid Python."""
    result = runner.invoke(
        app,
        ["new", "service", "demo-service", "--target", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout

    project = tmp_path / "demo-service"
    assert (project / "pyproject.toml").exists()
    assert (project / "src" / "demo_service" / "main.py").exists()
    assert (project / "Dockerfile").exists()
    assert (project / "tests" / "test_smoke.py").exists()

    # Every generated .py must parse cleanly.
    for py_file in project.rglob("*.py"):
        text = py_file.read_text()
        try:
            ast.parse(text)
        except SyntaxError as e:
            pytest.fail(f"{py_file} has invalid Python: {e}")


def test_new_service_generates_lint_clean_python(tmp_path: Path) -> None:
    """Generated Python source must pass ruff with no errors."""
    result = runner.invoke(
        app,
        ["new", "service", "demo-service", "--target", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout

    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(tmp_path / "demo-service" / "src")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---- qx generate ----


def _scaffold_service(tmp_path: Path, name: str = "my-service") -> "Path":
    result = runner.invoke(app, ["new", "service", name, "--target", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    return tmp_path / name


def test_generate_command_renders_valid_python(tmp_path: Path) -> None:
    project = _scaffold_service(tmp_path)
    result = runner.invoke(
        app,
        ["generate", "command", "CreateOrder", "--aggregate", "Order"],
        catch_exceptions=False,
    )
    # generate resolves from cwd; we need to cd into the service dir
    import os

    old = os.getcwd()
    try:
        os.chdir(project)
        result = runner.invoke(app, ["generate", "command", "CreateOrder"])
        assert result.exit_code == 0, result.stdout
        generated = project / "src" / "my_service" / "application" / "commands" / "create_order.py"
        assert generated.exists()
        ast.parse(generated.read_text())
    finally:
        os.chdir(old)


def test_generate_query_renders_valid_python(tmp_path: Path) -> None:
    project = _scaffold_service(tmp_path)
    import os

    old = os.getcwd()
    try:
        os.chdir(project)
        result = runner.invoke(app, ["generate", "query", "GetOrder"])
        assert result.exit_code == 0, result.stdout
        generated = project / "src" / "my_service" / "application" / "queries" / "get_order.py"
        assert generated.exists()
        ast.parse(generated.read_text())
    finally:
        os.chdir(old)


def test_generate_event_renders_valid_python(tmp_path: Path) -> None:
    project = _scaffold_service(tmp_path)
    import os

    old = os.getcwd()
    try:
        os.chdir(project)
        result = runner.invoke(app, ["generate", "event", "OrderPlaced"])
        assert result.exit_code == 0, result.stdout
        generated = project / "src" / "my_service" / "domain" / "events" / "order_placed.py"
        assert generated.exists()
        ast.parse(generated.read_text())
    finally:
        os.chdir(old)


def test_generate_endpoint_renders_valid_python(tmp_path: Path) -> None:
    project = _scaffold_service(tmp_path)
    import os

    old = os.getcwd()
    try:
        os.chdir(project)
        result = runner.invoke(
            app, ["generate", "endpoint", "/orders", "--handler", "CreateOrder"]
        )
        assert result.exit_code == 0, result.stdout
        generated = (
            project / "src" / "my_service" / "presentation" / "routes" / "create_order.py"
        )
        assert generated.exists()
        ast.parse(generated.read_text())
    finally:
        os.chdir(old)


def test_new_service_refuses_nonempty_dir_without_force(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "placeholder.txt").write_text("x")
    result = runner.invoke(
        app,
        ["new", "service", "demo", "--target", str(tmp_path)],
    )
    assert result.exit_code != 0


def test_doctor_runs_without_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    # Exit code may be 1 if some tools are absent in CI; the command itself must not crash.
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "qx doctor" in result.stdout


def test_generate_aggregate_renders_valid_python(tmp_path: Path) -> None:
    project = _scaffold_service(tmp_path)
    import os

    old = os.getcwd()
    try:
        os.chdir(project)
        result = runner.invoke(app, ["generate", "aggregate", "Invoice"])
        assert result.exit_code == 0, result.stdout

        agg = project / "src" / "my_service" / "domain" / "aggregates" / "invoice" / "__init__.py"
        repo = (
            project
            / "src"
            / "my_service"
            / "infrastructure"
            / "persistence"
            / "invoice"
            / "repository.py"
        )
        migration = project / "alembic" / "versions" / "create_invoice.py"

        assert agg.exists(), "aggregate __init__.py missing"
        assert repo.exists(), "repository.py missing"
        assert migration.exists(), "migration stub missing"

        for f in (agg, repo, migration):
            ast.parse(f.read_text())
    finally:
        os.chdir(old)
