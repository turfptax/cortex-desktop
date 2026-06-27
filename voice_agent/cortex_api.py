"""Async HTTP client to the Cortex Pi for the voice agent's tools.

Centralizes every read/write the agent (and its sub-agents) can do against
cortex-core at cfg.HOST:cfg.PORT. Two endpoint styles are used:
  - /api/cmd           -> {command, payload}; reply is "RSP:<cmd>:<json-or-text>"
  - /plugins/overseer/* -> direct JSON

Everything is async (httpx). Functions return compact, voice-friendly values.
"""
from __future__ import annotations

import json

import httpx

from . import config as cfg

_HEADERS = {"Authorization": cfg.AUTH, "Content-Type": "application/json"}
_BASE = f"http://{cfg.HOST}:{cfg.PORT}"


async def _cmd(command: str, payload: dict | None = None, timeout: float = 10.0):
    """POST the legacy /api/cmd envelope and unwrap the RSP:<cmd>:<body> reply."""
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{_BASE}/api/cmd", headers=_HEADERS,
                         json={"command": command, "payload": payload or {}})
        r.raise_for_status()
        data = r.json()
    if not data.get("ok", True):
        raise RuntimeError(data.get("error", "command failed"))
    resp = data.get("response", "")
    if isinstance(resp, str) and resp.startswith("RSP:"):
        # "RSP:<cmd>:<json-or-text>" — split on the first two colons only.
        body = resp.split(":", 2)[2] if resp.count(":") >= 2 else ""
        try:
            return json.loads(body)
        except Exception:
            return body
    return resp


async def _get(path: str, params: dict | None = None, timeout: float = 20.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(f"{_BASE}{path}", headers=_HEADERS, params=params or {})
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict, timeout: float = 60.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{_BASE}{path}", headers=_HEADERS, json=body)
        r.raise_for_status()
        return r.json()


# ── reads ────────────────────────────────────────────────────────────

async def search_memory(query: str, kinds: str | None = None,
                        limit_total: int = 12) -> list[dict]:
    """Search the interpretive corpus (gists, themes, questions, notes, ...)."""
    params: dict = {"q": query, "limit_total": limit_total}
    if kinds:
        params["kinds"] = kinds
    data = await _get("/plugins/overseer/search", params, timeout=25.0)
    return [{"token": h.get("token"), "kind": h.get("kind"),
             "snippet": (h.get("snippet") or "")[:240]}
            for h in data.get("hits", [])]


async def list_projects(status: str | None = "active", limit: int = 40) -> list[dict]:
    rows = await _cmd("query", {"table": "projects",
                                "filters": {"status": status} if status else {},
                                "limit": limit, "order_by": "last_touched DESC"})
    if not isinstance(rows, list):
        return []
    return [{"tag": p.get("tag"), "name": p.get("name"),
             "status": p.get("status"), "priority": p.get("priority")}
            for p in rows]


async def find_person(query: str, limit: int = 10) -> list[dict]:
    data = await _get("/plugins/overseer/people/search",
                      {"q": query, "limit": limit})
    return [{"id": p.get("id"), "name": p.get("name"),
             "expertise": p.get("areas_of_expertise", "")}
            for p in data.get("people", [])]


async def overseer_chat(question: str, timeout: float = 120.0) -> dict:
    """The deep path: full overseer (Opus, full corpus), spoken-clean reply."""
    data = await _post("/plugins/overseer/chat",
                       {"message": question, "voice_mode": True}, timeout)
    return {"reply": (data.get("reply") or "").strip(),
            "model": str(data.get("model") or "overseer").split("/")[-1]}


# ── writes ───────────────────────────────────────────────────────────

async def save_note(content: str, project: str | None = None,
                   tags: str | None = None, note_type: str = "note") -> dict:
    payload: dict = {"content": content, "type": note_type}
    if project:
        payload["project"] = project
    if tags:
        payload["tags"] = tags
    return await _cmd("note", payload, timeout=8.0)


async def log_activity(details: str, program: str = "voice",
                      project: str | None = None) -> dict:
    payload: dict = {"program": program, "details": details}
    if project:
        payload["project"] = project
    return await _cmd("activity", payload, timeout=8.0)


async def log_time(project: str, minutes: int, description: str | None = None,
                  activity_type: str = "development") -> dict:
    payload: dict = {"project": project, "duration_minutes": int(minutes),
                     "activity_type": activity_type}
    if description:
        payload["description"] = description
    return await _cmd("log_time", payload, timeout=8.0)


async def journal(text: str) -> dict:
    return await _post("/plugins/overseer/human-journal",
                       {"text": text, "entry_type": "free"}, timeout=10.0)
