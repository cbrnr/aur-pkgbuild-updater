# aur-pkgbuild-updater

Automatically checks all [AUR packages maintained by cbrnr](https://aur.archlinux.org/packages?SeB=m&K=cbrnr) for upstream updates. Depending on the package it either opens a GitHub issue or creates a pull request with updated `PKGBUILD` and `.SRCINFO` files that is automatically pushed to AUR on merge.

## How it works

A GitHub Actions workflow runs daily at 06:00 UTC (and can be triggered manually via `workflow_dispatch`). For each maintained package it:

1. Fetches the live package list from the AUR maintainer API.
2. Looks up the latest upstream version (PyPI, GitHub Releases, SourceForge RSS, website scraping, or SVN revision, depending on the package).
3. Compares the upstream version against the current AUR `pkgver`.
4. If outdated:
   - **Default** — opens a GitHub issue with links to the AUR page and upstream project, a unified diff of the proposed `PKGBUILD` changes, and the exact shell commands needed to apply the update and push to AUR manually.
   - **`auto_push = true`** — creates a branch `update/{pkgname}/{pkgver}`, commits updated `pkgs/{pkgname}/PKGBUILD` and `pkgs/{pkgname}/.SRCINFO`, and opens a pull request. Merging the PR automatically triggers the `push-to-aur` workflow (see below).
5. On every run, issues for packages that are now up to date are automatically closed.

A second workflow (`push-to-aur.yml`) triggers whenever a PR whose branch starts with `update/` is merged into `main`. It clones the AUR repository over SSH, copies the updated files from `pkgs/{pkgname}/`, commits, and pushes.

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

Optional fields (any source):

| Field | Description |
|-------|-------------|
| `upstream_url` | Overrides the link shown in issues/PRs. |
| `version_sub` | List of `["pattern", "replacement"]` pairs applied via `re.sub` to transform the raw captured version into a valid `pkgver` (e.g. `[["-", "."]]` turns `"2025.05.0-496"` into `"2025.05.0.496"`). |
| `checksum_regex` | Regex (with one capture group, matched with `re.DOTALL`) to extract a SHA256 hex string from the same `url`. |
| `source_url_template` | Binary source URL template; `{raw_version}` is the version string before any `version_sub` transforms. |
| `auto_push` | If `true`, open a PR instead of an issue (requires `checksum_regex` and `source_url_template`). |

## Setup (for `auto_push` packages)

The `push-to-aur` workflow needs SSH access to AUR. Add the following repository secret (Settings → Secrets and variables → Actions):

| Secret | Value |
|--------|-------|
| `AUR_SSH_KEY` | Private half of an Ed25519 keypair whose public key is registered on the AUR account. |

Also enable **"Allow GitHub Actions to create and approve pull requests"** under Settings → Actions → General → Workflow permissions.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run python check_updates.py --dry-run
```

`--dry-run` prints all proposed actions to stdout without creating or closing any GitHub issues, opening PRs, or committing changes to `packages.toml`.
