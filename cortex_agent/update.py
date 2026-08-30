"""Self-update against GitHub releases (the agent's own lane).

Release convention: tags agent-v<semver> build CortexIngest-Setup-*.exe
via .github/workflows/build-agent.yml. A tag containing -dev marks a dev
build (published as a prerelease); the stable channel ignores those, the
dev channel takes whatever is newest. Version comparison mirrors the old
Hub's rules: numeric tuples, dev.13 > dev.9, and a stable release
outranks a dev build of the same base.

Generalized on purpose: the repo comes from CORTEX_AGENT_UPDATE_REPO so
someone running their own fork updates from their own releases.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import urllib.request
from pathlib import Path

from cortex_agent import __version__

log = logging.getLogger("cortex.agent.update")

REPO = os.environ.get("CORTEX_AGENT_UPDATE_REPO", "turfptax/cortex-desktop")
TAG_PREFIX = "agent-v"
ASSET_PATTERN = re.compile(r"^CortexIngest-Setup-.*\.exe$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?$")

STABLE_RANK = 1_000_000  # a stable release outranks any dev of the same base


def parse_version(text: str) -> tuple | None:
    """(major, minor, patch, rank) where rank is the dev number or the
    stable sentinel. None for anything that is not an agent version."""
    match = _VERSION.match(text.strip())
    if not match:
        return None
    major, minor, patch, dev = match.groups()
    rank = int(dev) if dev is not None else STABLE_RANK
    return (int(major), int(minor), int(patch), rank)


def pick_latest(releases: list[dict], channel: str) -> dict | None:
    """The newest agent release visible to the channel, or None."""
    best = None
    best_version = None
    for release in releases:
        tag = str(release.get("tag_name", ""))
        if not tag.startswith(TAG_PREFIX):
            continue
        version = parse_version(tag[len(TAG_PREFIX):])
        if version is None:
            continue
        if channel != "dev" and (release.get("prerelease")
                                 or version[3] != STABLE_RANK):
            continue
        if best_version is None or version > best_version:
            best, best_version = release, version
    return best


def check(channel: str = "stable") -> dict:
    """Compare the newest visible release against the running version."""
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "cortex-ingest-agent"})
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.loads(response.read().decode("utf-8"))

    latest = pick_latest(releases, channel)
    current = parse_version(__version__)
    if latest is None:
        return {"current": __version__, "channel": channel,
                "update_available": False,
                "note": "no agent releases visible on this channel"}
    latest_version = parse_version(
        str(latest["tag_name"])[len(TAG_PREFIX):])
    asset_url = ""
    for asset in latest.get("assets", []):
        if ASSET_PATTERN.match(str(asset.get("name", ""))):
            asset_url = str(asset.get("browser_download_url", ""))
            break
    return {
        "current": __version__,
        "channel": channel,
        "latest": str(latest["tag_name"]),
        "update_available": bool(current and latest_version
                                 and latest_version > current),
        "installer_url": asset_url,
    }


def download_and_launch(installer_url: str) -> str:
    """Fetch the installer to temp and start it; the installer taskkills
    the running agent, upgrades in place, and relaunches."""
    name = installer_url.rsplit("/", 1)[-1] or "CortexIngest-Setup.exe"
    target = Path(tempfile.gettempdir()) / name
    request = urllib.request.Request(
        installer_url, headers={"User-Agent": "cortex-ingest-agent"})
    with urllib.request.urlopen(request, timeout=300) as response, \
            open(target, "wb") as out:
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
    log.info("downloaded %s (%d bytes); launching installer",
             target, target.stat().st_size)
    os.startfile(str(target))  # noqa: S606 - the point of the feature
    return str(target)
