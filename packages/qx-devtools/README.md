# qx-devtools

Shared code-quality configuration presets for Qx services — ruff, mypy, pre-commit, and editorconfig in one place.

## What lives here

- **`qx.devtools.RUFF_CONFIG`** — string constant with the standard ruff configuration (line length 100, full lint rule set, per-file ignores for tests and Alembic).
- **`qx.devtools.MYPY_CONFIG`** — string constant with strict mypy configuration (`strict = True`, pydantic plugin, test/alembic exclusions).
- **`qx.devtools.PRE_COMMIT_CONFIG`** — string constant with a pre-commit config that runs ruff, ruff-format, mypy, and standard file-hygiene hooks.
- **`qx.devtools.EDITORCONFIG`** — string constant with the standard `.editorconfig` (UTF-8, LF, 4-space indent).
- **`qx.devtools.write_configs`** — writes any combination of the above files into a target directory. Skips existing files unless `overwrite=True`.

## Usage

Write the standard config files into a new service's root directory:

```python
from pathlib import Path
from qx.devtools import write_configs

written = write_configs(Path("my-service/"), ruff=True, mypy=True, pre_commit=True)
print(f"Written: {[str(p) for p in written]}")
```

Or call it from the CLI during service scaffolding:

```bash
uv run python -c "from qx.devtools import write_configs; from pathlib import Path; write_configs(Path('.'))"
```

## Design rules

- The config strings are plain text constants so services can read, extend, or compose from them without parsing TOML/YAML.
- `write_configs` is intentionally conservative: it never overwrites by default. Run with `overwrite=True` only when you want to pull in updated framework defaults.
- Services that want to diverge from a specific rule should do so in their own config file rather than forking the preset — extend, don't override wholesale.
