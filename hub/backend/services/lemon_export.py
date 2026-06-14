"""Lemon Squeezer dispatch-export connector (2026-06-13).

Cortex Desktop is the egress for the Cortex <-> Lemon Squeezer integration.
Core (Pi) exposes graded sub-agent dispatches read-only at
`/plugins/overseer/dispatch-export`; this connector pulls them and POSTs
to Lemon Squeezer's `/ingest/dispatches`.

The cursor lives HERE (a small state file), not on the Pi: Lemon is
idempotent on `dispatch_id`, so Core stays stateless and at-least-once
delivery is safe. Per the Swarm Board contract: POST first, advance the
cursor only on a 2xx; a transient Lemon outage just re-sends next cycle.

Mirrors services/video_overseer_bridge.py — a self-healing background
poller started/stopped from the app lifespan — plus a `run_once()` the
router exposes for manual triggering.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from config import settings, user_config_path
from services import pi_client

log = logging.getLogger("cortex.hub.lemon")

# Cursor (high-water dispatch id we've shipped) lives next to config.json.
_CURSOR_PATH = user_config_path().parent / "lemon_export_cursor.json"


def read_cursor() -> int:
    try:
        return int(json.loads(_CURSOR_PATH.read_text()).get("cursor", 0))
    except Exception:
        return 0


def _write_cursor(value: int) -> None:
    try:
        _CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CURSOR_PATH.write_text(json.dumps({"cursor": int(value)}))
    except Exception as e:
        log.warning("lemon cursor write failed: %s", e)


async def run_once() -> dict:
    """Pull graded dispatches from the Pi since the cursor, POST them to
    Lemon Squeezer, and advance the cursor ONLY on a 2xx. Idempotent and
    safe to call repeatedly (manual trigger or background loop)."""
    cursor = read_cursor()
    pulled = await pi_client.plugin_call(
        "overseer", "GET", "/dispatch-export",
        {"since": cursor, "limit": 500}, timeout=30.0)
    if not pulled.get("ok"):
        return {"ok": False, "stage": "pull",
                "error": pulled.get("error"), "cursor": cursor}

    dispatches = pulled.get("dispatches") or []
    if not dispatches:
        return {"ok": True, "sent": 0, "cursor": cursor, "note": "nothing new"}

    url = settings.lemon_url.rstrip("/") + "/ingest/dispatches"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json={"dispatches": dispatches})
            resp.raise_for_status()
            body = resp.json()
    except Exception as e:
        # Leave the cursor untouched so this batch re-sends next cycle —
        # a Lemon outage can never lose a graded dispatch.
        return {"ok": False, "stage": "push", "error": str(e),
                "cursor": cursor, "attempted": len(dispatches)}

    new_cursor = max(cursor, int(pulled.get("max_id") or cursor))
    _write_cursor(new_cursor)
    return {"ok": True, "sent": len(dispatches), "cursor": new_cursor,
            "lemon": body}


class LemonExporter:
    """Background poller. No-op unless settings.lemon_export_enabled."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_result: dict | None = None

    def start(self) -> asyncio.Task | None:
        if not settings.lemon_export_enabled:
            log.info("lemon export disabled (lemon_export_enabled=false)")
            return None
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())
        return self._task

    def stop(self) -> None:
        self._stop.set()

    async def _loop(self) -> None:
        interval = max(60, int(settings.lemon_export_interval_s))
        log.info("lemon exporter started (interval=%ss, url=%s)",
                 interval, settings.lemon_url)
        while not self._stop.is_set():
            try:
                self.last_result = await run_once()
                if self.last_result.get("sent"):
                    log.info("lemon export: sent %s dispatches",
                             self.last_result.get("sent"))
                elif not self.last_result.get("ok"):
                    log.warning("lemon export failed: %s", self.last_result)
            except Exception as e:
                log.exception("lemon export loop error: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
