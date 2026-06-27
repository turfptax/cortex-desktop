"""In-process sub-agents for the voice agent.

dispatch() spins up a background asyncio task that runs a small tool-using loop on
an OpenRouter model (tiered: quick=Flash, deep=Sonnet, max=Opus), bounded by a
per-task USD cap and a step cap. Each step is streamed to the activity monitor so
the work is visible, and when the task finishes its result is announced back into
the conversation (non-intrusively) so the voice agent can relay it.

This is the "spool up a sub agent" path: it actually runs the work itself (web +
memory tools), unlike the Cortex sibling queue which needs an external claimant.
"""
from __future__ import annotations

import asyncio
import itertools
import json

import httpx
from loguru import logger

from . import config as cfg
from . import cortex_api, websearch
from .activity import record

_ids = itertools.count(1)
TASKS: dict[int, dict] = {}
_announce = None  # set by bot: callable(text:str) -> None

# Tools the sub-agent itself may call (OpenAI tool-call schema).
_SUB_TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Look up current information on the web.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "Search the user's Cortex memory (notes, gists, themes, "
                       "questions, projects).",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
]

_MODELS = {"quick": cfg.SUBAGENT_QUICK_MODEL,
           "deep": cfg.SUBAGENT_DEEP_MODEL,
           "max": cfg.SUBAGENT_MAX_MODEL}

_SYSTEM = ("You are a focused research/work sub-agent for a voice assistant. Use "
           "the tools to gather what you need, then give a SHORT spoken-friendly "
           "answer (2 to 4 sentences, key facts/names/numbers, no markdown). Be "
           "decisive; do not over-search.")


def set_announcer(fn) -> None:
    """bot.py provides a callback that surfaces a finished task into the chat."""
    global _announce
    _announce = fn


def list_tasks(limit: int = 8) -> list[dict]:
    items = sorted(TASKS.values(), key=lambda t: t["id"], reverse=True)[:limit]
    return [{"id": t["id"], "task": t["task"], "status": t["status"],
             "result": t["result"], "model": t["model"].split("/")[-1],
             "steps": t["steps"], "cost": t["cost"]} for t in items]


async def _exec_tool(name: str, args: dict) -> str:
    if name == "web_search":
        return await websearch.web_search(args.get("query", ""))
    if name == "search_memory":
        hits = await cortex_api.search_memory(args.get("query", ""))
        return json.dumps(hits)[:1500]
    return f"unknown tool {name}"


async def _or_chat(model: str, messages: list, timeout: float = 90.0) -> dict:
    body = {"model": model, "messages": messages, "tools": _SUB_TOOLS,
            "max_tokens": 700, "usage": {"include": True}}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(
            f"{cfg.TIER1_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.OPENROUTER_KEY}",
                     "Content-Type": "application/json"},
            json=body)
        r.raise_for_status()
        return r.json()


async def _run(task: dict) -> None:
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": task["task"]}]
    cost = 0.0
    try:
        for _ in range(cfg.SUBAGENT_MAX_STEPS):
            if cost >= cfg.SUBAGENT_COST_CAP:
                task["steps"].append("cost cap reached")
                record("task_step", id=task["id"], step="cost cap reached")
                break
            data = await _or_chat(task["model"], messages)
            cost += float((data.get("usage") or {}).get("cost", 0) or 0)
            msg = data["choices"][0]["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                task["result"] = (msg.get("content") or "").strip()
                break
            for tc in calls:
                fn = tc["function"]["name"]
                try:
                    a = json.loads(tc["function"].get("arguments") or "{}")
                except Exception:
                    a = {}
                label = f"{fn}: {str(a.get('query', ''))[:48]}"
                task["steps"].append(label)
                record("task_step", id=task["id"], step=label)
                result = await _exec_tool(fn, a)
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "content": str(result)[:2000]})
        if not task["result"]:
            task["result"] = "(no conclusion within the step/cost budget)"
        task["status"] = "done"
        task["cost"] = round(cost, 4)
        record("task_done", id=task["id"], result=task["result"][:300],
               cost=task["cost"])
        if _announce:
            try:
                _announce(f"[Background sub-agent #{task['id']} finished. When it "
                          f"next fits, tell the user briefly: {task['result']}]")
            except Exception:
                logger.exception("announce failed")
    except Exception as e:
        logger.exception("sub-agent failed")
        task["status"] = "error"
        task["result"] = f"error: {e}"
        task["cost"] = round(cost, 4)
        record("task_done", id=task["id"], result=task["result"], cost=task["cost"])


def dispatch(task_text: str, depth: str = "deep") -> dict:
    """Start a background sub-agent. Returns immediately with its id + model."""
    tid = next(_ids)
    model = _MODELS.get(depth, cfg.SUBAGENT_DEEP_MODEL)
    task = {"id": tid, "task": task_text, "status": "running", "result": "",
            "steps": [], "model": model, "cost": 0.0}
    TASKS[tid] = task
    record("task_start", id=tid, task=task_text[:200], model=model.split("/")[-1])
    asyncio.create_task(_run(task))
    return {"id": tid, "status": "running", "model": model.split("/")[-1]}
