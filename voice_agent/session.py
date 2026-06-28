"""Live voice-session coordination.

Binds the running pipecat LLMContext to the chat store so "new chat" and
"resume" swap the conversation IN PLACE (via context.set_messages) with no
WebRTC disconnect/reconnect. bot.py binds the fresh context on each connection
and records each turn here; the monitor's control endpoints call new_chat /
activate / list.
"""
from __future__ import annotations

from loguru import logger

from . import chats
from . import config as cfg

_ctx = None        # live LLMContext (rebound on each connection)
_active_id = None  # active chat id


def bind(context) -> None:
    """bot.py calls this for each new session with its fresh context."""
    global _ctx
    _ctx = context


def active_id() -> str | None:
    return _active_id


def _reset_context(messages: list[dict]) -> None:
    if _ctx is None:
        return
    system = {"role": "system", "content": cfg.build_system_prompt()}
    _ctx.set_messages([system] + messages)


def ensure_active() -> str:
    global _active_id
    if _active_id is None:
        _active_id = chats.create()["id"]
    return _active_id


def active_has_history() -> bool:
    return bool(_active_id) and len(chats.messages_for_context(_active_id)) > 0


def load_active_into_context() -> None:
    """On a new connection, load the active chat's history into the live context
    so a reload/reconnect resumes where we left off."""
    if _active_id is None:
        ensure_active()
        return
    _reset_context(chats.messages_for_context(_active_id))


def new_chat() -> dict:
    """Start a fresh conversation in place (no reconnect)."""
    global _active_id
    d = chats.create()
    _active_id = d["id"]
    _reset_context([])
    logger.info(f"[session] new chat {_active_id}")
    return {"ok": True, "id": _active_id, "title": d["title"]}


def activate(chat_id: str) -> dict:
    """Resume a saved conversation in place (no reconnect)."""
    global _active_id
    d = chats.get(chat_id)
    if not d:
        return {"ok": False, "error": "chat not found"}
    _active_id = chat_id
    _reset_context(chats.messages_for_context(chat_id))
    logger.info(f"[session] resumed chat {chat_id}")
    return {"ok": True, "id": chat_id, "title": d.get("title")}


def record(role: str, content: str) -> None:
    """Autosave a live turn into the active chat."""
    if not (content or "").strip():
        return
    chats.append_turn(ensure_active(), role, content)
