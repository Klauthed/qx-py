"""Jinja2 template rendering helpers.

Templates live in ``qx.cli.scaffolds`` as text files and get rendered
with a small context. The conventions:

- A template file's path under ``scaffolds/`` (with ``.j2`` stripped from the
  end) becomes its output path under the target directory.
- ``__name__`` substitutions are done in path components for paths that
  vary per generation (e.g., command name, aggregate name).
- The Jinja2 environment is sandboxed (no filesystem loaders into arbitrary
  paths, no exec filters).

The rendering loop intentionally keeps it boring: walk a template tree,
render each file, write to disk. No conditional file inclusion based on
template flags — if a scaffold needs variations, split it into multiple
templates and have the command pick one.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined
from rich.console import Console
from rich.tree import Tree

__all__ = ["preview_tree", "render_tree"]

console = Console()


def _make_env() -> Environment:
    env = Environment(
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        block_start_string="{%",
        block_end_string="%}",
        variable_start_string="{{",
        variable_end_string="}}",
    )
    env.filters["snake"] = _snake_case
    env.filters["pascal"] = _pascal_case
    env.filters["kebab"] = _kebab_case
    return env


def _snake_case(s: str) -> str:
    import re  # noqa: PLC0415

    s = re.sub(r"[\s\-]+", "_", s.strip())
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _pascal_case(s: str) -> str:
    return "".join(p.capitalize() for p in _snake_case(s).split("_"))


def _kebab_case(s: str) -> str:
    return _snake_case(s).replace("_", "-")


def render_tree(
    template_pkg: str,
    template_root: str,
    dest: Path,
    context: dict[str, Any],
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Render every template under ``template_pkg/template_root`` into ``dest``.

    ``template_pkg`` is a dotted Python package containing the templates
    (e.g., ``qx.cli.scaffolds.service``). Templates are discovered via
    ``importlib.resources`` so they work after install (no filesystem
    assumptions).

    Returns the list of files written.
    """
    env = _make_env()
    written: list[Path] = []

    source_root = resources.files(template_pkg)
    if template_root:
        source_root = source_root / template_root

    for entry in _walk(source_root):
        rel = entry["rel"]
        # Skip the empty __init__.py markers we use to make scaffold trees
        # importable via importlib.resources — they're package metadata, not content.
        basename = rel.rsplit("/", 1)[-1]
        if basename == "__init__.py" and not entry["text"].strip():
            continue
        text = entry["text"]
        # Substitute path components: __thing__ in filenames → context["thing"].
        out_rel = _substitute_path(rel, context)
        # Drop trailing .j2 from file names so output looks natural.
        if out_rel.endswith(".j2"):
            out_rel = out_rel[:-3]
        out_path = dest / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not overwrite:
            console.print(f"[yellow]skip[/yellow]   {out_path}")
            continue
        template = env.from_string(text)
        rendered = template.render(**context)
        out_path.write_text(rendered, encoding="utf-8")
        written.append(out_path)
        console.print(f"[green]write[/green]  {out_path}")
    return written


def _walk(source_root: Any) -> list[dict[str, Any]]:
    """Recursively gather template files from a resources Traversable."""
    out: list[dict[str, Any]] = []

    def visit(node: Any, rel: str) -> None:
        if node.is_file():
            out.append({"rel": rel, "text": node.read_text(encoding="utf-8")})
            return
        for child in node.iterdir():
            visit(child, f"{rel}/{child.name}" if rel else child.name)

    visit(source_root, "")
    return out


def _substitute_path(path: str, context: dict[str, Any]) -> str:
    """Replace ``__key__`` occurrences in path with ``context[key]``."""
    import re  # noqa: PLC0415

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in context:
            return m.group(0)
        return str(context[key])

    return re.sub(r"__([a-zA-Z_][a-zA-Z0-9_]*)__", repl, path)


def preview_tree(files: list[Path], dest: Path) -> None:
    """Render a Rich tree showing what was generated."""
    tree = Tree(f"[bold]{dest}[/bold]")
    rels = sorted(p.relative_to(dest) for p in files)
    nodes: dict[Path, Any] = {Path(): tree}
    for rel in rels:
        accumulated = Path()
        parent_node = tree
        for part in rel.parts:
            accumulated = accumulated / part
            if accumulated in nodes:
                parent_node = nodes[accumulated]
                continue
            node = parent_node.add(part)
            nodes[accumulated] = node
            parent_node = node
    console.print(tree)
