"""Tests for services/pi_client.py (Phase 2 of the redesign).

First coverage of the Hub-to-Pi path. Uses httpx.MockTransport so no
network and no real Pi is involved; the transport is injected through
pi_client._client, which get_client() honors.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from services import pi_client


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def captured(monkeypatch):
    """Install a mock transport that records requests and returns a
    canned JSON body. Yields the list of captured requests."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "echo": True})

    monkeypatch.setattr(pi_client, "_client", make_client(handler))
    yield requests


# ── plugin_call ──────────────────────────────────────────────────


def test_plugin_call_get_uses_query_params(captured):
    result = asyncio.run(
        pi_client.plugin_call("overseer", "GET", "/themes", {"limit": 5}))
    assert result["ok"] is True
    req = captured[0]
    assert req.method == "GET"
    assert req.url.path == "/plugins/overseer/themes"
    assert req.url.params["limit"] == "5"
    assert req.headers["Authorization"].startswith("Basic ")


def test_plugin_call_post_uses_json_body(captured):
    asyncio.run(
        pi_client.plugin_call("overseer", "POST", "/tick-now", {"x": 1}))
    req = captured[0]
    assert req.method == "POST"
    assert json.loads(req.content) == {"x": 1}


def test_plugin_call_timeout_maps_to_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("slow", request=request)

    monkeypatch.setattr(pi_client, "_client", make_client(handler))
    result = asyncio.run(pi_client.plugin_call("overseer", "GET", "/status"))
    assert result == {"ok": False, "error": "Pi request timed out"}


def test_plugin_call_connect_error_maps_to_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(pi_client, "_client", make_client(handler))
    result = asyncio.run(pi_client.plugin_call("overseer", "GET", "/status"))
    assert result == {"ok": False, "error": "Cannot connect to Pi"}


def test_plugin_call_http_error_is_caught(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(pi_client, "_client", make_client(handler))
    result = asyncio.run(pi_client.plugin_call("overseer", "GET", "/status"))
    assert result["ok"] is False
    assert "500" in result["error"]


# ── send_command_parsed ──────────────────────────────────────────


def _command_client(monkeypatch, response_str):
    def handler(request):
        return httpx.Response(200, json={"ok": True,
                                         "response": response_str})

    monkeypatch.setattr(pi_client, "_client", make_client(handler))


def test_send_command_parsed_rsp(monkeypatch):
    _command_client(monkeypatch, 'RSP:status:{"uptime": 42}')
    result = asyncio.run(pi_client.send_command_parsed("status"))
    assert result == {"data": {"uptime": 42}}


def test_send_command_parsed_ack(monkeypatch):
    _command_client(monkeypatch, 'ACK:note:{"id": 7}')
    result = asyncio.run(pi_client.send_command_parsed("note"))
    assert result == {"data": {"id": 7}}


def test_send_command_parsed_unexpected_format(monkeypatch):
    _command_client(monkeypatch, "garbage")
    result = asyncio.run(pi_client.send_command_parsed("status"))
    assert result["data"] is None
    assert "Unexpected" in result["error"]


# ── health / shapes / pool lifecycle ─────────────────────────────


def test_health_error_shape(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(pi_client, "_client", make_client(handler))
    result = asyncio.run(pi_client.health())
    assert result["online"] is False
    assert "error" in result


def test_shared_client_reused_and_closable(monkeypatch):
    monkeypatch.setattr(pi_client, "_client", None)
    c1 = pi_client.get_client()
    c2 = pi_client.get_client()
    assert c1 is c2
    asyncio.run(pi_client.aclose_client())
    assert pi_client._client is None
    # A new client is created transparently after close
    c3 = pi_client.get_client()
    assert c3 is not c1
    asyncio.run(pi_client.aclose_client())
