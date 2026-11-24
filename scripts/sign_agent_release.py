#!/usr/bin/env python3
"""Sign Cortex Ingest Agent releases so the in-tray updater will take them.

The agent installs nothing unless a release carries agent-release.json
signed by a key pinned in cortex_agent/update.py RELEASE_KEYS (see that
module's docstring for every check it runs).

Usage
-----
    python scripts/sign_agent_release.py keygen KEYFILE
        New Ed25519 key to KEYFILE (never overwrites). Encrypted when
        AGENT_RELEASE_KEY_PASSPHRASE is set. Prints the public key to pin.

    python scripts/sign_agent_release.py sign --tag agent-v0.1.3 \\
            [--key KEYFILE] [--out-dir DIR] FILE...
        Writes DIR/agent-release.json and DIR/agent-release.json.sig.
        The key comes from KEYFILE or, in CI, from the PEM text in the
        AGENT_RELEASE_SIGNING_KEY environment variable.

    python scripts/sign_agent_release.py verify [--dir DIR] FILE...
        Checks the manifest against the keys this checkout pins and the
        files against the manifest, the way the agent will.

Keep the private key off the repo. Holding it offline (signing a draft
release locally) is stronger than an Actions secret: with the secret, a
compromised GitHub account can sign too.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

MANIFEST = "agent-release.json"
SIGNATURE = MANIFEST + ".sig"
PASSPHRASE_ENV = "AGENT_RELEASE_KEY_PASSPHRASE"
KEY_ENV = "AGENT_RELEASE_SIGNING_KEY"


def public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def keygen(path: Path) -> str:
    if path.exists():
        raise SystemExit(f"{path} exists; refusing to overwrite a key")
    key = Ed25519PrivateKey.generate()
    passphrase = os.environ.get(PASSPHRASE_ENV, "")
    encryption = (serialization.BestAvailableEncryption(passphrase.encode())
                  if passphrase else serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       encryption))
    if not passphrase:
        print(f"warning: {path} is unencrypted (set {PASSPHRASE_ENV})",
              file=sys.stderr)
    return public_b64(key)


def load_key(path: Path | None) -> Ed25519PrivateKey:
    pem = path.read_bytes() if path else os.environ.get(KEY_ENV, "").encode()
    if not pem.strip():
        raise SystemExit(f"no key: pass --key or set {KEY_ENV}")
    passphrase = os.environ.get(PASSPHRASE_ENV, "")
    if not passphrase and b"ENCRYPTED" in pem and sys.stdin.isatty():
        passphrase = getpass.getpass("key passphrase: ")
    key = serialization.load_pem_private_key(
        pem, password=passphrase.encode() if passphrase else None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("the key is not Ed25519")
    return key


def file_entry(path: Path) -> dict:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": path.stat().st_size}


def sign(tag: str, files: list[Path], key: Ed25519PrivateKey,
         out_dir: Path) -> tuple[Path, Path]:
    if not tag.startswith("agent-v"):
        raise SystemExit(f"{tag!r} is not an agent-v* tag")
    manifest = {
        "format": 1,
        "tag": tag,
        "signed_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "files": {path.name: file_entry(path) for path in files},
    }
    body = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path, signature_path = out_dir / MANIFEST, out_dir / SIGNATURE
    manifest_path.write_bytes(body)
    signature_path.write_bytes(base64.b64encode(key.sign(body)) + b"\n")
    return manifest_path, signature_path


def verify(directory: Path, files: list[Path]) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cortex_agent import update

    claims = update.verify_manifest((directory / MANIFEST).read_bytes(),
                                    (directory / SIGNATURE).read_bytes())
    for path in files:
        if claims["files"].get(path.name) != file_entry(path):
            raise SystemExit(f"{path.name} does not match the manifest")
    return claims


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sign_agent_release")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("keygen")
    gen.add_argument("keyfile", type=Path)
    signing = commands.add_parser("sign")
    signing.add_argument("--tag", required=True)
    signing.add_argument("--key", type=Path)
    signing.add_argument("--out-dir", type=Path, default=Path("."))
    signing.add_argument("files", type=Path, nargs="+")
    checking = commands.add_parser("verify")
    checking.add_argument("--dir", type=Path, default=Path("."))
    checking.add_argument("files", type=Path, nargs="*")
    args = parser.parse_args(argv)

    if args.command == "keygen":
        public = keygen(args.keyfile)
        print("Pin this in cortex_agent/update.py RELEASE_KEYS:")
        print(f'    "{public}",')
    elif args.command == "sign":
        manifest, signature = sign(args.tag, args.files, load_key(args.key),
                                   args.out_dir)
        print(f"wrote {manifest} and {signature}")
    else:
        claims = verify(args.dir, args.files)
        print(f"{claims['tag']}: signature and {len(args.files)} file(s) ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
