"""The voice agent's toolbelt: pipecat FunctionSchemas + handlers.

Each handler logs a tool_call + tool_result to the activity monitor (so every
action is visible next to the playground), wires to cortex_api / websearch /
subagent, and replies via params.result_callback. register_all() binds them onto
the pipeline LLM; ALL_SCHEMAS is the tool list handed to the model context.
"""
from __future__ import annotations

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from . import cortex_api, subagent, websearch
from .activity import record


# ── handlers ─────────────────────────────────────────────────────────

async def _h_search_memory(params: FunctionCallParams) -> None:
    a = params.arguments or {}
    q = a.get("query", "")
    record("tool_call", name="search_memory", detail=q[:120])
    try:
        hits = await cortex_api.search_memory(q, a.get("kinds"))
        record("tool_result", name="search_memory", ok=True, detail=f"{len(hits)} hits")
        await params.result_callback({"hits": hits})
    except Exception as e:
        logger.exception("search_memory failed")
        record("tool_result", name="search_memory", ok=False, detail=str(e)[:80])
        await params.result_callback({"error": "search failed"})


async def _h_ask_overseer(params: FunctionCallParams) -> None:
    q = (params.arguments or {}).get("question", "")
    record("tool_call", name="ask_overseer", detail=q[:120])
    try:
        res = await cortex_api.overseer_chat(q)
        record("tool_result", name="ask_overseer", ok=True,
               detail=res["reply"][:120], answered_by=res["model"])
        await params.result_callback({"answer": res["reply"] or "No answer found."})
    except Exception as e:
        logger.exception("ask_overseer failed")
        record("tool_result", name="ask_overseer", ok=False, detail=str(e)[:80])
        await params.result_callback(
            {"answer": "I could not reach your memory just now."})


async def _h_web_search(params: FunctionCallParams) -> None:
    q = (params.arguments or {}).get("query", "")
    record("tool_call", name="web_search", detail=q[:120])
    try:
        ans = await websearch.web_search(q)
        record("tool_result", name="web_search", ok=True, detail=ans[:120])
        await params.result_callback({"answer": ans or "No result."})
    except Exception as e:
        logger.exception("web_search failed")
        record("tool_result", name="web_search", ok=False, detail=str(e)[:80])
        await params.result_callback({"answer": "Web search failed just now."})


async def _h_save_note(params: FunctionCallParams) -> None:
    a = params.arguments or {}
    content = a.get("content", "")
    record("tool_call", name="save_note", detail=content[:120])
    try:
        await cortex_api.save_note(content, a.get("project"), a.get("tags"))
        record("tool_result", name="save_note", ok=True, detail="saved")
        await params.result_callback({"ok": True})
    except Exception as e:
        logger.exception("save_note failed")
        record("tool_result", name="save_note", ok=False, detail=str(e)[:80])
        await params.result_callback({"ok": False, "error": "could not save note"})


async def _h_log_activity(params: FunctionCallParams) -> None:
    a = params.arguments or {}
    record("tool_call", name="log_activity", detail=(a.get("details") or "")[:120])
    try:
        await cortex_api.log_activity(a.get("details", ""), project=a.get("project"))
        record("tool_result", name="log_activity", ok=True, detail="logged")
        await params.result_callback({"ok": True})
    except Exception as e:
        logger.exception("log_activity failed")
        record("tool_result", name="log_activity", ok=False, detail=str(e)[:80])
        await params.result_callback({"ok": False, "error": "could not log activity"})


async def _h_log_time(params: FunctionCallParams) -> None:
    a = params.arguments or {}
    record("tool_call", name="log_time",
           detail=f"{a.get('project','')} {a.get('minutes','')}m")
    try:
        await cortex_api.log_time(a.get("project", ""), a.get("minutes", 0),
                                  a.get("description"))
        record("tool_result", name="log_time", ok=True, detail="logged")
        await params.result_callback({"ok": True})
    except Exception as e:
        logger.exception("log_time failed")
        record("tool_result", name="log_time", ok=False, detail=str(e)[:80])
        await params.result_callback({"ok": False, "error": "could not log time"})


async def _h_journal(params: FunctionCallParams) -> None:
    text = (params.arguments or {}).get("text", "")
    record("tool_call", name="journal", detail=text[:120])
    try:
        await cortex_api.journal(text)
        record("tool_result", name="journal", ok=True, detail="saved")
        await params.result_callback({"ok": True})
    except Exception as e:
        logger.exception("journal failed")
        record("tool_result", name="journal", ok=False, detail=str(e)[:80])
        await params.result_callback({"ok": False, "error": "could not save journal"})


async def _h_list_projects(params: FunctionCallParams) -> None:
    a = params.arguments or {}
    record("tool_call", name="list_projects", detail=a.get("status", "active"))
    try:
        projs = await cortex_api.list_projects(a.get("status", "active"))
        record("tool_result", name="list_projects", ok=True, detail=f"{len(projs)}")
        await params.result_callback({"projects": projs})
    except Exception as e:
        logger.exception("list_projects failed")
        record("tool_result", name="list_projects", ok=False, detail=str(e)[:80])
        await params.result_callback({"error": "could not list projects"})


async def _h_find_person(params: FunctionCallParams) -> None:
    q = (params.arguments or {}).get("query", "")
    record("tool_call", name="find_person", detail=q[:120])
    try:
        ppl = await cortex_api.find_person(q)
        record("tool_result", name="find_person", ok=True, detail=f"{len(ppl)}")
        await params.result_callback({"people": ppl})
    except Exception as e:
        logger.exception("find_person failed")
        record("tool_result", name="find_person", ok=False, detail=str(e)[:80])
        await params.result_callback({"error": "could not find person"})


async def _h_dispatch_agent(params: FunctionCallParams) -> None:
    a = params.arguments or {}
    task = a.get("task", "")
    depth = a.get("depth", "deep")
    record("tool_call", name="dispatch_agent", detail=f"[{depth}] {task[:100]}")
    try:
        res = subagent.dispatch(task, depth)
        record("tool_result", name="dispatch_agent", ok=True,
               detail=f"started #{res['id']} ({res['model']})")
        await params.result_callback({
            "started": True, "id": res["id"], "model": res["model"],
            "note": "Working in the background. Tell the user you started it "
                    f"(agent #{res['id']}); they can ask later what it found."})
    except Exception as e:
        logger.exception("dispatch_agent failed")
        record("tool_result", name="dispatch_agent", ok=False, detail=str(e)[:80])
        await params.result_callback({"error": "could not start sub-agent"})


async def _h_check_agents(params: FunctionCallParams) -> None:
    record("tool_call", name="check_agents", detail="")
    tasks = subagent.list_tasks()
    record("tool_result", name="check_agents", ok=True, detail=f"{len(tasks)} tasks")
    await params.result_callback({"tasks": [
        {"id": t["id"], "status": t["status"], "task": t["task"][:100],
         "result": t["result"][:300]} for t in tasks]})


# ── schemas ──────────────────────────────────────────────────────────

_STR = {"type": "string"}

ALL_SCHEMAS = [
    FunctionSchema(
        name="search_memory",
        description="Search the user's Cortex memory (notes, gists, themes, open "
                    "questions, project rollups). Use for 'what did I...', "
                    "'find my...', recent work.",
        properties={"query": _STR,
                    "kinds": {"type": "string",
                              "description": "optional comma-separated filter: "
                              "gist,theme,question,note,journal,narrative,pattern"}},
        required=["query"]),
    FunctionSchema(
        name="ask_overseer",
        description="Ask the deep memory agent (slower, smarter) anything that "
                    "needs synthesis across the user's whole history.",
        properties={"question": _STR},
        required=["question"]),
    FunctionSchema(
        name="web_search",
        description="Look up current or online information (news, docs, facts you "
                    "don't have). Returns a short web-grounded answer.",
        properties={"query": _STR},
        required=["query"]),
    FunctionSchema(
        name="save_note",
        description="Save a note the user wants remembered, in their Cortex memory.",
        properties={"content": _STR,
                    "project": {"type": "string", "description": "optional project tag"},
                    "tags": {"type": "string", "description": "optional comma-separated tags"}},
        required=["content"]),
    FunctionSchema(
        name="log_activity",
        description="Record what the user is working on right now.",
        properties={"details": _STR,
                    "project": {"type": "string", "description": "optional project tag"}},
        required=["details"]),
    FunctionSchema(
        name="log_time",
        description="Log time spent on a project.",
        properties={"project": _STR,
                    "minutes": {"type": "integer"},
                    "description": {"type": "string"}},
        required=["project", "minutes"]),
    FunctionSchema(
        name="journal",
        description="Save a longer free-form journal reflection for the user.",
        properties={"text": _STR},
        required=["text"]),
    FunctionSchema(
        name="list_projects",
        description="List the user's projects.",
        properties={"status": {"type": "string",
                               "description": "active (default), archived, paused, completed"}},
        required=[]),
    FunctionSchema(
        name="find_person",
        description="Look someone up in the user's contacts/relationship memory.",
        properties={"query": _STR},
        required=["query"]),
    FunctionSchema(
        name="dispatch_agent",
        description="Hand a bigger job (research, multi-step lookup) to a background "
                    "sub-agent that works while you keep talking. Tell the user you "
                    "started it.",
        properties={"task": {"type": "string", "description": "the full task"},
                    "depth": {"type": "string",
                              "description": "quick (simple), deep (default), or "
                              "max (hardest only)"}},
        required=["task"]),
    FunctionSchema(
        name="check_agents",
        description="Report the status and results of recent background sub-agents.",
        properties={},
        required=[]),
]

_HANDLERS = {
    "search_memory": _h_search_memory,
    "ask_overseer": _h_ask_overseer,
    "web_search": _h_web_search,
    "save_note": _h_save_note,
    "log_activity": _h_log_activity,
    "log_time": _h_log_time,
    "journal": _h_journal,
    "list_projects": _h_list_projects,
    "find_person": _h_find_person,
    "dispatch_agent": _h_dispatch_agent,
    "check_agents": _h_check_agents,
}


def register_all(llm) -> None:
    """Bind every tool handler onto the pipeline LLM service."""
    for name, fn in _HANDLERS.items():
        llm.register_function(name, fn)
