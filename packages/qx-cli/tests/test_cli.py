"""CLI tests — exercise the scaffold rendering."""

from __future__ import annotations

import ast
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


def test_new_service_refuses_nonempty_dir_without_force(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "placeholder.txt").write_text("x")
    result = runner.invoke(
        app,
        ["new", "service", "demo", "--target", str(tmp_path)],
    )
    assert result.exit_code != 0
