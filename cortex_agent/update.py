"""Self-update against GitHub releases (the agent's own lane).

Release convention: tags agent-v<semver> build CortexIngest-Setup-*.exe
via .github/workflows/build-agent.yml. A tag containing -dev marks a dev
build (published as a prerelease); the stable channel ignores those, the
dev channel takes whatever is newest. Version comparison mirrors the old
Hub's rules: numeric tuples, dev.13 > dev.9, and a stable release
outranks a dev build of the same base.

Trust: nothing launches unless the release carries agent-release.json
signed by a key pinned in RELEASE_KEYS, the manifest names this release's
tag, the tag is newer than the running build, and the downloaded bytes
match the manifest's SHA-256 and size. Anything else is refused, and so is
every release while no key is pinned. Every request verifies TLS through
the OS (truststore), as cortex_intake.oauth does; bare urlopen would trust
OpenSSL's copy of the ROOT store and skip Microsoft's disallowed list.
scripts/sign_agent_release.py makes the key and the signature.

Generalized on purpose: the repo comes from CORTEX_AGENT_UPDATE_REPO so
someone running their own fork updates from their own releases, signed
with the key pinned in their own build.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import ssl
import tempfile
import urllib.request
from pathlib import Path

import truststore

from cortex_agent import __version__

log = logging.getLogger("cortex.agent.update")

REPO = os.environ.get("CORTEX_AGENT_UPDATE_REPO", "turfptax/cortex-desktop")
TAG_PREFIX = "agent-v"
ASSET_PATTERN = re.compile(r"^CortexIngest-Setup-[\w.+-]+\.exe$")
MANIFEST_ASSET = "agent-release.json"
SIGNATURE_ASSET = "agent-release.json.sig"
MANIFEST_FORMAT = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?$")

STABLE_RANK = 1_000_000  # a stable release outranks any dev of the same base

# Ed25519 public keys (raw 32 bytes, base64) allowed to sign releases.
# Empty means no release can verify, so the updater installs nothing.
# Pin the line `python scripts/sign_agent_release.py keygen` prints.
RELEASE_KEYS: tuple[str, ...] = ()


class UpdateRefused(Exception):
    """The release failed verification; nothing was launched."""


def tls_context() -> ssl.SSLContext:
    """The OS chain verifier; every urlopen in this module passes it."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _open(url: str, *, timeout: int, accept: str = ""):
    if not url.startswith("https://"):
        raise UpdateRefused(f"refusing a non-HTTPS url: {url[:80]}")
    headers = {"User-Agent": "cortex-ingest-agent"}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout,
                                  context=tls_context())


def _read_capped(url: str, limit: int) -> bytes:
    with _open(url, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateRefused(f"{url.rsplit('/', 1)[-1]} exceeds {limit} bytes")
    return data


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
    """Compare the newest visible release against the running version.
    `refused` says why an available update will not be installed."""
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
    with _open(url, timeout=30,
               accept="application/vnd.github+json") as response:
        releases = json.loads(response.read().decode("utf-8"))

    latest = pick_latest(releases, channel)
    current = parse_version(__version__)
    if latest is None:
        return {"current": __version__, "channel": channel,
                "update_available": False,
                "note": "no agent releases visible on this channel"}
    latest_version = parse_version(
        str(latest["tag_name"])[len(TAG_PREFIX):])
    assets = {str(asset.get("name", "")):
              str(asset.get("browser_download_url", ""))
              for asset in latest.get("assets", [])}
    installer = next((name for name in assets if ASSET_PATTERN.match(name)),
                     "")
    result = {
        "current": __version__,
        "channel": channel,
        "latest": str(latest["tag_name"]),
        "update_available": bool(current and latest_version
                                 and latest_version > current),
        "installer_url": assets.get(installer, ""),
        "installer_name": installer,
        "manifest_url": assets.get(MANIFEST_ASSET, ""),
        "signature_url": assets.get(SIGNATURE_ASSET, ""),
    }
    if result["update_available"]:
        if not RELEASE_KEYS:
            result["refused"] = "this build pins no release key"
        elif not (result["manifest_url"] and result["signature_url"]):
            result["refused"] = "the release is unsigned"
    return result


def verify_manifest(manifest: bytes, signature: bytes,
                    keys: tuple[str, ...] | None = None) -> dict:
    """The manifest's claims, if a pinned key signed these exact bytes."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    keys = RELEASE_KEYS if keys is None else keys
    if not keys:
        raise UpdateRefused("this build pins no release key")
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
    except ValueError as exc:
        raise UpdateRefused("the release signature is not base64") from exc
    for key in keys:
        try:
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(key)).verify(raw, manifest)
            break
        except InvalidSignature:
            continue
    else:
        raise UpdateRefused("the manifest is not signed by a pinned key")
    claims = json.loads(manifest.decode("utf-8"))
    if (not isinstance(claims, dict)
            or claims.get("format") != MANIFEST_FORMAT
            or not isinstance(claims.get("files"), dict)):
        raise UpdateRefused("unrecognised manifest format")
    return claims


def _download(url: str, target: Path, limit: int) -> tuple[str, int]:
    """Stream to target, hashing as it goes; refuses past `limit` bytes."""
    digest = hashlib.sha256()
    size = 0
    with _open(url, timeout=300) as response, open(target, "wb") as out:
        while chunk := response.read(1 << 16):
            size += len(chunk)
            if size > limit:
                break
            digest.update(chunk)
            out.write(chunk)
    if size > limit:
        target.unlink(missing_ok=True)
        raise UpdateRefused(f"{target.name} is larger than its manifest says")
    return digest.hexdigest(), size


def download_and_launch(release: dict) -> str:
    """Verify a check() result's release, then start its installer; the
    installer taskkills the running agent, upgrades in place, and
    relaunches. Raises UpdateRefused, launching nothing, unless every
    check in the module docstring passes."""
    tag = str(release.get("latest", ""))
    name = str(release.get("installer_name", ""))
    if not ASSET_PATTERN.match(name) or not release.get("installer_url"):
        raise UpdateRefused("the release has no installer asset")
    if not (release.get("manifest_url") and release.get("signature_url")):
        raise UpdateRefused("the release is unsigned")
    claims = verify_manifest(
        _read_capped(release["manifest_url"], MAX_MANIFEST_BYTES),
        _read_capped(release["signature_url"], MAX_MANIFEST_BYTES))
    if claims.get("tag") != tag:
        raise UpdateRefused("the signed manifest is for {!r}, not {}".format(
            claims.get("tag"), tag))
    signed = parse_version(tag[len(TAG_PREFIX):])
    current = parse_version(__version__)
    if signed is None or (current is not None and signed <= current):
        raise UpdateRefused(f"{tag} is not newer than v{__version__}")
    entry = claims["files"].get(name)
    if not isinstance(entry, dict):
        raise UpdateRefused(f"the signed manifest does not list {name}")
    size = int(entry.get("size", 0))
    expected = str(entry.get("sha256", "")).lower()
    if not 0 < size <= MAX_INSTALLER_BYTES or len(expected) != 64:
        raise UpdateRefused(f"the manifest entry for {name} is malformed")

    target = Path(tempfile.mkdtemp(prefix="cortex-update-")) / name
    digest, got = _download(release["installer_url"], target, size)
    if got != size or not hmac.compare_digest(digest, expected):
        target.unlink(missing_ok=True)
        raise UpdateRefused(f"{name} does not match its signed SHA-256")
    log.info("verified %s (%d bytes, sha256 %s) against %s; launching",
             target, got, digest, tag)
    os.startfile(str(target))  # noqa: S606 - the point of the feature
    return str(target)
