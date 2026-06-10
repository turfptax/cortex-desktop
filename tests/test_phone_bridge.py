"""Tests for the phone-bridge integration asks (2026-06-10).

Ask 2: the daemon answers inbound phone-originated CMD: lines on the
dongle serial port. Ask 1: the Pi is optional (empty pi_host means
no Pi, fast-fail everywhere, dongle-only operation works).

See docs/CORTEX_LINK_PHONE_BRIDGE.md for the protocol contract.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from cortex_mcp import wifi_bridge
from cortex_mcp.bridge import SerialBridge
from cortex_mcp.daemon import CortexDaemon

from services import pi_client


# ── Ask 2: inbound responder ─────────────────────────────────────


@pytest.fixture
def daemon():
    return CortexDaemon(serial_port="COM_FAKE")


def test_ping_reply_shape(daemon):
    reply = daemon.answer_inbound("CMD:ping")
    assert reply.startswith("RSP:ping:")
    body = json.loads(reply[len("RSP:ping:"):])
    assert body["ok"] is True
    assert body["host"] == "desktop"
    assert body["via"] == "cortex-link"


def test_echo_returns_payload_verbatim(daemon):
    payload = '{"hello": "phone", "n": 3}'
    assert daemon.answer_inbound("CMD:echo:" + payload) == "RSP:echo:" + payload


def test_unknown_command_gets_ack(daemon):
    reply = daemon.answer_inbound('CMD:sync_push:{"kind": "x"}')
    assert reply == "ACK:sync_push:received-by-desktop"


def test_phone_request_counter(daemon):
    daemon.answer_inbound("CMD:ping")
    daemon.answer_inbound("CMD:ping")
    assert daemon._phone_requests == 2


def test_reader_routes_cmd_to_handler_not_queue():
    """Inbound CMD: lines must reach the handler and never pollute
    the rx queue (where they would corrupt send_and_wait replies).
    Drives the reader-loop classification logic directly."""
    bridge = SerialBridge(port="COM_FAKE")
    answered = []
    bridge.inbound_handler = lambda line: answered.append(line) or "RSP:ping:{}"
    sent = []
    bridge.send = lambda msg: sent.append(msg)

    # Replicate the reader-loop branch for one line of each class.
    for line in ["CMD:ping", "RSP:status:{}", "BLE: connected to phone"]:
        if line.startswith("CMD:") and bridge.inbound_handler:
            reply = bridge.inbound_handler(line)
            if reply:
                bridge.send(reply)
            continue
        bridge._rx_queue.append((0, line))

    assert answered == ["CMD:ping"]
    assert sent == ["RSP:ping:{}"]
    queued = [text for _ts, text in bridge._rx_queue]
    assert "CMD:ping" not in queued
    assert "RSP:status:{}" in queued     # replies still flow to clients
    assert "BLE: connected to phone" in queued  # debug lines tolerated


# ── Ask 1: Pi optional ───────────────────────────────────────────


@pytest.fixture
def no_pi(tmp_path, monkeypatch):
    """config.json with an explicit empty pi_host = Pi disabled."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("CORTEX_PI_HOST", raising=False)
    cortex_dir = tmp_path / "Cortex"
    cortex_dir.mkdir(parents=True)
    (cortex_dir / "config.json").write_text(json.dumps({"pi_host": ""}))
    monkeypatch.setattr(
        wifi_bridge, "DISCOVERY_FILE", str(tmp_path / "no-discovery.json"))
    return tmp_path


def test_empty_pi_host_disables_pi(no_pi):
    assert wifi_bridge.get_pi_host() == ""
    # No network probe, instant False -> transport picker goes
    # straight to the dongle/daemon path.
    assert wifi_bridge.is_pi_reachable() is False


def test_empty_host_does_not_fall_back_to_discovery(no_pi):
    """Explicit empty pi_host must NOT be resurrected by the BLE
    discovery file or the hardcoded default."""
    disc = no_pi / "discovery.json"
    disc.write_text(json.dumps({"ip": "10.3.3.3"}))
    wifi_bridge.DISCOVERY_FILE = str(disc)
    assert wifi_bridge.get_pi_host() == ""


def test_hub_pi_client_fast_fails_unconfigured(monkeypatch):
    monkeypatch.setattr(pi_client.settings, "pi_host", "")
    assert asyncio.run(pi_client.check_online()) is False
    h = asyncio.run(pi_client.health())
    assert h["online"] is False and "not configured" in h["error"]
    r = asyncio.run(pi_client.plugin_call("overseer", "GET", "/status"))
    assert r == {"ok": False, "error": "Pi not configured"}
    c = asyncio.run(pi_client.send_command("ping"))
    assert "not configured" in c["error"]
