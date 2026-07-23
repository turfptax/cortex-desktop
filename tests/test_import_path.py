"""Tests for the two-step conversation import (upload + from-path ingest).

Why this file exists: in July 2026 the gateway /core proxy dropped the
X-Filename header on forwarded uploads, so step 1 of every import failed
with "Missing X-Filename header" (a 26-file batch failed 26/26). Nothing
asserted the upload's wire contract, so it shipped broken. These tests
pin that contract and the dedup / failure branches around it.

Mocking style matches test_pi_client (httpx.MockTransport) and
test_overseer_router (monkeypatched pi_client.plugin_call).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import overseer
from services import pi_client

# Captured before any monkeypatching so the patched constructor can
# still build a real client around the mock transport.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def upload_capture(monkeypatch):
    """Route the upload helper's ad-hoc httpx.AsyncClient through a
    MockTransport that records requests. Yields the captured list."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "ok": True,
            "filename": request.headers.get("X-Filename", ""),
            "size": len(request.content),
            "path": "/data/uploads/session.jsonl",
            "file_id": 1,
        })

    def patched_client(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)
    # Deterministic base URL in the cloud form (full URL with a path,
    # the gateway /core proxy shape); never inherit the dev's config.
    monkeypatch.setattr(overseer.settings, "pi_host",
                        "https://cloud.example/core")
    monkeypatch.setattr(overseer.settings, "pi_username", "cortex")
    monkeypatch.setattr(overseer.settings, "pi_password", "token")
    yield requests


@pytest.fixture
def jsonl(tmp_path):
    p = tmp_path / "8033de60-c35f-44a0-a506-c1608ae56ccc.jsonl"
    p.write_bytes(b'{"type":"user","message":"hi"}\n')
    return p


# == _upload_to_pi wire contract ==================================


def test_upload_sends_x_filename_header(upload_capture, jsonl):
    """THE regression test: the upload must carry X-Filename. The core
    rejects the upload without it, and a proxy that strips it breaks
    every import silently."""
    result = asyncio.run(overseer._upload_to_pi(jsonl))
    assert result["ok"] is True
    req = upload_capture[0]
    assert req.headers["X-Filename"] == jsonl.name


def test_upload_wire_shape(upload_capture, jsonl):
    asyncio.run(overseer._upload_to_pi(jsonl))
    req = upload_capture[0]
    assert req.method == "POST"
    assert str(req.url) == "https://cloud.example/core/files/uploads"
    assert req.headers["Content-Type"] == "application/octet-stream"
    assert req.headers["Authorization"].startswith("Basic ")
    assert req.headers["X-Tags"] == "claude-code,overseer-import"
    assert req.content == jsonl.read_bytes()


# == _import_one_path branches ====================================


@pytest.fixture
def ingest_calls(monkeypatch):
    """Capture step-2 plugin_call invocations; canned ingest reply."""
    calls = []

    async def fake_plugin_call(plugin, method, route,
                               payload=None, timeout=30.0):
        calls.append({"plugin": plugin, "method": method,
                      "route": route, "payload": payload})
        return {"ok": True, "imported_id": 42,
                "session_id": "sess-1", "message_count": 3}

    monkeypatch.setattr(pi_client, "plugin_call", fake_plugin_call)
    return calls


def test_import_one_path_happy(upload_capture, ingest_calls, jsonl):
    result = asyncio.run(
        overseer._import_one_path(str(jsonl), "claude-code", set()))
    assert result["ok"] is True
    assert result["imported_id"] == 42
    assert result["uploaded_to"] == "/data/uploads/session.jsonl"
    # Step 2 was triggered with the path step 1 returned.
    assert ingest_calls[0]["route"] == "/imports/from-path"
    assert ingest_calls[0]["method"] == "POST"
    assert ingest_calls[0]["payload"] == {
        "path": "/data/uploads/session.jsonl", "source": "claude-code"}


def test_import_one_path_dedup_skips_upload(upload_capture, ingest_calls,
                                            jsonl):
    digest = overseer._file_sha256(jsonl)
    result = asyncio.run(
        overseer._import_one_path(str(jsonl), "claude-code", {digest}))
    assert result["ok"] is True
    assert result["skipped"] == "already imported (hash match)"
    assert upload_capture == []   # never uploaded
    assert ingest_calls == []     # never ingested


def test_import_one_path_upload_failure(monkeypatch, ingest_calls, jsonl):
    def handler(request):
        return httpx.Response(400, text="Missing X-Filename header")

    def patched_client(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)
    monkeypatch.setattr(overseer.settings, "pi_host",
                        "https://cloud.example/core")
    result = asyncio.run(
        overseer._import_one_path(str(jsonl), "claude-code", set()))
    assert result["ok"] is False
    assert result["error"].startswith("upload failed:")
    assert ingest_calls == []     # step 2 never runs after a failed upload


def test_import_one_path_missing_file(ingest_calls):
    result = asyncio.run(overseer._import_one_path(
        "C:/nope/does-not-exist.jsonl", "claude-code", set()))
    assert result["ok"] is False
    assert result["error"] == "file not found on Hub"


# == _already_imported_hashes =====================================


def test_already_imported_hashes_collects_pages(monkeypatch):
    pages = [
        {"ok": True, "imports": [{"file_hash": "aaa"},
                                 {"file_hash": ""},
                                 {"file_hash": "bbb"}]},
    ]

    async def fake_plugin_call(plugin, method, route,
                               payload=None, timeout=30.0):
        return pages.pop(0)

    monkeypatch.setattr(pi_client, "plugin_call", fake_plugin_call)
    out = asyncio.run(overseer._already_imported_hashes("claude-code"))
    assert out == {"aaa", "bbb"}   # empty hash filtered out


def test_already_imported_hashes_degrades_to_empty(monkeypatch):
    async def offline(plugin, method, route, payload=None, timeout=30.0):
        return {"ok": False, "error": "Cannot connect to Cortex"}

    monkeypatch.setattr(pi_client, "plugin_call", offline)
    out = asyncio.run(overseer._already_imported_hashes("claude-code"))
    assert out == set()


# == /import route counting =======================================


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(overseer.router, prefix="/api/overseer")
    return TestClient(app)


def test_import_route_counts(client, monkeypatch):
    canned = {
        "a.jsonl": {"ok": True, "src": "a.jsonl", "imported_id": 1},
        "b.jsonl": {"ok": True, "src": "b.jsonl",
                    "skipped": "already imported (hash match)"},
        "c.jsonl": {"ok": False, "src": "c.jsonl", "error": "boom"},
    }

    async def fake_import_one(path_str, source, known_hashes):
        return canned[path_str]

    async def no_hashes(source):
        return set()

    monkeypatch.setattr(overseer, "_import_one_path", fake_import_one)
    monkeypatch.setattr(overseer, "_already_imported_hashes", no_hashes)

    resp = client.post("/api/overseer/import", json={
        "paths": ["a.jsonl", "b.jsonl", "c.jsonl"],
        "source": "claude-code",
        "skip_already_imported": True,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"requested": 3, "imported": 1,
                              "skipped": 1, "failed": 1}
    assert [r["src"] for r in body["imported"]] == ["a.jsonl"]
    assert [r["src"] for r in body["skipped"]] == ["b.jsonl"]
    assert [r["src"] for r in body["failed"]] == ["c.jsonl"]


def test_import_route_empty_paths(client):
    resp = client.post("/api/overseer/import", json={"paths": []})
    assert resp.status_code == 200
    assert resp.json()["counts"] == {"requested": 0}
