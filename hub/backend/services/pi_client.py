"""HTTP client for the Cortex Pi Zero API."""

import base64
import json as _json

import httpx

from config import settings


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


async def health() -> dict | None:
    """GET /health on the Pi."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.pi_base_url}/health", headers=_headers()
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e), "online": False}


async def send_command(command: str, payload: dict | None = None) -> dict:
    """POST /api/cmd on the Pi."""
    body = {"command": command}
    if payload:
        body["payload"] = payload

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.pi_base_url}/api/cmd",
                json=body,
                headers=_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "Pi request timed out", "online": False}
    except httpx.ConnectError:
        return {"error": "Cannot connect to Pi", "online": False}
    except Exception as e:
        return {"error": str(e)}


async def get_status() -> dict:
    """Get combined Pi status (health + get_status command)."""
    h = await health()
    if h and h.get("error"):
        return h
    status = await send_command("get_status")
    return {
        "health": h,
        "status": status,
        "online": True,
    }


async def pet_ask(prompt: str, poll_timeout: float = 120.0) -> dict:
    """Send pet_ask command, then poll for the actual response.

    pet_ask is async on the Pi: it returns ACK immediately, and the
    inference result arrives later via pet_response.  We poll until we
    get a non-empty response list or the timeout expires.
    """
    import asyncio
    import time

    ack = await send_command("pet_ask", {"prompt": prompt})
    ack_resp = ack.get("response", "")
    if not isinstance(ack_resp, str) or not ack_resp.startswith("ACK:pet_ask:"):
        return ack  # error or unexpected

    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(2.0)
        poll = await send_command("pet_response")
        resp_str = poll.get("response", "")
        if isinstance(resp_str, str) and resp_str.startswith("RSP:pet_response:"):
            try:
                items = _json.loads(resp_str[len("RSP:pet_response:"):])
                if items:
                    latest = items[-1]
                    return {"response": latest.get("response", ""), "data": latest}
            except (ValueError, _json.JSONDecodeError):
                pass

    return {"response": "(no response — inference timed out)", "error": "timeout"}


async def pet_history(limit: int = 20) -> dict:
    """Get pet conversation history."""
    return await send_command("pet_history", {"limit": limit})


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
    """Get pet status (stage, mood, XP). Parses the RSP: protocol response."""
    result = await send_command_parsed("pet_status")
    # Remap for backward compat: existing frontend expects {pet: ...}
    return {"pet": result.get("data"), "error": result.get("error")}


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
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{settings.pi_base_url}/health", headers=_headers()
            )
            return resp.status_code == 200
    except Exception:
        return False
