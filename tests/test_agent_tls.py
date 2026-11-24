"""Agent TLS: urllib verifies through the OS, and --check stays read-only.

The 2026-10 emSign compromise (GHSA-6q48-6c7w-9gw2) is blocked on Windows by
Microsoft's disallowed-certificate list, which only CryptoAPI reads. Bare
urlopen hands OpenSSL the ROOT store and skips that list. A CA that only
OpenSSL trusts (via SSL_CERT_FILE) stands in for the compromised roots here:
the agent must refuse it, and no urlopen in cortex_agent may go out without
the OS context.
"""

from __future__ import annotations

import ast
import datetime as dt
import http.server
import ipaddress
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cortex_agent import update

AGENT_SRC = Path(__file__).resolve().parent.parent / "cortex_agent"

# On Linux truststore delegates to OpenSSL, so SSL_CERT_FILE is the OS store.
os_verifier = pytest.mark.skipif(sys.platform.startswith("linux"),
                                 reason="truststore uses OpenSSL on Linux")


def _mint_chain(directory: Path) -> tuple[Path, Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                            "OpenSSL-only CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name)
          .issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1)
          .not_valid_before(now - dt.timedelta(hours=1))
          .not_valid_after(now + dt.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                         True)
          .sign(ca_key, hashes.SHA256()))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                                        "127.0.0.1")]))
            .issuer_name(ca_name).public_key(leaf_key.public_key())
            .serial_number(2)
            .not_valid_before(now - dt.timedelta(hours=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(ca_key, hashes.SHA256()))
    paths = directory / "ca.pem", directory / "leaf.pem", directory / "leaf.key"
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return paths


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler contract
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *args):
        pass


@pytest.fixture
def openssl_only_server(tmp_path, monkeypatch):
    ca, leaf, key = _mint_chain(tmp_path)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Ok)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    yield f"https://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@os_verifier
def test_bare_urlopen_trusts_the_openssl_only_ca(openssl_only_server):
    # The control: proves the fixture reproduces the exposure.
    with urllib.request.urlopen(openssl_only_server, timeout=10) as r:
        assert r.status == 200


@os_verifier
def test_updater_refuses_the_openssl_only_ca(openssl_only_server):
    with pytest.raises(urllib.error.URLError) as caught:
        update._read_capped(openssl_only_server, 1024)
    assert isinstance(caught.value.reason, ssl.SSLError)


def test_no_urlopen_in_the_agent_goes_out_without_a_context():
    bare = []
    for path in sorted(AGENT_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name == "urlopen" and not any(
                    k.arg == "context" for k in node.keywords):
                bare.append(f"{path.name}:{node.lineno}")
    assert bare == [], "urlopen without the OS context: " + ", ".join(bare)


# == --check never writes ==========================================


@pytest.fixture
def app(monkeypatch):
    from cortex_agent import app as agent_app

    monkeypatch.setattr(update, "check",
                        lambda channel: {"update_available": False,
                                         "note": "none"})

    class _Meta:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"issuer": "https://graph.example"}'

    monkeypatch.setattr(agent_app.urllib.request, "urlopen",
                        lambda *a, **k: _Meta())
    return agent_app


def _grant(app, monkeypatch, *, seconds_left: int) -> None:
    monkeypatch.setattr(app.oauth, "load_auth", lambda path=None: {
        "access_token": "a", "expires_at": int(time.time()) + seconds_left})


def test_check_skips_capture_rather_than_refresh(app, monkeypatch):
    _grant(app, monkeypatch, seconds_left=10)
    monkeypatch.setattr(app, "sweep",
                        lambda *a, **k: pytest.fail("must not sweep"))
    report = app.health_check()
    assert "skipped" in report["capture_dry_run"]
    assert report["oauth_metadata"]["issuer"] == "https://graph.example"


def test_check_sweeps_dry_run_only(app, monkeypatch):
    _grant(app, monkeypatch, seconds_left=86400 * 10)
    calls = []

    def fake_sweep(gateway, **kwargs):
        calls.append(kwargs)
        return {"parsed": 2, "skipped": 5, "minutes_logged": 7,
                "unlinked": []}

    monkeypatch.setattr(app, "sweep", fake_sweep)
    monkeypatch.setattr(app, "build_mcp_gateway", lambda: object())
    report = app.health_check()
    assert calls == [{"dry_run": True}]
    assert report["ok"] is True
    assert report["capture_dry_run"]["minutes_pending"] == 7
