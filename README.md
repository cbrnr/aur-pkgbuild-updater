# aur-pkgbuild-updater

Automatically checks all AUR packages of a given maintainer for upstream updates, build-verifies the proposed change in an Arch Linux container, and opens a pull request that is pushed to AUR on merge. This repository tracks [the packages maintained by cbrnr](https://aur.archlinux.org/packages?SeB=m&K=cbrnr), but it is a [template repository](#use-this-for-your-own-packages) — any AUR maintainer can run their own instance by changing a single line of configuration.

## How it works

A GitHub Actions workflow (`check-updates.yml`) runs hourly at minute 43 and can also be triggered manually via `workflow_dispatch`. The `detect` job:

1. Fetches the live package list from the AUR maintainer API.
2. Looks up the latest upstream version (PyPI, GitHub Releases, SourceForge RSS, website scraping, or SVN revision, depending on the package).
3. Compares the upstream version against the current AUR `pkgver`.
4. For each outdated package, renders updated `pkgs/{pkgname}/PKGBUILD` and `pkgs/{pkgname}/.SRCINFO` (new `pkgver`, `pkgrel=1`, refreshed source URL and checksums) onto a branch `update/{pkgname}/{pkgver}` and pushes it.

A `build-verify` job then runs for each pushed branch in an `archlinux:base-devel` container and builds the package with `paru -Bi`, which resolves both repo and AUR dependencies. Depending on the outcome, the `finalize` command:

- **Build succeeded** — opens a pull request. Merging it triggers `push-to-aur.yml`, which clones the AUR repository over SSH, copies the files from `pkgs/{pkgname}/`, and pushes.
- **Build failed** — opens a "Build failed: Update X to Y" issue with the proposed diff and the tail of the build log attached, and deletes the branch. No PR is opened.

Packages marked `skip_build_check` bypass CI entirely and get a plain issue with the diff and the `makepkg -sri` commands to apply the update by hand.

Issues close themselves once resolved: an update issue when the package is current on AUR again, a build-failure issue when a later build succeeds or the package is updated manually.

## Configuration

`packages.toml` holds the AUR username to track and maps each package name to its upstream source. The package list itself comes from the AUR API, so:

- **New package**: picked up automatically on the next run. If no entry exists in `packages.toml` yet, an issue is opened asking to add one.
- **Removed/transferred package**: disappears from the AUR API response; the stale `packages.toml` entry and the corresponding `pkgs/{pkgname}/` directory are removed automatically and committed back.

Instance settings (top-level keys, before the first `[section]`):

| Key | Description |
|-----|-------------|
| `maintainer` | AUR username whose packages are tracked. Overridable with `--maintainer`. |

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
| `checksum_regex` | Regex (with one capture group, matched with `re.DOTALL`) to extract a SHA256 hex string from the same `url`. Required for non-PyPI sources. |
| `source_url_template` | Binary source URL template; `{raw_version}` is the version string before any `version_sub` transforms. Required for non-PyPI sources. |
| `skip_build_check` | If `true`, skip the CI build and open a plain issue with manual instructions instead. Only for packages that cannot be built in CI for reasons unrelated to correctness (interactive licensing, excessive build time). |

## Setup

The `push-to-aur` workflow needs SSH access to AUR. Add the following repository secret (Settings → Secrets and variables → Actions):

| Secret | Value |
|--------|-------|
| `AUR_SSH_KEY` | Private half of an Ed25519 keypair whose public key is registered on the AUR account. |

Also enable **"Allow GitHub Actions to create and approve pull requests"** under Settings → Actions → General → Workflow permissions.

## Use this for your own packages

1. Click **Use this template** on GitHub to create your own repository, e.g. `alice/aur-updates`.
2. Enable **Actions** in the new repository, and tick **"Allow GitHub Actions to create and approve pull requests"** under Settings → Actions → General → Workflow permissions.
3. Create an SSH key (`ssh-keygen -t ed25519`), register the public half on your AUR account under My Account → SSH Public Key (AUR accepts several keys), and add the private half as the repository secret `AUR_SSH_KEY`.
4. Set `maintainer` at the top of `packages.toml` to your **AUR username** (often not the same as your GitHub username). The inherited package entries can stay — the first run removes every package you do not maintain, together with its `pkgs/` directory. To skip the cleanup commit, delete the `[sections]` and `pkgs/*` yourself.
5. Run the workflow once manually: Actions → *Check AUR Package Updates* → *Run workflow*. It opens an **"Add upstream config for X"** issue for every package of yours that has no `packages.toml` entry.
6. Work through those issues by adding entries using the source-type tables above. The issues close automatically once the package is configured and current.
7. From then on the hourly schedule takes over. Review the pull requests it opens; merging one pushes `PKGBUILD` and `.SRCINFO` to AUR.

Setting `maintainer` is the only *required* change. The following are optional, but worth a look:

| Where | What | Why |
|-------|------|-----|
| `.github/workflows/check-updates.yml` | The **cron minute** (`43 * * * *`) | Every instance created from this template inherits the same schedule. Pick your own minute so they don't all hit the AUR RPC at once. Drop to a few times a day (e.g. `'17 */6 * * *'`) if hourly is more than you need. |
| `.github/workflows/push-to-aur.yml` | The `branches:` filter (`main`) | Workflow event filters cannot use expressions, so this is a literal. Change it if your default branch is not `main`. |
| `.github/workflows/check-updates.yml` | `max-parallel: 3` and `timeout-minutes: 30` | How many packages are build-verified at once, and how long a single build may take. Raise the timeout for slow-building packages. The builds themselves run on GitHub runners, but every job clones `paru-bin` from the AUR and lets `paru` clone and RPC-query each AUR dependency, so `max-parallel` also caps how much git/RPC traffic is aimed at the AUR at once. |

Two more things to be aware of:

- GitHub **disables scheduled workflows in repositories with no activity for 60 days**. Merging update PRs normally counts, but a quiet instance needs an occasional manual run.
- The workflows run on the free tier for public repositories. The Arch container build step is the expensive part, so a private instance with many packages can consume a noticeable share of the included Actions minutes.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run python check_updates.py detect --dry-run
```

`--dry-run` prints all proposed actions to stdout without creating or closing any GitHub issues, pushing branches, opening PRs, or committing changes to `packages.toml`. Pass `--maintainer NAME` to check someone else's packages without editing `packages.toml`.
