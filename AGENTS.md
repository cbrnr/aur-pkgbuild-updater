# AGENTS.md

Guidelines for AI coding agents working on this repository.

## What this repository is

A single script ([check_updates.py](check_updates.py)) plus two GitHub Actions workflows that
keep a maintainer's AUR packages in sync with their upstream releases. See
[README.md](README.md) for the full flow.

It is also a **GitHub template repository**: other AUR maintainers create their own instance
from it. That has one consequence for every change you make — nothing specific to a single
maintainer, package, or account may be hardcoded in the script or the workflows. Such values
belong in `packages.toml`:

- Instance settings are top-level scalar keys (currently just `maintainer`). `load_config`
  returns them separately from the package sections, so a new setting must not be added as a
  `[section]`.
- Per-package settings are `[sections]`. When adding a source type or an option, document it in
  both the `packages.toml` header comment and the README tables.

Anything that must stay in a workflow because GitHub does not allow expressions there (e.g. the
`branches:` filter in `push-to-aur.yml`) gets a comment saying so, and a row in the README's
"optional adjustments" table.

## Project setup

- This project uses [uv](https://docs.astral.sh/uv/) for package and environment management.
- Install dependencies with `uv sync`.
- Run the update checker with `uv run python check_updates.py detect --dry-run`.

## Code style

- Formatting is enforced by [Ruff](https://docs.astral.sh/ruff/). Run both of the following before committing:
  ```
  ruff check --select I --fix
  ruff format
  ```
- Line length is 88 characters (the default). This limit applies to all code, including docstrings.
- Docstrings follow [NumPy style](https://numpydoc.readthedocs.io/en/latest/format.html), but use standard Markdown syntax instead of reStructuredText. In particular, inline code uses single backticks (`` `x` ``), not double backticks (` ``x`` `).
- Inline comments should start with a lower-case letter and be a single sentence where possible.
