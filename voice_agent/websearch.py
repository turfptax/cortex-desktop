"""Web lookup for the voice agent + its sub-agents.

Uses an OpenRouter "online" model (the ":online" suffix adds web search to any
model), so it reuses the existing OPENROUTER_KEY with no separate search account.
"""
from __future__ import annotations

import httpx

from . import config as cfg

_SYSTEM = ("You are a concise web research assistant. Answer the query using "
           "current web information in 2 to 4 sentences. Include the key facts, "
           "names, dates, and numbers. Plain prose, no markdown.")


async def web_search(query: str, timeout: float = 45.0) -> str:
    """Return a short, web-grounded answer to `query`."""
    body = {
        "model": cfg.WEB_MODEL,
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": query}],
        "max_tokens": 400,
    }
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(
            f"{cfg.TIER1_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.OPENROUTER_KEY}",
                     "Content-Type": "application/json"},
            json=body)
        r.raise_for_status()
        data = r.json()
    return (data["choices"][0]["message"].get("content") or "").strip()
