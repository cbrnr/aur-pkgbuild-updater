# aur-pkgbuild-updater

Automatically checks all [AUR packages maintained by cbrnr](https://aur.archlinux.org/packages?SeB=m&K=cbrnr) for upstream updates and opens a GitHub issue for each outdated package.

## How it works

A GitHub Actions workflow runs daily at 06:00 UTC (and can be triggered manually via `workflow_dispatch`). For each maintained package it:

1. Fetches the live package list from the AUR maintainer API.
2. Looks up the latest upstream version (PyPI, GitHub Releases, SourceForge RSS, website scraping, or SVN revision, depending on the package).
3. Compares the upstream version against the current AUR `pkgver`.
4. If outdated, opens a GitHub issue with:
   - links to the AUR page and upstream project
   - a unified diff of the proposed `PKGBUILD` changes
   - the exact shell commands needed to apply the update and push to AUR
5. On every run, issues for packages that are now up to date are automatically closed.

## Configuration

`packages.toml` maps each AUR package name to its upstream source. The package list itself comes from the AUR API, so:

- **New package**: picked up automatically on the next run. If no entry exists in `packages.toml` yet, an issue is opened asking to manually add one.
- **Removed/transferred package**: disappears from the AUR API response; the stale `packages.toml` entry is removed automatically and committed back.

Supported source types:

| Source        | Required fields                                            |
|---------------|------------------------------------------------------------|
| `pypi`        | `pypi_name`                                                |
| `github`      | `github` (`"owner/repo"`)                                  |
| `sourceforge` | `sourceforge_project`, `sourceforge_path`, `version_regex` |
| `website`     | `url`, `version_regex`, optionally `version_compact`       |
| `svn`         | `svn_url`                                                  |

Any source can also set `upstream_url` to override the link shown in the issue.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run python check_updates.py --dry-run
```

`--dry-run` prints all proposed actions to stdout without creating or closing any GitHub issues and without committing changes to `packages.toml`.
