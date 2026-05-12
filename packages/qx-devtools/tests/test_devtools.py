"""qx-devtools tests — config writers produce expected files."""

from __future__ import annotations

from pathlib import Path

from qx.devtools import (
    EDITORCONFIG,
    MYPY_CONFIG,
    PRE_COMMIT_CONFIG,
    RUFF_CONFIG,
    write_configs,
)


def test_constants_present() -> None:
    assert "line-length" in RUFF_CONFIG
    assert "python_version" in MYPY_CONFIG
    assert "pre-commit" in PRE_COMMIT_CONFIG or "ruff" in PRE_COMMIT_CONFIG
    assert "root = true" in EDITORCONFIG


def test_write_configs_creates_all_four(tmp_path: Path) -> None:
    written = write_configs(tmp_path)
    names = {p.name for p in written}
    assert names == {"ruff.toml", "mypy.ini", ".pre-commit-config.yaml", ".editorconfig"}
    for p in written:
        assert p.read_text().strip()


def test_write_configs_respects_existing(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("# user-customized")
    written = write_configs(tmp_path)
    assert not any(p.name == "ruff.toml" for p in written)
    assert (tmp_path / "mypy.ini").exists()


def test_write_configs_overwrite_flag(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("# user-customized")
    written = write_configs(tmp_path, overwrite=True)
    assert any(p.name == "ruff.toml" for p in written)
    assert "line-length" in (tmp_path / "ruff.toml").read_text()


def test_write_configs_selective(tmp_path: Path) -> None:
    written = write_configs(
        tmp_path, ruff=True, mypy=False, pre_commit=False, editorconfig=False
    )
    assert {p.name for p in written} == {"ruff.toml"}
