#!/usr/bin/env python3
"""Check AUR packages maintained by cbrnr for upstream updates.

For each outdated package a GitHub issue is opened with a proposed PKGBUILD diff. Issues
are automatically closed when the package is updated on AUR. Stale entries in
packages.toml (packages no longer maintained on AUR) are removed and the change is
committed back to the repository.

Usage:
    python check_updates.py [--dry-run] [--maintainer cbrnr]
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import quote

from packaging.version import InvalidVersion, Version

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def remove_stale_config_entries(config_path: Path, stale_keys: set[str]) -> bool:
    """Remove entire [section] blocks for stale keys from packages.toml.

    Operates on the raw text to preserve formatting and comments.
    Returns True if the file was modified.
    """
    text = config_path.read_text()
    lines = text.splitlines(keepends=True)
    result = []
    in_stale = False
    for line in lines:
        header = re.match(r"^\[([^\]]+)\]", line.strip())
        if header:
            in_stale = header.group(1) in stale_keys
        if not in_stale:
            result.append(line)
    new_text = "".join(result).rstrip("\n") + "\n"
    if new_text == text:
        return False
    config_path.write_text(new_text)
    return True


# --------------------------------------------------------------------------------------
# AUR
# --------------------------------------------------------------------------------------


def get_aur_packages(maintainer: str) -> dict[str, dict]:
    """Return {pkgname: {pkgver, pkgrel, version, url}} for all packages
    currently maintained by *maintainer* on AUR."""
    url = f"https://aur.archlinux.org/rpc/v5/search?by=maintainer&arg={maintainer}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    result = {}
    for r in data["results"]:
        ver_str = r["Version"]  # e.g. "1.12.0-2"
        pkgver, pkgrel = ver_str.rsplit("-", 1)
        result[r["Name"]] = {
            "pkgver": pkgver,
            "pkgrel": pkgrel,
            "version": ver_str,
            "url": r.get("URL") or "",
        }
    return result


# --------------------------------------------------------------------------------------
# Upstream version checkers
# --------------------------------------------------------------------------------------


def get_pypi_version(pypi_name: str) -> str:
    url = f"https://pypi.org/pypi/{pypi_name}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["info"]["version"]


def get_github_version(repo: str, token: str | None = None) -> str:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["tag_name"].lstrip("v")


def get_sourceforge_version(project: str, path: str, version_regex: str) -> str:
    encoded_path = quote(path)
    url = f"https://sourceforge.net/projects/{project}/rss?path={encoded_path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        content = resp.read()
    root = ET.fromstring(content)
    versions: list[str] = []
    for item in root.iter("item"):
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            m = re.search(version_regex, title_el.text)
            if m:
                versions.append(m.group(1))
    if not versions:
        raise ValueError(
            f"No versions matched '{version_regex}' in SourceForge RSS "
            f"for {project}{path}"
        )

    def sort_key(v: str) -> Version:
        try:
            return Version(v)
        except InvalidVersion:
            return Version("0")

    return sorted(versions, key=sort_key)[-1]


def get_website_version(
    url: str, version_regex: str, version_compact: bool = False
) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        content = resp.read().decode("utf-8", errors="replace")
    m = re.search(version_regex, content)
    if not m:
        raise ValueError(f"Version regex '{version_regex}' found no match at {url}")
    v = m.group(1)
    if version_compact:
        # e.g. "214" → "2.14"  (major = v[:-2], minor = v[-2:])
        v = str(int(v) // 100) + "." + str(int(v) % 100)
    return v


def get_svn_revision(svn_url: str) -> str:
    """Return the latest SVN revision as 'r{N}'."""
    try:
        result = subprocess.run(
            ["svn", "info", "--xml", svn_url],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        root = ET.fromstring(result.stdout)
        commit = root.find(".//commit")
        if commit is None:
            raise ValueError("No <commit> element in svn info output")
        return f"r{commit.attrib['revision']}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # HTTP fallback via SourceForge ViewVC
    sf_project = re.search(r"/p/([^/]+)/", svn_url)
    if sf_project:
        project = sf_project.group(1)
        vvc_url = f"https://sourceforge.net/p/{project}/code/HEAD/tree/"
        with urllib.request.urlopen(vvc_url, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        m = re.search(r"\[r(\d+)\]", content)
        if m:
            return f"r{m.group(1)}"
    raise ValueError(f"Could not determine SVN revision for {svn_url}")


def get_upstream_version(pkg_config: dict, github_token: str | None = None) -> str:
    source = pkg_config["source"]
    if source == "pypi":
        return get_pypi_version(pkg_config["pypi_name"])
    if source == "github":
        return get_github_version(pkg_config["github"], token=github_token)
    if source == "sourceforge":
        return get_sourceforge_version(
            pkg_config["sourceforge_project"],
            pkg_config["sourceforge_path"],
            pkg_config["version_regex"],
        )
    if source == "website":
        return get_website_version(
            pkg_config["url"],
            pkg_config["version_regex"],
            pkg_config.get("version_compact", False),
        )
    if source == "svn":
        return get_svn_revision(pkg_config["svn_url"])
    raise ValueError(f"Unknown source type: {source!r}")


# --------------------------------------------------------------------------------------
# Version comparison
# --------------------------------------------------------------------------------------


def is_outdated(aur_pkgver: str, upstream_version: str) -> bool:
    """Return True if *upstream_version* is newer than *aur_pkgver*."""
    # SVN packages use "r{N}" format
    if aur_pkgver.startswith("r") and upstream_version.startswith("r"):
        try:
            return int(upstream_version[1:]) > int(aur_pkgver[1:])
        except ValueError:
            pass
    try:
        return Version(upstream_version) > Version(aur_pkgver)
    except InvalidVersion:
        return upstream_version != aur_pkgver


# --------------------------------------------------------------------------------------
# PKGBUILD fetching
# --------------------------------------------------------------------------------------


def get_pkgbuild(pkgname: str, tmpdir: str) -> str | None:
    """Clone the AUR repo for *pkgname* into *tmpdir* and return PKGBUILD."""
    dest = os.path.join(tmpdir, pkgname)
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                f"https://aur.archlinux.org/{pkgname}.git",
                dest,
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        print(f"  [error] git clone failed: {exc.stderr.decode()[:200]}")
        return None
    pkgbuild_path = Path(dest) / "PKGBUILD"
    if not pkgbuild_path.exists():
        return None
    return pkgbuild_path.read_text()


# --------------------------------------------------------------------------------------
# SHA256
# --------------------------------------------------------------------------------------


def _source_url_from_pkgbuild(pkgbuild: str) -> str | None:
    """Extract the first plain URL from a PKGBUILD source=() line."""
    m = re.search(
        r'source(?:_[a-z0-9_]+)?\s*=\s*\(["\']?([^"\')\s]+)',
        pkgbuild,
    )
    if not m:
        return None
    url = m.group(1)
    # Strip optional "filename::" prefix
    if "::" in url:
        url = url.split("::", 1)[1]
    return url


def get_new_sha256(pkg_config: dict, new_version: str, pkgbuild: str) -> str | None:
    """Return the SHA-256 of the new upstream source, or None on failure."""
    source = pkg_config["source"]

    if source == "pypi":
        pypi_name = pkg_config["pypi_name"]
        url = f"https://pypi.org/pypi/{pypi_name}/{new_version}/json"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
            for release in data["urls"]:
                if release["packagetype"] == "sdist":
                    return release["digests"]["sha256"]
        except Exception as exc:
            print(f"  [warn] PyPI sha256 lookup failed: {exc}")
        return None

    # For other source types, derive the tarball URL from the PKGBUILD and
    # substitute the version string.
    src_url = _source_url_from_pkgbuild(pkgbuild)
    if not src_url:
        return None

    m = re.search(r"^pkgver=(.+)$", pkgbuild, re.MULTILINE)
    if not m:
        return None
    old_version = m.group(1).strip()

    new_url = src_url.replace(old_version, new_version)
    if new_url == src_url:
        # Also try with a "v" prefix on the version
        new_url = src_url.replace(f"v{old_version}", f"v{new_version}")
    if new_url == src_url:
        return None

    # Expand $pkgver / ${pkgver} that may remain after substitution
    new_url = new_url.replace("$pkgver", new_version).replace("${pkgver}", new_version)

    print(f"  Downloading tarball for sha256: {new_url}")
    try:
        sha256 = hashlib.sha256()
        req = urllib.request.Request(new_url)
        with urllib.request.urlopen(req, timeout=300) as resp:
            while chunk := resp.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as exc:
        print(f"  [warn] Could not download tarball: {exc}")
        return None


# --------------------------------------------------------------------------------------
# Diff generation
# --------------------------------------------------------------------------------------


def generate_diff(pkgbuild: str, new_version: str, new_sha256: str | None) -> str:
    """Return a unified diff of the proposed PKGBUILD changes."""
    proposed = pkgbuild
    proposed = re.sub(
        r"^(pkgver=).*", rf"\g<1>{new_version}", proposed, flags=re.MULTILINE
    )
    proposed = re.sub(r"^(pkgrel=).*", r"\g<1>1", proposed, flags=re.MULTILINE)
    if new_sha256:
        proposed = re.sub(
            r"(sha256sums(?:_[a-z0-9_]+)?\s*=\s*\()[^)]*\)",
            rf"\g<1>'{new_sha256}')",
            proposed,
            flags=re.DOTALL,
        )
    return "".join(
        difflib.unified_diff(
            pkgbuild.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile="PKGBUILD",
            tofile="PKGBUILD (proposed)",
        )
    )


# --------------------------------------------------------------------------------------
# GitHub Issues API
# --------------------------------------------------------------------------------------


class GitHubAPI:
    def __init__(self, token: str, repo: str) -> None:
        self.token = token
        self.repo = repo

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        url = f"https://api.github.com{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def list_open_issues(self) -> list[dict]:
        issues: list[dict] = []
        page = 1
        while True:
            batch = self._request(
                "GET",
                f"/repos/{self.repo}/issues?state=open&per_page=100&page={page}",
            )
            if not isinstance(batch, list) or not batch:
                break
            issues.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return issues

    def create_issue(self, title: str, body: str) -> dict:
        return self._request(
            "POST", f"/repos/{self.repo}/issues", {"title": title, "body": body}
        )

    def update_issue(self, number: int, title: str, body: str) -> dict:
        return self._request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            {"title": title, "body": body},
        )

    def close_issue(self, number: int, comment: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/comments",
            {"body": comment},
        )
        self._request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            {"state": "closed"},
        )


# Issue title pattern: "Update {pkgname} to {version}"
_ISSUE_TITLE_RE = re.compile(r"^Update (.+?) to (.+)$")
_CONFIG_ISSUE_TITLE_RE = re.compile(r"^Add upstream config for (.+)$")


def _upstream_url(pkg_name: str, pkg_config: dict) -> str:
    if "upstream_url" in pkg_config:
        return pkg_config["upstream_url"]
    source = pkg_config["source"]
    if source == "pypi":
        return f"https://pypi.org/project/{pkg_config['pypi_name']}/"
    if source == "github":
        return f"https://github.com/{pkg_config['github']}"
    if source == "sourceforge":
        return f"https://sourceforge.net/projects/{pkg_config['sourceforge_project']}/"
    if source == "website":
        return pkg_config["url"]
    if source == "svn":
        return pkg_config["svn_url"]
    return ""


def _make_update_issue_body(
    pkgname: str,
    aur_version: str,
    new_version: str,
    upstream_url: str,
    diff: str,
) -> str:
    today = date.today().isoformat()
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_link = (
        f"[Workflow run](https://github.com/{gh_repo}/actions/runs/{run_id})"
        if run_id
        else ""
    )
    sha_note = (
        ""
        if new_version not in diff or re.search(r"^\+sha256sums", diff, re.MULTILINE)
        else "\n> ⚠️ Checksum could not be computed automatically — update manually.\n"
    )
    repo_link = f"[aur-pkgbuild-updater](https://github.com/{gh_repo})"
    footer = f"> *Last checked: {today} · Auto-generated by {repo_link}*"
    if run_link:
        footer = (
            f"> *Last checked: {today} · Auto-generated by {repo_link} · {run_link}*"
        )
    return (
        f"## Update `{pkgname}` to {new_version}\n\n"
        f"**Current AUR version:** {aur_version}  \n"
        f"**New upstream version:** {new_version}  \n"
        f"**Upstream:** {upstream_url}\n\n"
        f"### Proposed PKGBUILD changes\n\n"
        f"```diff\n{diff}```\n"
        f"{sha_note}\n"
        f"{footer}\n"
    )


def manage_update_issue(
    gh: GitHubAPI | None,
    pkgname: str,
    aur_version: str,
    new_version: str,
    upstream_url: str,
    diff: str,
    open_issues: list[dict],
    dry_run: bool,
) -> None:
    title = f"Update {pkgname} to {new_version}"
    body = _make_update_issue_body(
        pkgname, aur_version, new_version, upstream_url, diff
    )

    existing = [
        i
        for i in open_issues
        if (m := _ISSUE_TITLE_RE.match(i["title"])) and m.group(1) == pkgname
    ]

    if existing:
        issue = existing[0]
        m = _ISSUE_TITLE_RE.match(issue["title"])
        existing_version = m.group(2) if m else None
        if existing_version == new_version:
            print(
                f"  [skip] Issue #{issue['number']} already open for "
                f"{pkgname} {new_version}"
            )
            return
        print(
            f"  [update] Issue #{issue['number']} for {pkgname}: "
            f"{existing_version} → {new_version}"
        )
        if not dry_run and gh:
            gh.update_issue(issue["number"], title, body)
    else:
        print(f"  [create] New issue: {title!r}")
        if not dry_run and gh:
            gh.create_issue(title, body)


def close_resolved_issues(
    gh: GitHubAPI | None,
    outdated_pkg_names: set[str],
    open_issues: list[dict],
    dry_run: bool,
) -> None:
    today = date.today().isoformat()
    for issue in open_issues:
        m = _ISSUE_TITLE_RE.match(issue["title"])
        if not m:
            continue
        pkgname = m.group(1)
        if pkgname not in outdated_pkg_names:
            print(f"  [close] Issue #{issue['number']} for {pkgname} is resolved")
            if not dry_run and gh:
                gh.close_issue(
                    issue["number"],
                    f"Package has been updated on AUR. Closing automatically.\n\n"
                    f"*Checked: {today}*",
                )


def manage_config_issue(
    gh: GitHubAPI | None,
    pkgname: str,
    open_issues: list[dict],
    dry_run: bool,
) -> None:
    """Open a 'needs upstream config' issue for an unconfigured package."""
    title = f"Add upstream config for {pkgname}"
    existing = [i for i in open_issues if i["title"] == title]
    if existing:
        return
    print(f"  [create] Config needed issue for {pkgname!r}")
    if not dry_run and gh:
        gh.create_issue(
            title,
            f"The package `{pkgname}` is maintained on AUR but has no entry "
            f"in `packages.toml`.\n\n"
            f"Please add an upstream source configuration so the update checker "
            f"can monitor it. See "
            f"[packages.toml](../../blob/main/packages.toml) for the format.\n",
        )


# --------------------------------------------------------------------------------------
# Git helpers
# --------------------------------------------------------------------------------------


def commit_and_push(config_path: Path, stale_keys: set[str]) -> None:
    """Commit the cleaned-up packages.toml and push."""
    removed = ", ".join(sorted(stale_keys))
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "github-actions[bot]@users.noreply.github.com",
        ],
        check=True,
    )
    subprocess.run(["git", "add", str(config_path)], check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"chore: remove stale packages.toml entries ({removed}) [skip ci]",
        ],
        check=True,
    )
    subprocess.run(["git", "push"], check=True)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check AUR packages for upstream updates"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed actions without creating/closing issues or committing",
    )
    parser.add_argument(
        "--maintainer",
        default="cbrnr",
        help="AUR maintainer username (default: cbrnr)",
    )
    args = parser.parse_args()

    config_path = Path(__file__).parent / "packages.toml"
    config: dict = load_config(config_path)

    github_token = os.environ.get("GITHUB_TOKEN")
    github_repo = os.environ.get("GITHUB_REPOSITORY")

    if not args.dry_run and (not github_token or not github_repo):
        print(
            "ERROR: GITHUB_TOKEN and GITHUB_REPOSITORY must be set (or use --dry-run)",
            file=sys.stderr,
        )
        sys.exit(1)

    gh = None if args.dry_run else GitHubAPI(github_token, github_repo)

    # ---- Step 1: Live AUR package list ------------------------------------
    print(f"Fetching AUR packages for maintainer '{args.maintainer}'...")
    aur_packages = get_aur_packages(args.maintainer)
    print(f"Found {len(aur_packages)} packages on AUR.\n")

    aur_names = set(aur_packages.keys())
    config_names = set(config.keys())

    # ---- Step 2: Remove stale config entries ------------------------------
    stale = config_names - aur_names
    if stale:
        print(f"Stale packages.toml entries: {', '.join(sorted(stale))}")
        if not args.dry_run:
            remove_stale_config_entries(config_path, stale)
            commit_and_push(config_path, stale)
            print("  packages.toml updated and pushed.\n")
        else:
            print("  [dry-run] Would remove stale entries.\n")
        for k in stale:
            config.pop(k, None)

    # ---- Step 3: Unconfigured packages ------------------------------------
    unconfigured = aur_names - config_names
    if unconfigured:
        print(f"Packages without upstream config: {', '.join(sorted(unconfigured))}\n")

    # ---- Step 4: Fetch open issues once -----------------------------------
    open_issues: list[dict] = []
    if gh:
        print("Fetching open GitHub issues...")
        open_issues = gh.list_open_issues()
        print(f"Found {len(open_issues)} open issue(s).\n")

    # ---- Step 5: Open 'needs config' issues for unconfigured packages -----
    for pkgname in sorted(unconfigured):
        manage_config_issue(gh, pkgname, open_issues, dry_run=args.dry_run)

    # ---- Step 6: Check versions and manage update issues ------------------
    outdated_pkg_names: set[str] = set()

    with tempfile.TemporaryDirectory() as tmpdir:
        for pkgname in sorted(aur_packages.keys()):
            if pkgname not in config:
                continue

            pkg_config = config[pkgname]
            aur_info = aur_packages[pkgname]
            aur_pkgver = aur_info["pkgver"]
            aur_version_str = aur_info["version"]

            print(f"Checking {pkgname} (AUR: {aur_version_str})...")

            try:
                upstream_ver = get_upstream_version(
                    pkg_config, github_token=github_token
                )
            except Exception as exc:
                print(f"  [error] Could not fetch upstream version: {exc}\n")
                continue

            print(f"  upstream: {upstream_ver}")

            if not is_outdated(aur_pkgver, upstream_ver):
                print("  up to date\n")
                continue

            outdated_pkg_names.add(pkgname)
            print(f"  OUTDATED: {aur_pkgver} → {upstream_ver}")

            pkgbuild = get_pkgbuild(pkgname, tmpdir)
            if not pkgbuild:
                print("  [error] Could not fetch PKGBUILD\n")
                continue

            new_sha256 = get_new_sha256(pkg_config, upstream_ver, pkgbuild)
            diff = generate_diff(pkgbuild, upstream_ver, new_sha256)

            if args.dry_run:
                print(f"  --- proposed diff ---\n{diff}")

            manage_update_issue(
                gh,
                pkgname,
                aur_version_str,
                upstream_ver,
                _upstream_url(pkgname, pkg_config),
                diff,
                open_issues,
                dry_run=args.dry_run,
            )
            print()

    # ---- Step 7: Close resolved issues ------------------------------------
    print("Closing resolved issues...")
    close_resolved_issues(gh, outdated_pkg_names, open_issues, dry_run=args.dry_run)

    if outdated_pkg_names:
        print(f"\nOutdated packages: {', '.join(sorted(outdated_pkg_names))}")
    else:
        print("\nAll packages are up to date.")


if __name__ == "__main__":
    main()
