"""Router-level tests for the overseer proxy (Phase 2 of the redesign).

Mounts ONLY the overseer router on a bare FastAPI app (main.app's
lifespan would start real plugin sidecars) and monkeypatches
pi_client.plugin_call, so these tests verify the router wiring:
route -> method -> Pi path -> payload mapping.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import overseer
from services import pi_client


@pytest.fixture
def proxy(monkeypatch):
    """Capture plugin_call invocations; return a canned response."""
    calls = []

    async def fake_plugin_call(plugin, method, route,
                               payload=None, timeout=30.0):
        calls.append({"plugin": plugin, "method": method,
                      "route": route, "payload": payload})
        return {"ok": True, "fake": True}

    monkeypatch.setattr(pi_client, "plugin_call", fake_plugin_call)
    # The router module imported pi_client as a module, so patching
    # the module attribute reaches it.
    return calls


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(overseer.router, prefix="/api/overseer")
    return TestClient(app)


def test_status_proxies_to_pi(client, proxy):
    resp = client.get("/api/overseer/status")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "fake": True}
    assert proxy[0]["plugin"] == "overseer"
    assert proxy[0]["method"] == "GET"
    assert proxy[0]["route"] == "/status"


def test_working_memory_forwards_rebuild_param(client, proxy):
    client.get("/api/overseer/working-memory?rebuild=1")
    assert proxy[0]["route"] == "/working-memory"
    assert proxy[0]["payload"] == {"rebuild": 1}


def test_post_body_reaches_pi(client, proxy):
    resp = client.post("/api/overseer/projects/setting",
                       json={"project": "cortex", "treat_as": "human"})
    assert resp.status_code == 200
    assert proxy[0]["method"] == "POST"
    assert proxy[0]["route"] == "/projects/setting"
    assert proxy[0]["payload"]["project"] == "cortex"


def test_pi_error_shape_passes_through(client, monkeypatch):
    async def offline_plugin_call(*args, **kwargs):
        return {"ok": False, "error": "Cannot connect to Pi"}

    monkeypatch.setattr(pi_client, "plugin_call", offline_plugin_call)
    app = FastAPI()
    app.include_router(overseer.router, prefix="/api/overseer")
    resp = TestClient(app).get("/api/overseer/status")
    # Router degrades gracefully: 200 with the error envelope, the
    # frontend renders the offline state from it.
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "Cannot connect to Pi"}
