"""HTTP client for the Cortex Pi Zero API."""

import base64
import json as _json

import httpx

from config import settings

# Shared connection pool. The Hub polls the Pi constantly (status
# every 15s from App.tsx, the overseer page every 30s), and the old
# client-per-request pattern paid TCP setup/teardown on every single
# call. One pooled client reuses connections; the per-call timeout
# still varies per request. Closed by the app lifespan on shutdown.
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use.

    Tests can inject their own (e.g. with httpx.MockTransport) by
    assigning to pi_client._client directly.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient()
    return _client


async def aclose_client() -> None:
    """Close the shared client (called from the app lifespan)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _basic_auth_header() -> str:
    """Build HTTP Basic Auth header value from configured credentials."""
    credentials = f"{settings.pi_username}:{settings.pi_password}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.pi_username and settings.pi_password:
        h["Authorization"] = _basic_auth_header()
    return h


# Phone-bridge ask 1 (2026-06-10): the Pi is OPTIONAL. An empty
# pi_host means "no Pi configured" (Azure pivot; the app must run
# with only the dongle and/or the Gateway). Every Pi call returns a
# fast, consistent not-configured error instead of a network timeout.
_NOT_CONFIGURED = "Pi not configured"


def pi_configured() -> bool:
    return bool(settings.pi_host)


async def health() -> dict | None:
    """GET /health on the Pi."""
    if not pi_configured():
        return {"error": _NOT_CONFIGURED, "online": False}
    try:
        resp = await get_client().get(
            f"{settings.pi_base_url}/health", headers=_headers(),
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "online": False}


async def send_command(command: str, payload: dict | None = None) -> dict:
    """POST /api/cmd on the Pi."""
    if not pi_configured():
        return {"error": _NOT_CONFIGURED, "online": False}
    body = {"command": command}
    if payload:
        body["payload"] = payload

    try:
        resp = await get_client().post(
            f"{settings.pi_base_url}/api/cmd",
            json=body,
            headers=_headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        return {"error": "Pi request timed out", "online": False}
    except httpx.ConnectError:
        return {"error": "Cannot connect to Pi", "online": False}
    except Exception as e:
        return {"error": str(e)}


async def plugin_call(
    plugin: str,
    method: str,
    route: str,
    payload: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    """Call a plugin route on the Pi.

    Hits /plugins/<plugin><route> with HTTP Basic Auth. For GET, payload
    becomes URL query params. For POST/PUT/DELETE, payload becomes the
    JSON body. Returns the plugin handler's dict directly: {ok, ...fields}
    on success, {ok: false, error: "..."} on transport failure.

    Slice 2c2c2 — replaces send_command_parsed("pet_*") for the 23 pet
    routes after slice 2c2c1 moved them out of the legacy CMD: protocol.
    """
    if not pi_configured():
        return {"ok": False, "error": _NOT_CONFIGURED}
    method = method.upper()
    url = f"{settings.pi_base_url}/plugins/{plugin}{route}"
    try:
        kwargs = {"headers": _headers(), "timeout": timeout}
        if method == "GET" and payload:
            kwargs["params"] = payload
        elif payload is not None:
            kwargs["json"] = payload
        resp = await get_client().request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        return {"ok": False, "error": "Pi request timed out"}
    except httpx.ConnectError:
        return {"ok": False, "error": "Cannot connect to Pi"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def to_legacy_shape(plugin_response: dict) -> dict:
    """Adapt {ok: true, ...fields} -> {data: {...fields}, error: None}.

    Matches the shape send_command_parsed returned so the existing
    frontend (Pi page, etc.) keeps working without any React/TS changes.
    """
    if plugin_response.get("ok"):
        data = {k: v for k, v in plugin_response.items() if k != "ok"}
        return {"data": data, "error": None}
    return {"data": None, "error": plugin_response.get("error", "unknown")}


async def get_status() -> dict:
    """Get combined Pi status (health + get_status command)."""
    h = await health()
    if h and h.get("error"):
        return h
    result = await send_command_parsed("status")
    return {
        "health": h,
        "status": result.get("data") or {},
        "online": True,
    }


async def pet_ask(prompt: str, poll_timeout: float = 120.0) -> dict:
    """POST /plugins/pet/chat then poll /plugins/pet/responses for the result.

    Pet inference is async on the Pi: /chat returns ACK with an interaction
    ID, the response arrives later via /responses. We poll until we get our
    specific response or the timeout expires.

    Slice 2c2c2 — was previously send_command("pet_ask")+poll on pet_response.
    """
    import asyncio
    import time

    ack = await plugin_call("pet", "POST", "/chat", {"prompt": prompt})
    if not ack.get("ok"):
        return {
            "response": "(failed to send to pet)",
            "error": ack.get("error", "unknown"),
        }
    interaction_id = ack.get("interaction_id", 0) or 0
    since_id = max(interaction_id - 1, 0)

    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(2.0)
        poll = await plugin_call(
            "pet", "GET", "/responses", {"since_id": since_id}, timeout=10.0
        )
        if not poll.get("ok"):
            continue
        items = poll.get("responses", []) or []
        # Find our specific response by interaction ID
        for item in items:
            if item.get("id") == interaction_id:
                return {"response": item.get("response", ""), "data": item}
        # If no exact match but items exist, return the latest
        if items:
            latest = items[-1]
            return {"response": latest.get("response", ""), "data": latest}

    return {"response": "(no response — inference timed out)", "error": "timeout"}


async def pet_history(limit: int = 20) -> dict:
    """GET /plugins/pet/history?limit=N (returns legacy shape for compat)."""
    raw = await plugin_call("pet", "GET", "/history", {"limit": limit})
    return to_legacy_shape(raw)


async def send_command_parsed(
    command: str, payload: dict | None = None
) -> dict:
    """Send a command and parse the RSP:/ACK: protocol response generically.

    The Pi returns {"ok": true, "response": "RSP:<command>:<json>"}
    for queries and "ACK:<command>:<json>" for actions.
    This extracts and parses the JSON payload from either format.
    """
    raw = await send_command(command, payload)
    resp_str = raw.get("response", "")

    # Try both RSP: (query response) and ACK: (action acknowledgment)
    for prefix_type in ("RSP", "ACK"):
        prefix = f"{prefix_type}:{command}:"
        if isinstance(resp_str, str) and resp_str.startswith(prefix):
            try:
                parsed = _json.loads(resp_str[len(prefix):])
                return {"data": parsed}
            except (ValueError, _json.JSONDecodeError):
                pass

    # Fallback: might be an error or unexpected format
    if raw.get("error"):
        return {"data": None, "error": raw["error"]}
    return {"data": None, "error": f"Unexpected response for {command}"}


async def pet_status() -> dict:
    """GET /plugins/pet/status — remapped to {pet: ...} for frontend compat."""
    raw = await plugin_call("pet", "GET", "/status")
    if raw.get("ok"):
        # The new endpoint returns engine_loaded/heartbeat_running/stats etc;
        # the existing frontend reads pet_status's pet.X fields, which match
        # the keys inside `stats`. Surface stats under "pet".
        return {"pet": raw.get("stats"), "error": None}
    return {"pet": None, "error": raw.get("error", "unknown")}


async def send_note(
    content: str,
    tags: str = "",
    project: str = "",
    note_type: str = "note",
) -> dict:
    """Send a note to the Pi."""
    return await send_command(
        "note",
        {
            "content": content,
            "tags": tags,
            "project": project,
            "note_type": note_type,
        },
    )


async def query_table(
    table: str,
    filters: str = "",
    limit: int = 20,
    order_by: str = "created_at DESC",
) -> dict:
    """Query a table on the Pi."""
    return await send_command(
        "query",
        {
            "table": table,
            "filters": filters,
            "limit": limit,
            "order_by": order_by,
        },
    )


async def table_counts() -> dict:
    """Get row counts for all tables."""
    return await send_command_parsed("table_counts")


async def upsert_record(table: str, data: dict) -> dict:
    """Upsert a row in a Pi database table."""
    return await send_command_parsed("upsert", {"table": table, "data": data})


async def delete_record(table: str, row_id) -> dict:
    """Delete a row from a Pi database table."""
    return await send_command_parsed("delete", {"table": table, "id": row_id})


async def check_online() -> bool:
    """Quick check if Pi is reachable."""
    if not pi_configured():
        return False
    try:
        resp = await get_client().get(
            f"{settings.pi_base_url}/health", headers=_headers(),
            timeout=3.0,
        )
        return resp.status_code == 200
    except Exception:
        return False
