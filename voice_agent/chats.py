"""Persistent multi-conversation store for the voice agent.

Each chat is a JSON file in %APPDATA%/Cortex/voice-chats/<id>.json:
  {id, title, created_at, updated_at, messages: [{role, content, ts}]}
Plain files, newest-first listing, no DB. The store is pure I/O; the live
context wiring lives in session.py.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


def _dir() -> Path:
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) / "Cortex" if appdata else Path.home() / ".cortex"
    d = root / "voice-chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(cid: str) -> Path:
    return _dir() / f"{cid}.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _write(data: dict) -> None:
    data["updated_at"] = _now()
    with open(_path(data["id"]), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def create(title: str = "") -> dict:
    cid = uuid.uuid4().hex[:12]
    data = {"id": cid, "title": title, "created_at": _now(),
            "updated_at": _now(), "messages": []}
    _write(data)
    return data


def get(cid: str) -> dict | None:
    p = _path(cid)
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def append_turn(cid: str, role: str, content: str) -> None:
    data = get(cid)
    if not data:
        return
    data["messages"].append({"role": role, "content": content, "ts": _now()})
    # Auto-title from the first user line.
    if not data.get("title") and role == "user" and content.strip():
        data["title"] = " ".join(content.split()[:8])[:60]
    _write(data)


def rename(cid: str, title: str) -> None:
    data = get(cid)
    if data:
        data["title"] = title[:80]
        _write(data)


def delete(cid: str) -> None:
    try:
        _path(cid).unlink()
    except OSError:
        pass


def list_chats(limit: int = 50) -> list[dict]:
    out = []
    for p in _dir().glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            out.append({"id": d["id"], "title": d.get("title") or "(untitled)",
                        "updated_at": d.get("updated_at", ""),
                        "turns": len(d.get("messages", []))})
        except Exception:
            pass
    out.sort(key=lambda c: c["updated_at"], reverse=True)
    return out[:limit]


def messages_for_context(cid: str) -> list[dict]:
    """The chat's user/assistant turns as plain LLM context dicts."""
    d = get(cid)
    if not d:
        return []
    return [{"role": m["role"], "content": m["content"]}
            for m in d.get("messages", [])
            if m.get("role") in ("user", "assistant") and m.get("content")]
