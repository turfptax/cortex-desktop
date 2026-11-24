"""The agent updater's version and channel rules.

Mirrors the old Hub's hard-won ordering: numeric tuples (never string
compare), dev.13 > dev.9, and a stable release outranks a dev build of
the same base. Channel rules: stable ignores prereleases and dev-suffixed
tags; dev takes whatever is newest. Then the signed-manifest gate, which
decides whether anything launches at all.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
import truststore
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cortex_agent import update
from cortex_agent.update import parse_version, pick_latest


def release(tag: str, prerelease: bool) -> dict:
    return {"tag_name": tag, "prerelease": prerelease, "assets": []}


def test_version_ordering():
    assert parse_version("0.2.0") > parse_version("0.1.9")
    assert parse_version("0.2.0-dev.13") > parse_version("0.2.0-dev.9")
    assert parse_version("0.2.0") > parse_version("0.2.0-dev.99")
    assert parse_version("0.10.0") > parse_version("0.9.9")
    assert parse_version("not-a-version") is None
    assert parse_version("v0.2.0") is None


def test_stable_channel_skips_dev_builds():
    releases = [
        release("agent-v0.2.0-dev.3", True),
        release("agent-v0.1.1", False),
        release("v0.22.0", False),          # the legacy Hub lane, ignored
    ]
    best = pick_latest(releases, "stable")
    assert best["tag_name"] == "agent-v0.1.1"


def test_dev_channel_takes_newest_of_all():
    releases = [
        release("agent-v0.2.0-dev.3", True),
        release("agent-v0.1.1", False),
    ]
    best = pick_latest(releases, "dev")
    assert best["tag_name"] == "agent-v0.2.0-dev.3"


def test_stable_release_beats_same_base_dev_on_dev_channel():
    releases = [
        release("agent-v0.2.0-dev.9", True),
        release("agent-v0.2.0", False),
    ]
    best = pick_latest(releases, "dev")
    assert best["tag_name"] == "agent-v0.2.0"


def test_no_agent_releases_returns_none():
    assert pick_latest([release("v0.22.0", False)], "stable") is None


# == the signed-manifest gate ======================================
#
# Nothing launches unless a pinned key signed a manifest that names this
# tag, the tag is newer than the running build, and the installer's bytes
# match the manifest. Each test breaks one link and expects a refusal with
# nothing started.

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = b"MZ pretend installer " * 512
NAME = "CortexIngest-Setup-9.0.0.exe"
TAG = "agent-v9.0.0"


def _public(key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _manifest(*, tag=TAG, name=NAME, payload=INSTALLER) -> bytes:
    return json.dumps({"format": 1, "tag": tag, "files": {name: {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload)}}}).encode()


class _Feed:
    """A fake GitHub: URL -> bytes, recording the TLS context of each open."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.contexts = []

    def urlopen(self, request, timeout=None, context=None):
        self.contexts.append(context)
        return io.BytesIO(self.files[request.full_url])


@pytest.fixture
def signer():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def launched(monkeypatch, signer, tmp_path):
    started = []
    monkeypatch.setattr(update.os, "startfile", started.append, raising=False)
    monkeypatch.setattr(update, "RELEASE_KEYS", (_public(signer),))
    monkeypatch.setattr(update.tempfile, "mkdtemp",
                        lambda prefix="": str(tmp_path))
    return started


def _serve(monkeypatch, signer, *, manifest=None, payload=INSTALLER,
           signature=None, with_signature=True) -> dict:
    manifest = _manifest() if manifest is None else manifest
    base = "https://github.example/download/"
    files = {base + NAME: payload, base + "agent-release.json": manifest,
             base + "agent-release.json.sig":
             signature or base64.b64encode(signer.sign(manifest))}
    api = [{"tag_name": TAG, "prerelease": False, "assets": [
        {"name": name.rsplit("/", 1)[-1], "browser_download_url": name}
        for name in files
        if with_signature or not name.endswith((".json", ".sig"))]}]
    files[f"https://api.github.com/repos/{update.REPO}/releases?per_page=20"] = \
        json.dumps(api).encode()
    feed = _Feed(files)
    monkeypatch.setattr(update.urllib.request, "urlopen", feed.urlopen)
    result = update.check("stable")
    result["_feed"] = feed
    return result


def test_signed_release_verifies_and_launches(monkeypatch, signer, launched):
    release = _serve(monkeypatch, signer)
    assert release["update_available"] and "refused" not in release
    path = update.download_and_launch(release)
    assert launched == [path]
    assert Path(path).read_bytes() == INSTALLER
    assert Path(path).name == NAME
    # every request (feed, manifest, signature, installer) used the OS verifier
    contexts = release["_feed"].contexts
    assert len(contexts) == 4
    assert all(isinstance(c, truststore.SSLContext) for c in contexts)


def test_tampered_installer_is_refused_and_deleted(monkeypatch, signer,
                                                   launched, tmp_path):
    evil = INSTALLER[:-1] + b"!"
    release = _serve(monkeypatch, signer, payload=evil)
    with pytest.raises(update.UpdateRefused, match="SHA-256"):
        update.download_and_launch(release)
    assert launched == []
    assert not (tmp_path / NAME).exists()


def test_oversize_installer_is_refused(monkeypatch, signer, launched):
    release = _serve(monkeypatch, signer, payload=INSTALLER + b"extra")
    with pytest.raises(update.UpdateRefused, match="larger"):
        update.download_and_launch(release)
    assert launched == []


def test_signature_from_an_unpinned_key_is_refused(monkeypatch, signer,
                                                   launched):
    stranger = Ed25519PrivateKey.generate()
    manifest = _manifest()
    release = _serve(monkeypatch, signer, manifest=manifest,
                     signature=base64.b64encode(stranger.sign(manifest)))
    with pytest.raises(update.UpdateRefused, match="pinned key"):
        update.download_and_launch(release)
    assert launched == []


def test_a_signed_manifest_replayed_under_another_tag_is_refused(
        monkeypatch, signer, launched):
    release = _serve(monkeypatch, signer,
                     manifest=_manifest(tag="agent-v8.0.0"))
    with pytest.raises(update.UpdateRefused, match="agent-v8.0.0"):
        update.download_and_launch(release)
    assert launched == []


def test_a_signed_older_release_is_refused(monkeypatch, signer, launched):
    release = _serve(monkeypatch, signer)
    monkeypatch.setattr(update, "__version__", "9.0.0")
    with pytest.raises(update.UpdateRefused, match="not newer"):
        update.download_and_launch(release)
    assert launched == []


def test_unsigned_release_is_flagged_and_refused(monkeypatch, signer,
                                                 launched):
    release = _serve(monkeypatch, signer, with_signature=False)
    assert release["refused"] == "the release is unsigned"
    with pytest.raises(update.UpdateRefused, match="unsigned"):
        update.download_and_launch(release)
    assert launched == []


def test_no_pinned_key_refuses_everything(monkeypatch, signer, launched):
    monkeypatch.setattr(update, "RELEASE_KEYS", ())
    release = _serve(monkeypatch, signer)
    assert release["refused"] == "this build pins no release key"
    with pytest.raises(update.UpdateRefused, match="pins no release key"):
        update.download_and_launch(release)
    assert launched == []


def test_non_https_urls_are_refused(monkeypatch, signer, launched):
    release = _serve(monkeypatch, signer)
    release["installer_url"] = "http://github.example/download/" + NAME
    with pytest.raises(update.UpdateRefused, match="non-HTTPS"):
        update.download_and_launch(release)
    assert launched == []


def test_installer_names_cannot_carry_paths():
    assert update.ASSET_PATTERN.match("CortexIngest-Setup-0.2.0-dev.3.exe")
    assert not update.ASSET_PATTERN.match(r"CortexIngest-Setup-..\evil.exe")
    assert not update.ASSET_PATTERN.match("CortexIngest-Setup-a/b.exe")


def test_release_script_and_agent_agree(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "sign_agent_release", REPO_ROOT / "scripts" / "sign_agent_release.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.delenv(script.PASSPHRASE_ENV, raising=False)

    public = script.keygen(tmp_path / "release.key")
    installer = tmp_path / NAME
    installer.write_bytes(INSTALLER)
    manifest, signature = script.sign(
        TAG, [installer], script.load_key(tmp_path / "release.key"),
        tmp_path / "out")
    claims = update.verify_manifest(manifest.read_bytes(),
                                    signature.read_bytes(), keys=(public,))
    assert claims["tag"] == TAG
    assert claims["files"][NAME]["sha256"] == hashlib.sha256(
        INSTALLER).hexdigest()
    with pytest.raises(SystemExit):
        script.keygen(tmp_path / "release.key")   # never overwrites a key
