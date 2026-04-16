# AGENTS.md

Guidelines for AI coding agents working on this repository.

## Project setup

- This project uses [uv](https://docs.astral.sh/uv/) for package and environment management.
- Install dependencies with `uv sync`.
- Run the update checker with `uv run python check_updates.py --dry-run`.

## Code style

- Formatting is enforced by [Ruff](https://docs.astral.sh/ruff/). Run both of the following before committing:
  ```
  ruff check --select I --fix
  ruff format
  ```
- Line length is 88 characters (the default). This limit applies to all code, including docstrings.
- Docstrings follow [NumPy style](https://numpydoc.readthedocs.io/en/latest/format.html), but use standard Markdown syntax instead of reStructuredText. In particular, inline code uses single backticks (`` `x` ``), not double backticks (` ``x`` `).
- Inline comments should start with a lower-case letter and be a single sentence where possible.
