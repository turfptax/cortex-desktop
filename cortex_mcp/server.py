"""Cortex MCP Server.

Serves the Cortex corpus (the cloud core, reached through the gateway's
authenticated /core proxy) as MCP tools for AI agents. Transport is a
single HTTP bridge (wifi_bridge.WiFiBridge) whose base URL comes from
%APPDATA%/Cortex/config.json (pi_host may be a full https URL).

Run with:
    cortex-mcp          (installed entry point)
    python -m cortex_mcp.server  (direct invocation)
"""

import json
import os
import socket
import platform

from mcp.server.fastmcp import FastMCP

from cortex_mcp.protocol import send_command


def _get_bridge():
    """Single transport: the HTTP bridge to the Cortex core (cloud or
    legacy Pi host, per config)."""
    from cortex_mcp.wifi_bridge import WiFiBridge
    return WiFiBridge()


# Lazy singleton bridge instance
_bridge = None


def _reset_bridge():
    """Reset the bridge so the next call rebuilds it (e.g. after a
    config change)."""
    global _bridge
    _bridge = None


def _get_bridge_lazy():
    """Get or initialize the bridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = _get_bridge()
    return _bridge


# MCP server
mcp = FastMCP(
    "Cortex",
    instructions=(
        "Cortex is the user's permanent AI memory system. The core runs "
        "in the cloud (an Azure Container App reached through the "
        "gateway's authenticated /core proxy) and stores notes, "
        "sessions, activities, and files in SQLite, continuously "
        "replicated to Blob storage.\n\n"

        "TRANSPORT: a single HTTPS bridge to the configured core URL "
        "(Settings > Cortex Cloud). Use connection_info to check "
        "reachability.\n\n"

        "RECOMMENDED WORKFLOW:\n"
        "1. Call get_context first -- returns active projects, recent sessions, "
        "pending reminders, open bugs, recent files, and DB stats.\n"
        "2. Call session_start to register this conversation.\n"
        "3. Use tools as needed during the session.\n"
        "4. Call session_end with a summary before the conversation ends.\n\n"

        "CAPABILITIES:\n"
        "- Notes: send_note, note_update, notes_search\n"
        "- Projects: project_upsert, project_list\n"
        "- Sessions: session_start/session_end\n"
        "- Activities: log_activity, log_time\n"
        "- Searches: log_search\n"
        "- Database: query, upsert_row, delete_row, table_counts\n"
        "- Files: file_register, file_list, file_search, "
        "file_upload, file_download (WiFi only)\n"
        "- Audit: audit_projects, audit_notes, audit_data_quality, weekly_review\n"
        "- Tech knowledge: cortex_skills, cortex_skill_log, "
        "cortex_rules, cortex_rule_add (the user's living skills "
        "portfolio + standing tech rules; log lessons and hard-won "
        "defaults as they emerge in your work so other AI sessions "
        "learn from them)\n"
        "- Diagnostics: ping, get_status, connection_info\n\n"

        "WEEKLY REVIEW WORKFLOW:\n"
        "1. Call weekly_review for a full database health report.\n"
        "2. Review stale projects — update status with project_upsert or "
        "archive inactive ones.\n"
        "3. Triage untagged notes — use note_update to add tags and projects.\n"
        "4. Check data quality with audit_data_quality.\n"
        "5. Ask the user questions about projects and log decisions as notes.\n\n"

        "FILE OPERATIONS: Files on the core are organized by category: "
        "recordings, notes, logs, uploads. Use file_upload to send a file "
        "from this computer (auto-registers in DB) and file_download to "
        "retrieve; file_register records metadata for files already there."
    ),
)


@mcp.tool()
def ping() -> str:
    """Ping the Pi Zero to test round-trip connectivity.

    Sends CMD:ping through the ESP32 BLE bridge and waits for CMD:pong.
    Use this to verify the full chain: Computer -> ESP32 -> BLE -> Pi.
    """
    try:
        return send_command(_get_bridge_lazy(), "ping", timeout=5)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def get_status() -> str:
    """Get the Pi Zero's current status.

    Returns uptime, connection info, storage stats, and recording state.
    """
    try:
        return send_command(_get_bridge_lazy(), "status", timeout=5)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def send_note(content: str, tags: str = "", project: str = "", note_type: str = "note") -> str:
    """Send a text note to the Pi Zero for storage.

    Notes are timestamped and stored on the Pi's SD card for future analysis.
    Notes of any length are supported -- the transport handles chunking automatically.

    Args:
        content: The note text to store.
        tags: Optional comma-separated tags for categorization
              (e.g. "idea,project,urgent").
        project: Optional project tag (e.g. "cortex", "employer").
        note_type: Note type: note, decision, bug, reminder, idea, todo, context.
    """
    try:
        payload = {"content": content}
        if tags:
            payload["tags"] = tags
        if project:
            payload["project"] = project
        if note_type and note_type != "note":
            payload["type"] = note_type
        return send_command(_get_bridge_lazy(), "note", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def log_activity(program: str, details: str = "", file_path: str = "", project: str = "") -> str:
    """Log what the user is currently working on.

    Records the program, optional file path, and details to the Pi for
    building an activity timeline.

    Args:
        program: Program name (e.g. "VS Code", "Chrome", "Terminal").
        details: Optional description of the activity.
        file_path: Optional file path being worked on.
        project: Optional project tag.
    """
    try:
        payload = {"program": program}
        if details:
            payload["details"] = details
        if file_path:
            payload["file_path"] = file_path
        if project:
            payload["project"] = project
        return send_command(_get_bridge_lazy(), "activity", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def log_time(
    project: str,
    duration_minutes: int,
    description: str = "",
    activity_type: str = "development",
    date: str = "",
    project_name: str = "",
    org_tag: str = "",
) -> str:
    """Log a time entry for work done on a project.

    Creates a time entry and automatically creates the project if it doesn't
    exist yet. Use this to log work after a session — estimate duration from
    the conversation context.

    Args:
        project: Project tag (e.g. "cortex-desktop", "employer"). Auto-creates if missing.
        duration_minutes: Estimated duration in minutes.
        description: Brief description of the work done.
        activity_type: Type of work: development, bugfix, research, documentation,
                      devops, meeting, design, testing, planning.
        date: Approximate date/time as ISO string (e.g. "2026-04-02T14:00:00").
              Defaults to now if omitted.
        project_name: Friendly name for new projects (e.g. "Cortex Desktop").
                     Defaults to project tag if omitted.
        org_tag: Optional organization tag.
    """
    try:
        payload = {
            "project": project,
            "duration_minutes": duration_minutes,
        }
        if description:
            payload["description"] = description
        if activity_type and activity_type != "development":
            payload["activity_type"] = activity_type
        if date:
            payload["date"] = date
        if project_name:
            payload["project_name"] = project_name
        if org_tag:
            payload["org_tag"] = org_tag
        return send_command(_get_bridge_lazy(), "log_time", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def log_search(query: str, url: str = "", source: str = "web", project: str = "") -> str:
    """Log a web search or research query.

    Records searches for building a research history on the Pi.

    Args:
        query: The search query text.
        url: Optional URL of the search or result page.
        source: Search engine or source (e.g. "google", "github", "stackoverflow").
        project: Optional project tag.
    """
    try:
        payload = {"query": query, "source": source}
        if url:
            payload["url"] = url
        if project:
            payload["project"] = project
        return send_command(_get_bridge_lazy(), "search", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def session_start(ai_platform: str = "claude") -> str:
    """Start a new Cortex session.

    Call this at the beginning of a conversation to register the session
    with Cortex Core. Returns a session_id for use in subsequent calls.

    Args:
        ai_platform: The AI platform name (e.g. "claude", "chatgpt").
    """
    try:
        payload = {
            "ai_platform": ai_platform,
            "hostname": socket.gethostname(),
            "os_info": "{} {}".format(platform.system(), platform.release()),
        }
        return send_command(_get_bridge_lazy(), "session_start", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def session_end(session_id: str, summary: str, projects: str = "") -> str:
    """End a Cortex session.

    Call this before a conversation ends to record what was accomplished.

    Args:
        session_id: The session ID from session_start.
        summary: Brief summary of what was accomplished in this session.
        projects: Comma-separated project tags that were touched.
    """
    try:
        payload = {
            "session_id": session_id,
            "summary": summary,
        }
        if projects:
            payload["projects"] = projects
        return send_command(_get_bridge_lazy(), "session_end", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def get_context() -> str:
    """Get the FULL working_memory + composite system state. Heavy.

    **For new AI sessions: prefer `cortex_intro` instead** — it's the
    curated 30-second brief about Tory (who he is, what he's working
    on, what he's thinking about) that's actually useful at session
    boot. `get_context` returns 29 fields including a lot of
    overseer-internal operational state (queue depths, sibling
    counters, gist source distribution, automation rollups) most
    consumers don't need.

    Use `get_context` when you specifically need to reason about the
    Cortex system's plumbing — overseer's own queue, journal, import
    backlog, rollups, etc. Otherwise use `cortex_intro`.

    Working memory items carry `token` fields like "q:42" (question),
    "p:5" (pattern), "d:1" (drift), "g:75" (gist), "r:18" (rollup),
    "n:1" (future-overseer note), "t:5" (theme), "b:7" (blindspot),
    "j:1" (journal entry), "e:3" (episode), "dial:2" (dialectic),
    "nar:1" (temporal narrative), "hj:1" (human journal entry).
    Pass any of these to cortex_overseer_detail to drill in.
    """
    try:
        return send_command(_get_bridge_lazy(), "get_context", timeout=20)
    except Exception as e:
        return "Error: {}".format(e)


def _overseer_plugin_call(method, route, payload=None, timeout=20):
    """Helper: call /plugins/overseer/<route> via the WiFi bridge.
    Returns (result_dict, err_str)."""
    bridge = _get_bridge_lazy()
    fn = getattr(bridge, "plugin_call", None)
    if fn is None:
        return None, ("Overseer routes need the WiFi bridge to the Pi "
                      "(BLE/serial fallback can't reach plugin endpoints).")
    try:
        result = fn("overseer", method, route, payload, timeout=timeout)
    except Exception as e:
        return None, str(e)
    if not isinstance(result, dict):
        return None, "Bad response shape from Pi"
    if not result.get("ok"):
        return None, result.get("error", "unknown error")
    return result, None


@mcp.tool()
def cortex_overseer_detail(token: str) -> str:
    """Drill into one item from the Overseer's working memory.

    Working memory (returned by get_context) carries breadth — it lists
    questions, patterns, drift, rollups, gists, themes, etc., but each
    item is trimmed for size. This tool resolves a single item to its
    full untruncated row plus any related artifacts you can drill into
    next.

    The token is a short string from working_memory like:
      q:42    — open question
      p:5     — behavioral pattern
      d:1     — drift observation (started/stopped/shifted)
      g:75    — gist (per-session summary)
      e:3     — episode
      t:5     — theme
      r:18    — automation rollup (per-project per-day digest)
      n:1     — note from a prior overseer instance to future ones
      j:1     — overseer journal entry (the thinking layer)
      hj:8    — human journal entry (the user's own free-form/voice journal)
      nar:5   — temporal narrative (daily/weekly/monthly/yearly rollup)
      b:7     — known blindspot
      dial:2  — open dialectic between Opus and Gemma readings

    Returns JSON with:
      primary       — the full row with every column
      tags          — tags attached to this row
      context       — type-specific extras (e.g. evidence list for q:,
                      sample sessions for r:, parsed referenced
                      artifacts for j:)
      next_tokens   — list of {token, label, kind} you can pass back to
                      this tool to walk the graph (e.g. q: → its
                      evidence gists; g: → the questions it was filed
                      against; r: → the linked summary gist)

    Args:
        token: A token like "q:42" or "p:5".
    """
    import json as _json
    result, err = _overseer_plugin_call(
        "GET", "/detail", {"token": token}, timeout=10,
    )
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_intro(format: str = "markdown") -> str:
    """**START HERE.** Curated context brief about Tory (the user
    Cortex serves) and what he's currently working on / thinking
    about / deciding.

    Call this FIRST when starting a new conversation. In ~30 seconds
    of reading, you'll know:
      - Who Tory is (name, role, neurotype, location, sensitive
        topics, how to work with him)
      - What he's actively working on (top projects with session +
        time data)
      - What he's thinking about (high-confidence open questions
        with evidence counts + drill tokens)
      - Recent decisions (with drill tokens to the source gists)
      - Recent themes and key drift (his focus + behavior changes)
      - Calibration notes for the AI reading this (blindspots
        specifically about how YOUR model class misreads him)
      - Institutional memory from prior AI/looper instances

    This REPLACES the old `get_context` -> `working_memory` dump for
    new-session boot. working_memory was 29 keys, ~half operational
    chatter (queue depths, sibling stats, gist source distribution,
    automation rollups). The intro brief leads with Tory-state +
    demotes ops to a single sub-key.

    Drill tokens (`q:N`, `g:N`, `t:N`, `d:N`, `b:N`, `n:N`) in the
    brief work with `cortex_overseer_detail` for the full row.

    Args:
        format: 'markdown' (default — render as readable doc) or
            'json' (structured dict for programmatic consumers).

    Returns the brief as markdown or JSON.
    """
    import json as _json
    payload = {"format": format} if format else {}
    result, err = _overseer_plugin_call(
        "GET", "/intro", payload, timeout=15,
    )
    if err:
        return "Error: {}".format(err)
    if format == "markdown" and isinstance(result, dict) \
            and result.get("markdown"):
        return result["markdown"]
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_search(
    q: str,
    kinds: str = "",
    limit_per_kind: int = 5,
    limit_total: int = 40,
    days: int = 0,
    caller_id: str = "",
    mode: str = "auto",
) -> str:
    """Search across the Overseer's interpretive corpus - substring
    AND semantic (vector) in one call.

    mode='auto' (default): substring hits PLUS a `semantic` list of
    meaning-matched gists from the vector index (forgiving of
    paraphrase - 'bracelet that reads forearm muscles' finds the
    OpenMuscle gists with zero keyword overlap). mode='substring'
    skips the vector call; mode='semantic' returns only the vector
    results. Semantic results carry cosine `similarity` + the same
    g: drill tokens.

    This is the primary discovery tool for external AIs trying to
    find context on a topic. Walks the layered memory (gists,
    themes, episodes, patterns, drift, future_overseer_notes,
    journal entries, temporal narratives, open questions,
    blindspots, human journal entries) and returns hits with
    drill-down tokens.

    The intended workflow:
      1. Call cortex_search("topic of interest") to discover what
         the corpus knows.
      2. Pick the most-promising hits by snippet + kind.
      3. Pass the hit's `token` to cortex_overseer_detail for the
         full row + linked artifacts.

    This is NOT the right tool for:
      - Looking up notes (use notes_search — different table).
      - Asking the overseer a question (use overseer_chat — that
        renders working memory and runs Opus).
      - Reading a specific known token (use cortex_overseer_detail
        — direct lookup, no search).

    Each hit is recorded as a pull_event on the Pi so the overseer
    can see what external AIs are looking for. That signal is how
    gist prompts evolve.

    Args:
        q: Substring to search for (case-insensitive). Min 2 chars.
        kinds: Comma-separated subset of: gist, theme, episode,
            pattern, drift, note, journal, narrative, question,
            blindspot, human. Empty = all kinds.
        limit_per_kind: Max hits per kind (default 5).
        limit_total: Hard cap across all kinds (default 40).
        days: Restrict to artifacts within last N days (0 = no
            limit).
        caller_id: Optional free-form id. Convention (2026-06-06,
            looper iter #2): if you're automation (a script, a
            looper iteration, a test harness), tag yourself so the
            overseer's F1 adoption signal (caller_class =
            'organic-external') stays honest. Examples:
              'looper-iter5-cleanup'  → automation:looper
              'phase2-checkpoint'     → automation:bootstrap
              'tory-probe-2026-06-06' → user-probe
              'claude-code-verify-*'  → automation:verification
              <leave empty>           → organic-external (the F1
                                        metric — preserve this for
                                        ACTUAL external AI reads)

    Returns JSON with:
      ok            — bool
      query         — echoed query
      kinds_searched — list of kinds actually searched
      hits          — list of {token, kind, artifact_table,
                      artifact_id, snippet, created_at, extras}
      total         — count of hits
      truncated     — true if limit_total was hit
    """
    import json as _json
    semantic = []
    if mode in ("auto", "semantic"):
        sresult, serr = _overseer_plugin_call(
            "POST", "/vector/search", {"q": q, "k": 10}, timeout=30,
        )
        if not serr and isinstance(sresult, dict) and sresult.get("ok"):
            semantic = sresult.get("results") or []
    if mode == "semantic":
        return _json.dumps({
            "ok": True, "query": q, "mode": "semantic",
            "semantic": semantic, "total": len(semantic),
        }, indent=2, default=str)
    payload = {
        "q": q,
        "limit_per_kind": int(limit_per_kind),
        "limit_total": int(limit_total),
    }
    if kinds.strip():
        payload["kinds"] = kinds.strip()
    if days:
        payload["days"] = int(days)
    if caller_id.strip():
        payload["caller_id"] = caller_id.strip()
    result, err = _overseer_plugin_call(
        "GET", "/search", payload, timeout=20,
    )
    if err:
        return "Error: {}".format(err)
    if isinstance(result, dict) and semantic:
        result["semantic"] = semantic
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_sub_agents() -> str:
    """List the Cortex Overseer's B/C sub-agents with their current
    model tier, default tier, and per-agent invocation history.

    Sub-agents are stateless helpers the overseer dispatches for
    judgment-call work (e.g. b_theme_check audits a theme's confidence
    calibration). Each runs at one of four tiers — flash (cheap), glm
    (Z.ai GLM-5.2: open-weights, ~Opus-class coding/agentic at ~1/6 the
    price), sonnet (mid), opus (premium) — and Tory pulls the upgrade
    trigger when output is poor. The registry persists tier choices
    across restarts.

    Returns a JSON list. Each row has:
      agent_type           'b' or 'c'
      agent_name           e.g. 'theme_check'
      model_tier           current tier (flash|glm|sonnet|opus)
      current_model        the OpenRouter id that tier resolves to
      default_tier         code-side default (compare to spot drift
                           from Tory's manual upgrades)
      default_tier_rationale  why the default is what it is
      tier_set_at / tier_set_by  who set the current tier + when
      last_model_used      the model that actually ran on the most
                           recent dispatch (verifies the registry
                           is being honored)
      last_invoked_at      most recent dispatch time
      invocation_count     total dispatches since registry inception

    Pair with cortex_set_sub_agent_tier to change a tier.
    """
    import json as _json
    result, err = _overseer_plugin_call(
        "GET", "/sub-agents", None, timeout=10,
    )
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_set_sub_agent_tier(
    agent_type: str,
    agent_name: str,
    tier: str,
    notes: str = "",
) -> str:
    """Change a Cortex sub-agent's model tier. Persists across
    restarts. The next dispatch of that agent picks up the new tier.

    Args:
        agent_type: 'b' (stateless audit) or 'c' (scheduled).
        agent_name: e.g. 'theme_check' or 'project_merge_check'. Use
            cortex_sub_agents() to see the valid names.
        tier: 'flash' (cheap, ~$0.001/call), 'glm' (~$0.01, Z.ai GLM-5.2:
            open-weights, ~Opus-class coding/agentic at ~1/6 the price),
            'sonnet' (~$0.02), or 'opus' (~$0.10). Higher tiers cost more
            but handle nuance better. Confidence-calibration B-agents
            typically need sonnet minimum; structural-comparison Bs do
            fine on flash.
        notes: Optional free-text rationale for the tier change.
            Logged with the registry row so future-you (or future-AI)
            knows why the change was made.

    Returns the updated registry row.
    """
    import json as _json
    payload = {
        "agent_type": agent_type,
        "agent_name": agent_name,
        "tier": tier,
        "notes": notes,
    }
    result, err = _overseer_plugin_call(
        "POST", "/sub-agents/set-tier", payload, timeout=10,
    )
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_sub_agent_performance(
    agent_type: str,
    agent_name: str,
    last_n: int = 10,
) -> str:
    """Quality-rating signal for one sub-agent. Returns the last N
    completed dispatches with their quality_rating (Tory's 1-5 grade)
    + the model that actually ran.

    Use this when deciding whether to upgrade a sub-agent's tier.
    Rule of thumb (per overseer's guidance): if avg rating is <3 over
    last 5 rated dispatches, consider promoting one tier. If avg is
    >=4, consider demoting to save cost.

    Args:
        agent_type: 'b' or 'c'.
        agent_name: e.g. 'theme_check'.
        last_n: How many recent dispatches to look back (default 10).

    Returns:
      recent       — list of {id, rating, claimed_at, model, ...}
      n            — number of dispatches in the window
      avg_rating   — average rating across the rated dispatches (or null)
      rated_count  — how many in the window have a rating
      unrated_count — how many lack a rating
    """
    import json as _json
    payload = {
        "agent_type": agent_type,
        "agent_name": agent_name,
        "last_n": int(last_n),
    }
    result, err = _overseer_plugin_call(
        "GET", "/sub-agents/performance", payload, timeout=10,
    )
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def overseer_chat(message: str, timeout: int = 120) -> str:
    """Chat directly with the Overseer. Returns its full reply.

    This is the AI-conversation counterpart to ``cortex_overseer_detail``
    (which is read-only graph drill-down). The Overseer is the long-lived
    memory/reflection agent that runs on the Pi as the ``overseer`` plugin.
    It reads your notes, sessions, and imported AI conversations and
    builds working memory + summaries + open questions + drift + themes
    on top of them. ``overseer_chat`` sends a message to the chat endpoint
    (POST /plugins/overseer/chat), which renders the working-memory
    artifact, recent gists/themes/questions, and your message into a
    prompt and returns the model's reply.

    Default model: Opus 4.7 via OpenRouter (per plugin.toml). Sonnet is
    used for the cheaper internal jobs (auto-tag, journal); chat is
    Opus. Cost is ~$0.02-$0.10 per turn depending on context length.
    Daily call budget applies (currently 250/day, $5/day).

    When to use:
      - To get a real-time react/synthesis from the overseer about
        something happening NOW. (Milestones, decisions, open questions,
        observations.)
      - To ask the overseer what it remembers about a specific person,
        project, theme, or time period, anchored in its own analytical
        state (not just the raw notes).
      - To get it to draft an entry for ``future_overseer_notes`` or
        suggest schema changes to its own analytical surfaces — the
        overseer has demonstrated capacity to self-reflect and propose
        edits to its own context plumbing.

    When NOT to use:
      - To ask a generic LLM question. Use a normal LLM call; the
        overseer's context prompt is heavy and a generic question
        wastes the budget.
      - To do passive note-taking. Use ``send_note`` — it's free and
        the overseer reads notes on its next ingest pass.
      - To drill into a specific gist/theme/question by token — use
        ``cortex_overseer_detail`` instead, it's a read-only DB lookup.

    The overseer can take 15-60 seconds to reply (Opus + heavy context).
    Patience.

    Args:
        message: Your message to the overseer. Free-form. The overseer
            sees both this message and its assembled context.
        timeout: Max seconds to wait for a reply (default 120, hard
            cap 300).
    """
    import json as _json
    # Cap the timeout — chat shouldn't ever take >5min in practice;
    # an unbounded timeout would let a stuck call wedge the MCP session.
    t = max(15, min(int(timeout), 300))
    result, err = _overseer_plugin_call(
        "POST", "/chat", {"message": message}, timeout=t,
    )
    if err:
        return "Error: {}".format(err)
    # The chat endpoint returns {"ok": True, "reply": "...", ...
    # possibly other metadata like cost_usd, latency_ms, model}.
    # Surface the reply prominently; include metadata as a footer so
    # the caller can see how much it cost.
    reply = result.get("reply") or ""
    extra = {k: v for k, v in result.items()
             if k not in ("ok", "reply") and v is not None}
    if not reply:
        # Empty reply is unusual — return the whole envelope so the
        # caller can see what happened.
        return _json.dumps(result, indent=2, default=str)
    if extra:
        return reply + "\n\n---\n" + _json.dumps(extra, default=str)
    return reply


# ── Slice 9.3: sibling task dispatch tools ────────────────────────
# These let the overseer dispatch work TO this Claude Code session.
# Counterpart to overseer_chat (talk TO overseer). Pattern:
#   1. Overseer writes a task via its dispatch_sibling chat tool.
#   2. This Claude Code session calls sibling_pending() to see queue.
#   3. Claims a task with sibling_claim(id) — atomic so two siblings
#      can't race.
#   4. Does the actual work.
#   5. Submits result with sibling_complete(id, result, ...). Optionally
#      grades the overseer's dispatch back (reciprocal — prevents the
#      data from becoming "what overseer already believed, validated").
#   6. Or rejects with sibling_reject(id, reason) if out of scope.
#
# Auto-surface convention: at session start, any session with the cortex
# MCP loaded should call sibling_pending() and, if there's queued work,
# surface using the overseer's exact phrasing:
#   "Overseer flagged this for review when you opened the session"
# (Not notification register — handoff register. Distinction matters.)

@mcp.tool()
def sibling_pending(limit: int = 50) -> str:
    """List sibling tasks the overseer has dispatched that are
    waiting to be claimed.

    Use at session start (or when prompted) to see what the overseer
    has queued up for review. Tasks are typically judgment calls the
    overseer couldn't resolve alone — "is my read of X overfitting,"
    "does theme Y deserve [high] confidence given recent evidence,"
    etc. Each task carries the prompt + context the overseer attached
    + the cost budget you should respect.

    Returns JSON list of {id, prompt, task_type, cost_budget_usd,
    created_at, created_by, context_json, ...} or "(no pending tasks)".

    Auto-surface phrasing (for first-message-of-session use):
      "Overseer flagged this for review when you opened the session"
    Treat as continuous-work-state, not inbox notification.

    Args:
        limit: Max tasks to list (default 50).
    """
    import json as _json
    result, err = _overseer_plugin_call(
        "GET", "/sibling/pending",
        {"target": "claude-code", "limit": int(limit)},
        timeout=10,
    )
    if err:
        return "Error: {}".format(err)
    tasks = result.get("tasks") or []
    if not tasks:
        return "(no pending sibling tasks)"
    return _json.dumps(tasks, indent=2, default=str)


@mcp.tool()
def sibling_claim(task_id: int, claimed_by: str = "") -> str:
    """Claim a sibling task atomically. Refuses if another sibling
    already claimed it (race-safe).

    Returns the full task on success so you have everything needed
    without a second round-trip. Returns an error if the task is
    already claimed, completed, or doesn't exist.

    Args:
        task_id: The id from sibling_pending.
        claimed_by: Your sibling identifier. Defaults to
            "claude-code:<hostname>" — pass an explicit string if
            you want it more specific (e.g. a session_id or repo).
    """
    import json as _json
    import socket as _socket
    if not claimed_by:
        try:
            claimed_by = "claude-code:" + _socket.gethostname()
        except Exception:
            claimed_by = "claude-code:unknown"
    result, err = _overseer_plugin_call(
        "POST", "/sibling/claim",
        {"id": int(task_id), "claimed_by": claimed_by},
        timeout=10,
    )
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def sibling_complete(task_id: int, result_text: str,
                     actual_model_used: str = "",
                     result_cost_usd: float = 0.0,
                     dispatch_quality_rating: int = 0,
                     dispatch_quality_notes: str = "") -> str:
    """Submit a completed result for a sibling task.

    The overseer will see the result on its next tick (or sooner if it
    polls sibling_recent). The result_text is stored permanently and
    NEVER compacted, so future overseer instances can audit the
    round-trip.

    Reciprocal grading is OPTIONAL but strongly encouraged. Pass a
    dispatch_quality_rating (1-5) to rate the overseer's dispatch
    quality back at it. Mitigates the "overseer rates only what it
    already believed as valid" bias the overseer itself flagged when
    this surface was designed. Notes can be brief:
      1 = ambiguous / unanswerable as posed
      3 = workable but could have been scoped better
      5 = well-formed; one round-trip was the right granularity

    Args:
        task_id: The id you claimed.
        result_text: Your answer / synthesis / pushback.
        actual_model_used: e.g. "anthropic/claude-opus-4.7" or
            "anthropic/claude-sonnet-4.6". Helps the dataset later.
        result_cost_usd: Best-effort estimate of what doing this cost.
        dispatch_quality_rating: 1-5 reciprocal grade; 0 = skipped.
        dispatch_quality_notes: One-line rationale for the rating.
    """
    import json as _json
    payload = {
        "id": int(task_id),
        "result_text": result_text,
        "actual_model_used": actual_model_used,
        "result_cost_usd": float(result_cost_usd),
    }
    if dispatch_quality_rating and 1 <= int(dispatch_quality_rating) <= 5:
        payload["dispatch_quality_rating"] = int(dispatch_quality_rating)
        payload["dispatch_quality_notes"] = dispatch_quality_notes
    result, err = _overseer_plugin_call(
        "POST", "/sibling/complete", payload, timeout=15,
    )
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def sibling_reject(task_id: int, reason: str) -> str:
    """Pass on a sibling task. Different from completing with a poor
    result: rejection means you chose not to attempt it.

    Use when the task is out of scope, ambiguous beyond what one
    round-trip can resolve, would clearly exceed the cost budget, or
    isn't something a Claude Code session is the right surface for.
    The reason text shows up in the overseer's next-tick read so it
    learns what kinds of asks aren't landing.

    Args:
        task_id: The id you would otherwise claim/complete.
        reason: Why you're skipping it. Be specific — this is how the
            overseer calibrates future dispatches.
    """
    import json as _json
    result, err = _overseer_plugin_call(
        "POST", "/sibling/reject",
        {"id": int(task_id), "reason": reason},
        timeout=10,
    )
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def query(table: str, filters: str = "", limit: int = 10, order_by: str = "created_at DESC") -> str:
    """Query the Cortex database on the Pi.

    Generic query interface for retrieving stored data.

    Args:
        table: Table to query (notes, activities, searches, sessions, projects, computers, people, files).
        filters: JSON string of filters, e.g. '{"project":"cortex","type":"bug"}'.
        limit: Max results to return (default 10).
        order_by: SQL ORDER BY clause (default "created_at DESC").
    """
    try:
        payload = {"table": table, "limit": limit, "order_by": order_by}
        if filters:
            try:
                payload["filters"] = json.loads(filters)
            except (json.JSONDecodeError, ValueError):
                return "Error: 'filters' must be valid JSON (e.g. '{\"project\":\"cortex\"}')"
        return send_command(_get_bridge_lazy(), "query", payload, timeout=10)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def register_computer() -> str:
    """Register this computer with Cortex Core.

    Auto-detects hostname, OS, platform, and Python version.
    Useful for tracking which machines the user works on.
    """
    try:
        payload = {
            "hostname": socket.gethostname(),
            "os_info": "{} {} {}".format(
                platform.system(), platform.release(), platform.version()
            ),
            "platform": platform.machine(),
        }
        return send_command(_get_bridge_lazy(), "computer_reg", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_register(filename: str, category: str = "uploads", description: str = "",
                  tags: str = "", project: str = "", mime_type: str = "",
                  size_bytes: int = 0) -> str:
    """Register a file in the Cortex database for sharing and discovery.

    Records file metadata so AI agents can find and serve files by context.
    The file must already exist on the Pi (in the appropriate category directory).

    To transfer a file FROM this computer to the Pi, use file_upload instead.
    file_upload auto-registers the file in the DB after upload.

    Args:
        filename: Name of the file on the Pi.
        category: File category: recordings, notes, logs, uploads.
        description: Human-readable description of the file contents.
        tags: Comma-separated tags for categorization.
        project: Project tag this file belongs to.
        mime_type: MIME type (auto-detected if empty).
        size_bytes: File size in bytes.
    """
    try:
        payload = {"filename": filename, "category": category}
        if description:
            payload["description"] = description
        if tags:
            payload["tags"] = tags
        if project:
            payload["project"] = project
        if mime_type:
            payload["mime_type"] = mime_type
        if size_bytes:
            payload["size_bytes"] = size_bytes
        return send_command(_get_bridge_lazy(), "file_register", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_list(category: str = "", project: str = "", limit: int = 50) -> str:
    """List files registered in the Cortex database.

    Returns file metadata including name, description, tags, and download info.

    Args:
        category: Filter by category (recordings, notes, logs, uploads). Empty for all.
        project: Filter by project tag. Empty for all.
        limit: Max results (default 50).
    """
    try:
        payload = {"limit": limit}
        if category:
            payload["category"] = category
        if project:
            payload["project"] = project
        return send_command(_get_bridge_lazy(), "file_list", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_search(query: str, limit: int = 20) -> str:
    """Search for files by name, description, or tags.

    Searches across filename, description, and tags fields.

    Args:
        query: Search text to match against file metadata.
        limit: Max results (default 20).
    """
    try:
        payload = {"query": query, "limit": limit}
        return send_command(_get_bridge_lazy(), "file_search", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_upload(local_path: str, remote_name: str = "", description: str = "",
                tags: str = "", project: str = "") -> str:
    """Upload a file from this computer to the Pi Zero over WiFi.

    Transfers the file contents via HTTP and auto-registers it in the Cortex
    database with metadata. Only works when WiFi transport is active (not BLE).

    Use connection_info to verify WiFi is connected before uploading.

    Args:
        local_path: Absolute path to the file on this computer.
        remote_name: Filename on the Pi (defaults to local filename).
        description: Human-readable description of the file.
        tags: Comma-separated tags for categorization.
        project: Project tag this file belongs to.
    """
    try:
        bridge = _get_bridge_lazy()
        if not hasattr(bridge, "upload_file"):
            return ("Error: file_upload requires WiFi transport. "
                    "Current transport does not support file transfer. "
                    "Use connection_info to check WiFi status.")
        if not os.path.isfile(local_path):
            return "Error: file not found: {}".format(local_path)
        result = bridge.upload_file(
            local_path,
            remote_name=remote_name or None,
            description=description,
            tags=tags,
            project=project,
        )
        return "Uploaded: {} ({} bytes, file_id={})".format(
            result.get("filename"), result.get("size"), result.get("file_id"))
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_download(category: str, filename: str, local_path: str = "") -> str:
    """Download a file from the Pi Zero to this computer over WiFi.

    Retrieves file contents via HTTP. Only works when WiFi transport is active.

    Args:
        category: File category on the Pi: recordings, notes, logs, uploads.
        filename: Name of the file to download.
        local_path: Local destination path (defaults to current directory + filename).
    """
    try:
        bridge = _get_bridge_lazy()
        if not hasattr(bridge, "download_file"):
            return ("Error: file_download requires WiFi transport. "
                    "Current transport does not support file transfer. "
                    "Use connection_info to check WiFi status.")
        dest = local_path or os.path.join(".", filename)
        bridge.download_file(category, filename, dest)
        size = os.path.getsize(dest)
        return "Downloaded: {} -> {} ({} bytes)".format(filename, dest, size)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def send_message(message: str) -> str:
    """Send an arbitrary message to the Pi Zero through the bridge.

    Use for custom commands or data not covered by other tools.
    Messages are newline-delimited UTF-8, max 512 bytes.

    Args:
        message: The message to send.
    """
    try:
        lines = _get_bridge_lazy().send_and_wait(message, timeout=5)
        if lines:
            return "\n".join(lines)
        return "Sent (no response)."
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def read_responses() -> str:
    """Read any pending messages from the Pi Zero.

    Returns buffered messages that arrived without a preceding request.
    Useful for checking unsolicited data or async responses.
    """
    try:
        bridge = _get_bridge_lazy()
        bridge._ensure_connected()
        lines = bridge.read_pending()
        if lines:
            return "\n".join(lines)
        return "No pending messages."
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def connection_info() -> str:
    """Show the configured Cortex core URL and whether it is reachable."""
    try:
        from cortex_mcp.wifi_bridge import is_pi_reachable
        bridge = _get_bridge_lazy()
        reachable = is_pi_reachable(timeout=5.0)
        return "Core: {} ({})".format(
            bridge.port_name, "reachable" if reachable else "UNREACHABLE")
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Project Management Tools
# ===================================================================


@mcp.tool()
def project_upsert(
    tag: str,
    name: str = "",
    status: str = "active",
    priority: int = 3,
    description: str = "",
    category: str = "",
    org_tag: str = "",
    github_url: str = "",
    collaborators: str = "",
) -> str:
    """Create or update a project in the Cortex database.

    If the project tag already exists, its fields are updated.
    If it doesn't exist, a new project is created.

    Args:
        tag: Unique project identifier (e.g. "cortex-desktop", "employer"). Required.
        name: Human-friendly name (e.g. "Cortex Desktop"). Defaults to tag.
        status: Project status: active, archived, paused, completed.
        priority: Priority 1-5 (1 = highest).
        description: What the project is about.
        category: Project category (e.g. "ai", "web", "hardware").
        org_tag: Organization this project belongs to.
        github_url: GitHub repository URL.
        collaborators: Comma-separated list of collaborator names/IDs.
    """
    try:
        payload = {"tag": tag}
        if name:
            payload["name"] = name
        if status:
            payload["status"] = status
        if priority != 3:
            payload["priority"] = priority
        if description:
            payload["description"] = description
        if category:
            payload["category"] = category
        if org_tag:
            payload["org_tag"] = org_tag
        if github_url:
            payload["github_url"] = github_url
        if collaborators:
            payload["collaborators"] = collaborators
        return send_command(_get_bridge_lazy(), "project_upsert", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def project_list(status: str = "", category: str = "", limit: int = 50) -> str:
    """List all projects, optionally filtered by status or category.

    Args:
        status: Filter by status (active, archived, paused, completed). Empty for all.
        category: Filter by category. Empty for all.
        limit: Max results (default 50).
    """
    try:
        payload = {
            "table": "projects",
            "limit": limit,
            "order_by": "last_touched DESC",
        }
        filters = {}
        if status:
            filters["status"] = status
        if category:
            filters["category"] = category
        if filters:
            payload["filters"] = filters
        return send_command(_get_bridge_lazy(), "query", payload, timeout=10)
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Note Management Tools
# ===================================================================


@mcp.tool()
def note_update(note_id: int, tags: str = "", project: str = "", note_type: str = "") -> str:
    """Update an existing note's tags, project, or type.

    Use this to triage and categorize notes during review.

    Args:
        note_id: The note ID to update. Required.
        tags: New comma-separated tags (replaces existing).
        project: New project tag to assign.
        note_type: New note type: note, decision, bug, reminder, idea, todo, context.
    """
    try:
        row_data = {"id": note_id}
        if tags:
            row_data["tags"] = tags
        if project:
            row_data["project"] = project
        if note_type:
            row_data["note_type"] = note_type
        payload = {"table": "notes", "data": row_data}
        return send_command(_get_bridge_lazy(), "upsert", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def notes_search(search_text: str, project: str = "", note_type: str = "", limit: int = 30) -> str:
    """Search notes by content text, with optional project/type filters.

    Performs a case-insensitive substring search across note content.
    Also supports filtering by project tag and note type.

    Args:
        search_text: Text to search for in note content. Required.
        project: Filter to notes in this project only.
        note_type: Filter by type: note, decision, bug, reminder, idea, todo, context.
        limit: Max results (default 30).
    """
    try:
        # The Pi query command only supports exact = filters, so we fetch
        # a larger set and filter client-side for content matching.
        payload = {
            "table": "notes",
            "limit": 100,
            "order_by": "created_at DESC",
        }
        filters = {}
        if project:
            filters["project"] = project
        if note_type:
            filters["note_type"] = note_type
        if filters:
            payload["filters"] = filters
        raw = send_command(_get_bridge_lazy(), "query", payload, timeout=10)

        # Parse response and filter by content
        if raw.startswith("RSP:query:"):
            data = json.loads(raw[len("RSP:query:"):])
        elif raw.startswith("["):
            data = json.loads(raw)
        else:
            return raw  # Error or unexpected format

        needle = search_text.lower()
        matches = [
            row for row in data
            if needle in (row.get("content") or "").lower()
        ][:limit]
        return json.dumps(matches, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Generic CRUD Tools
# ===================================================================


@mcp.tool()
def upsert_row(table: str, data: str) -> str:
    """Insert or update a row in any Cortex database table.

    If the data includes an 'id' (or primary key) that already exists,
    the row is updated. Otherwise a new row is inserted.

    Args:
        table: Table name (notes, projects, activities, searches, sessions,
               computers, people, files, organizations, time_entries).
        data: JSON string of column-value pairs, e.g. '{"tag":"my-proj","name":"My Project"}'.
    """
    try:
        try:
            row_data = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return "Error: 'data' must be valid JSON (e.g. '{\"tag\":\"my-proj\"}')"
        payload = {"table": table, "data": row_data}
        return send_command(_get_bridge_lazy(), "upsert", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def delete_row(table: str, row_id: int) -> str:
    """Delete a row from a Cortex database table by its ID.

    Args:
        table: Table name (notes, activities, searches, sessions, files, etc.).
        row_id: The row ID to delete.
    """
    try:
        payload = {"table": table, "id": row_id}
        return send_command(_get_bridge_lazy(), "delete", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def table_counts() -> str:
    """Get row counts for all tables in the Cortex database.

    Returns a summary of how many rows each table contains.
    Useful for a quick health check or data overview.
    """
    try:
        return send_command(_get_bridge_lazy(), "table_counts", timeout=10)
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Audit & Upkeep Tools
# ===================================================================


def _query_table(table, filters=None, limit=100, order_by="created_at DESC"):
    """Internal helper: query a table and return parsed list of dicts."""
    payload = {"table": table, "limit": limit, "order_by": order_by}
    if filters:
        payload["filters"] = filters
    raw = send_command(_get_bridge_lazy(), "query", payload, timeout=15)
    if raw.startswith("RSP:query:"):
        return json.loads(raw[len("RSP:query:"):])
    elif raw.startswith("["):
        return json.loads(raw)
    return []


@mcp.tool()
def audit_projects(stale_days: int = 30) -> str:
    """Audit all projects for staleness, missing data, and activity levels.

    Reviews each project and flags issues:
    - Stale: no activity (notes, sessions, time entries) in N+ days
    - Missing description
    - No time logged
    - No notes linked

    Args:
        stale_days: Number of days without activity to consider a project stale (default 30).
    """
    try:
        from datetime import datetime, timedelta

        projects = _query_table("projects", limit=100, order_by="last_touched DESC")
        notes = _query_table("notes", limit=100)
        time_entries = _query_table("time_entries", limit=100, order_by="created_at DESC")

        # Index notes and time by project
        notes_by_project = {}
        for n in notes:
            p = n.get("project", "")
            if p:
                notes_by_project.setdefault(p, []).append(n)

        time_by_project = {}
        for t in time_entries:
            p = t.get("project_tag", "")
            if p:
                time_by_project.setdefault(p, []).append(t)

        cutoff = (datetime.utcnow() - timedelta(days=stale_days)).isoformat()
        report = []

        for proj in projects:
            tag = proj.get("tag", "")
            issues = []
            last_touched = proj.get("last_touched") or proj.get("created_at") or ""

            if last_touched and last_touched < cutoff:
                issues.append("stale (no activity in {}+ days)".format(stale_days))
            if not proj.get("description"):
                issues.append("missing description")

            note_count = len(notes_by_project.get(tag, []))
            time_count = len(time_by_project.get(tag, []))
            total_hours = proj.get("total_hours", 0)

            if time_count == 0:
                issues.append("no time logged")
            if note_count == 0:
                issues.append("no notes linked")

            report.append({
                "tag": tag,
                "name": proj.get("name", ""),
                "status": proj.get("status", ""),
                "last_touched": last_touched,
                "total_hours": total_hours,
                "note_count": note_count,
                "time_entry_count": time_count,
                "issues": issues,
            })

        # Sort: projects with issues first, then by last_touched
        report.sort(key=lambda x: (len(x["issues"]) == 0, x.get("last_touched") or ""))

        summary = {
            "total_projects": len(projects),
            "projects_with_issues": sum(1 for r in report if r["issues"]),
            "stale_projects": sum(1 for r in report if any("stale" in i for i in r["issues"])),
            "projects": report,
        }
        return json.dumps(summary, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def audit_notes(limit: int = 50) -> str:
    """Find notes that need triage — untagged, uncategorized, or unlinked.

    Returns notes missing tags, project assignment, or using the default 'note' type.
    Use note_update to fix them.

    Args:
        limit: Max notes to return (default 50).
    """
    try:
        notes = _query_table("notes", limit=100, order_by="created_at DESC")

        untagged = []
        no_project = []
        default_type = []

        for n in notes:
            note_id = n.get("id")
            preview = (n.get("content") or "")[:100]
            entry = {
                "id": note_id,
                "preview": preview,
                "created_at": n.get("created_at", ""),
                "tags": n.get("tags", ""),
                "project": n.get("project", ""),
                "note_type": n.get("note_type", ""),
            }
            if not n.get("tags"):
                untagged.append(entry)
            if not n.get("project"):
                no_project.append(entry)
            if n.get("note_type", "note") == "note":
                default_type.append(entry)

        result = {
            "total_notes_checked": len(notes),
            "untagged_count": len(untagged),
            "no_project_count": len(no_project),
            "default_type_count": len(default_type),
            "untagged": untagged[:limit],
            "no_project": no_project[:limit],
            "default_type": default_type[:limit],
        }
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def audit_data_quality() -> str:
    """Run a data quality and health check on the Cortex database.

    Checks:
    - Table row counts
    - Sessions with no summary (incomplete)
    - Projects with no activity
    - Orphaned time entries (project doesn't exist)
    - Overall database health summary
    """
    try:
        # Get table counts
        counts_raw = send_command(_get_bridge_lazy(), "table_counts", timeout=10)
        if counts_raw.startswith("RSP:table_counts:"):
            counts = json.loads(counts_raw[len("RSP:table_counts:"):])
        elif counts_raw.startswith("{"):
            counts = json.loads(counts_raw)
        else:
            counts = {}

        # Check for incomplete sessions (no summary)
        sessions = _query_table("sessions", limit=50, order_by="started_at DESC")
        incomplete_sessions = [
            {"id": s.get("id"), "started_at": s.get("started_at"), "ai_platform": s.get("ai_platform")}
            for s in sessions
            if not s.get("summary") and not s.get("ended_at")
        ]

        # Check projects with no recent activity
        projects = _query_table("projects", limit=100, order_by="last_touched DESC")
        project_tags = {p.get("tag") for p in projects}

        # Check time entries referencing non-existent projects
        time_entries = _query_table("time_entries", limit=100, order_by="created_at DESC")
        orphaned_time = [
            {"id": t.get("id"), "project_tag": t.get("project_tag"), "description": t.get("description")}
            for t in time_entries
            if t.get("project_tag") and t.get("project_tag") not in project_tags
        ]

        issues = []
        if incomplete_sessions:
            issues.append("{} incomplete sessions (no summary/end)".format(len(incomplete_sessions)))
        if orphaned_time:
            issues.append("{} time entries reference non-existent projects".format(len(orphaned_time)))
        if counts.get("notes", 0) == 0:
            issues.append("No notes in database")
        if counts.get("projects", 0) == 0:
            issues.append("No projects in database")

        result = {
            "table_counts": counts,
            "issues_found": len(issues),
            "issues": issues,
            "incomplete_sessions": incomplete_sessions[:10],
            "orphaned_time_entries": orphaned_time[:10],
            "health": "good" if len(issues) == 0 else "needs attention",
        }
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def weekly_review() -> str:
    """Run a comprehensive weekly review of the Cortex database.

    Combines table counts, project audit, note triage, and recent session
    summary into a single structured report. Use this at the start of a
    weekly upkeep session to understand what needs attention.

    The report includes:
    - Database overview (row counts)
    - Project health (stale, missing data)
    - Notes needing triage (untagged, uncategorized)
    - Recent sessions summary
    - Actionable recommendations
    """
    try:
        from datetime import datetime, timedelta

        # 1. Table counts
        counts_raw = send_command(_get_bridge_lazy(), "table_counts", timeout=10)
        if counts_raw.startswith("RSP:table_counts:"):
            counts = json.loads(counts_raw[len("RSP:table_counts:"):])
        elif counts_raw.startswith("{"):
            counts = json.loads(counts_raw)
        else:
            counts = {}

        # 2. Projects
        projects = _query_table("projects", limit=100, order_by="last_touched DESC")
        cutoff_30d = (datetime.utcnow() - timedelta(days=30)).isoformat()
        cutoff_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()

        active_projects = [p for p in projects if p.get("status") == "active"]
        stale_projects = [
            p.get("tag") for p in active_projects
            if (p.get("last_touched") or "") < cutoff_30d
        ]

        # 3. Notes triage
        notes = _query_table("notes", limit=100, order_by="created_at DESC")
        recent_notes = [n for n in notes if (n.get("created_at") or "") >= cutoff_7d]
        untagged_notes = [n for n in notes if not n.get("tags")]
        no_project_notes = [n for n in notes if not n.get("project")]

        # 4. Recent sessions
        sessions = _query_table("sessions", limit=10, order_by="started_at DESC")
        recent_sessions = []
        for s in sessions:
            recent_sessions.append({
                "id": s.get("id"),
                "started_at": s.get("started_at"),
                "summary": (s.get("summary") or "(no summary)")[:120],
                "projects": s.get("projects", ""),
            })

        # 5. Time entries this week
        time_entries = _query_table("time_entries", limit=100, order_by="created_at DESC")
        weekly_time = [t for t in time_entries if (t.get("created_at") or "") >= cutoff_7d]
        weekly_hours = sum(t.get("duration_minutes", 0) for t in weekly_time) / 60.0

        # 6. Build recommendations
        recommendations = []
        if stale_projects:
            recommendations.append(
                "Review {} stale projects: {}".format(
                    len(stale_projects), ", ".join(stale_projects[:5])
                )
            )
        if untagged_notes:
            recommendations.append(
                "Triage {} untagged notes (use note_update to add tags)".format(len(untagged_notes))
            )
        if no_project_notes:
            recommendations.append(
                "Link {} notes to projects (use note_update)".format(len(no_project_notes))
            )
        incomplete = [s for s in sessions if not s.get("summary") and not s.get("ended_at")]
        if incomplete:
            recommendations.append(
                "Close {} incomplete sessions".format(len(incomplete))
            )
        if not recommendations:
            recommendations.append("Everything looks good! Database is well-maintained.")

        report = {
            "report_date": datetime.utcnow().isoformat()[:10],
            "database_overview": counts,
            "projects": {
                "total": len(projects),
                "active": len(active_projects),
                "stale_30d": stale_projects,
            },
            "notes": {
                "total": counts.get("notes", len(notes)),
                "added_this_week": len(recent_notes),
                "untagged": len(untagged_notes),
                "no_project": len(no_project_notes),
            },
            "sessions": {
                "recent": recent_sessions[:5],
            },
            "time_tracking": {
                "hours_this_week": round(weekly_hours, 1),
                "entries_this_week": len(weekly_time),
            },
            "recommendations": recommendations,
        }
        return json.dumps(report, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


# ── Slice 6: people (Overseer's relationship memory) ────────────
#
# These tools are the PRIMARY surface for adding/updating people in
# the user's memory. The Hub UI is the secondary curation/review
# surface. Agents working alongside Tory in his other repos call
# these to capture who he's working with — without him having to
# do data entry.
#
# When TO use these:
#   - Recurring conversation partners or collaborators named in
#     the work you're doing with the user
#   - Subjects of inquiry (researchers, founders, historical figures
#     central to a project)
#   - Mentors, references, sources cited repeatedly
#
# When NOT to use:
#   - Casual single mentions in code or docs
#   - Names of fictional characters in stories the user is writing
#   - Variable names that happen to look like names
#   - Yourself or other AI agents
#
# Add tools are IDEMPOTENT on case-insensitive name match — safe to
# call repeatedly. Use cortex_people_search FIRST to check whether
# someone's already in memory before adding (avoids dupe records
# with slight name variations like "Dr. X" vs "Dr X").


def _people_get(method, route, payload=None, timeout=15):
    """Helper: call /plugins/overseer/people<route>."""
    bridge = _get_bridge_lazy()
    fn = getattr(bridge, "plugin_call", None)
    if fn is None:
        return None, ("People routes need the WiFi bridge to the Pi "
                      "(BLE/serial fallback can't reach plugin endpoints).")
    full_route = "/people" + route
    try:
        result = fn("overseer", method, full_route, payload,
                    timeout=timeout)
    except Exception as e:
        return None, str(e)
    if not isinstance(result, dict):
        return None, "Bad response shape from Pi"
    if not result.get("ok"):
        return None, result.get("error", "unknown error")
    return result, None


@mcp.tool()
def cortex_people_list(limit: int = 50) -> str:
    """List people in the Overseer's memory, newest-interaction first.

    Returns name + display_name + handles + expertise + linked
    projects (count). Use cortex_people_get for the full record on
    one person.

    Args:
        limit: Max rows to return (default 50, max 500).
    """
    import json as _json
    result, err = _people_get("GET", "",
                               {"limit": min(limit, 500)})
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_search(query: str, limit: int = 20) -> str:
    """Search people by name / handle / expertise / tags / notes.

    USE THIS BEFORE cortex_people_add to avoid creating duplicate
    records when someone already exists under a slightly different
    name (e.g. "Dr. Jane X" vs "Jane X" vs "@janex").

    Args:
        query: substring to match (case-insensitive). Empty = recent.
        limit: max rows (default 20, max 200).
    """
    import json as _json
    result, err = _people_get("GET", "/search",
                               {"q": query,
                                "limit": min(limit, 200)})
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_get(person_id: int) -> str:
    """Full record for one person — name, handles, expertise, all
    notes (full text, audit-trailed), tags, linked projects with
    roles. Use cortex_people_search to find IDs first.
    """
    import json as _json
    result, err = _people_get("GET", "/get", {"id": person_id})
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_add(
    name: str,
    display_name: str = "",
    online_handles: str = "",
    social_links: str = "",
    areas_of_expertise: str = "",
    notes: str = "",
    tags: str = "",
    aliases: str = "",
    created_by_agent: str = "claude-code",
    created_by_session_id: str = "",
) -> str:
    """Add a person to the Overseer's relationship memory.

    Idempotent on case-insensitive name — if someone with the same
    name already exists, returns the existing record with
    `created: false`. Call cortex_people_update instead to merge
    new information into an existing record.

    Use this for people who recur in the user's work — collaborators,
    subjects of inquiry, mentors, regular conversation partners. Skip
    casual single mentions, fictional names, and yourself.

    Args:
        name: Canonical name (e.g. "Jane Doe" or "Dr. Jane Doe").
              Case-insensitive match for dedup; preserve user's
              preferred capitalization.
        display_name: How the user usually refers to them
              (e.g. "Jane"). Optional.
        online_handles: Comma-separated handles
              (e.g. "@jane,github.com/jane,@jane#discord").
        social_links: Comma-separated URLs
              (e.g. "https://janedoe.com,linkedin.com/in/jane").
        areas_of_expertise: Comma-separated short tags
              (e.g. "AI ethics,ancient material culture").
        notes: Free-form one or two sentences on who they are and
              why they matter to the user. Append-mode on update —
              this becomes the seed.
        tags: Comma-separated general tags (optional).
        created_by_agent: Identifies which agent added them
              (default "claude-code").
        created_by_session_id: Session identifier so the user can
              trace back to the conversation that captured them.
    """
    import json as _json
    payload = {
        "name": name,
        "display_name": display_name,
        "online_handles": online_handles,
        "social_links": social_links,
        "areas_of_expertise": areas_of_expertise,
        "notes": notes,
        "tags": tags,
        "aliases": aliases,
        "created_by_agent": created_by_agent,
        "created_by_session_id": created_by_session_id,
    }
    result, err = _people_get("POST", "/add", payload, timeout=20)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_update(
    person_id: int,
    notes_append: str = "",
    display_name: str = "",
    online_handles: str = "",
    social_links: str = "",
    areas_of_expertise: str = "",
    tags: str = "",
    aliases: str = "",
    update_last_interacted: bool = True,
) -> str:
    """Update an existing person's record. Pass only the fields you
    want to change.

    notes_append is the agent-friendly mode: appends a new
    timestamped line to the existing notes (preserves history). Use
    this when you've learned something new about a person — e.g.
    "Mentioned working on X with the user this week."

    JSON list fields (handles / links / expertise / tags) are
    REPLACE-mode — pass the FULL new list (existing list + your
    additions). Call cortex_people_get first to see the current
    state if you need to merge.

    update_last_interacted: when True (default), bumps the
    last_interacted_at timestamp. Drives ordering in the Hub UI;
    NO nudge or notification is driven from this field.

    Args:
        person_id: id from cortex_people_search or cortex_people_list.
        notes_append: new note line to append (timestamped + agent-
              attributed automatically).
        display_name / online_handles / social_links /
        areas_of_expertise / tags: replace-mode updates.
        update_last_interacted: bump the timestamp (default True).
    """
    import json as _json
    payload = {"id": person_id}
    if notes_append:
        payload["notes_append"] = notes_append
    if display_name:
        payload["display_name"] = display_name
    if online_handles:
        payload["online_handles"] = online_handles
    if social_links:
        payload["social_links"] = social_links
    if areas_of_expertise:
        payload["areas_of_expertise"] = areas_of_expertise
    if tags:
        payload["tags"] = tags
    if aliases:
        payload["aliases"] = aliases
    if update_last_interacted:
        from datetime import datetime, timezone
        payload["last_interacted_at"] = datetime.now(
            timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    result, err = _people_get("POST", "/update", payload, timeout=20)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_link_project(
    project: str,
    person_id: int,
    role: str = "",
    created_by_agent: str = "claude-code",
) -> str:
    """Link a person to a project. Idempotent — re-linking updates
    the role.

    Use this when you can clearly identify a person's relationship
    to a project (collaborator, subject, mentor, source, inspiration).

    Args:
        project: Project name as it appears in the Hub (e.g. "Cortex",
              "UFOSINT", "Ancient Art"). See cortex_people_for_project
              for the canonical project list.
        person_id: id from cortex_people_search.
        role: Optional free-text role (e.g. "collaborator", "mentor",
              "subject", "source", "inspiration").
        created_by_agent: agent identifier (default "claude-code").
    """
    import json as _json
    payload = {
        "project": project, "person_id": person_id,
        "role": role, "created_by_agent": created_by_agent,
    }
    result, err = _people_get("POST", "/link-project", payload)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_for_project(project: str) -> str:
    """List all people linked to a project, with their roles. Use
    this to load relationship context when working on a project —
    e.g. before writing a session that references collaborators or
    discusses someone the user has notes on.
    """
    import json as _json
    result, err = _people_get("GET", "/for-project", {"project": project})
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_unlink_project(project: str, person_id: int) -> str:
    """Remove a project↔person link. Use this if you (or a prior
    agent) created a wrong link — e.g. linked the wrong person, or
    linked a person to a project they're not actually involved in.

    The person record itself is NOT deleted; only the link.
    """
    import json as _json
    payload = {"project": project, "person_id": person_id}
    result, err = _people_get("POST", "/unlink-project", payload)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_stats() -> str:
    """Cross-cutting stats on the Overseer's people memory.

    Returns counts + signal pointers:
      total_people
      added_24h, added_7d         — capture velocity
      orphans_count               — people with no project links
                                    (review candidates: agents added
                                    them but never connected them
                                    to a project)
      multi_project_count         — people linked to ≥2 projects
                                    (the connectors — these make
                                    cross-project narratives interesting)
      top_projects                — top 10 projects by linked-people count
      top_expertise_tags          — top 5 areas_of_expertise tags by frequency
      recent_additions            — newest 10 with timestamp + agent +
                                    session_id (for spot-checking what
                                    got captured recently)

    Useful for both agents (decide whether the people memory is rich
    enough to load into context) and the upcoming Hub UI (Network
    section header summary).
    """
    import json as _json
    result, err = _people_get("GET", "/stats", None)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_note_add(
    person_id: int,
    body: str,
    note_kind: str = "context",
    provenance: str = "ai-convo",
    modality: str = "statement",
    created_by_agent: str = "claude-code",
    created_by_session_id: str = "",
) -> str:
    """Add a STRUCTURED, taxonomy-tagged note about a person to the
    canonical people store (overseer_people / person_notes).

    The axis-aware companion to cortex_people_update(notes_append=...).
    Each note is a queryable row carrying the integrity pair —
    provenance (WHO authored) + modality (what KIND of claim) — plus a
    lens-ish note_kind, so a future AI can separate "Tory-stated
    preferences" from "inferences made about them" and never let an
    inference read as fact.

    Use for durable, attributable context/preferences/commitments. For a
    quick free-form line, cortex_people_update(notes_append=...) is fine.

    Args:
        person_id: id from cortex_people_search / cortex_people_list.
        body: the note text (one or two sentences).
        note_kind: context | interaction | preference | commitment | fact
              (default "context").
        provenance: tory-voice | tory-typed | overseer | ai-convo |
              import. Default "ai-convo" (an MCP agent is writing it);
              use tory-voice/tory-typed only when relaying his words.
        modality: observation | statement | inference | hypothesis |
              value-judgment | external-claim | pattern (default
              "statement"). Mark inferences honestly.
        created_by_agent: which agent (default "claude-code").
        created_by_session_id: session id for traceback.
    """
    import json as _json
    payload = {
        "person_id": person_id,
        "body": body,
        "note_kind": note_kind,
        "provenance": provenance,
        "modality": modality,
        "created_by_agent": created_by_agent,
        "created_by_session_id": created_by_session_id,
    }
    result, err = _people_get("POST", "/notes/add", payload, timeout=20)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_people_notes(person_id: int, limit: int = 100) -> str:
    """List the structured notes about a person (newest first, live
    only). Each note carries provenance + modality + note_kind + time,
    so you can see WHO said WHAT KIND of thing about them and when.

    Use cortex_people_get for the person's core record + free-form notes
    blob; this is the structured, axis-tagged channel on top of it.
    """
    import json as _json
    result, err = _people_get(
        "GET", "/notes", {"person_id": person_id, "limit": limit})
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


# ── Tech skills + rules (2026-07-12) ─────────────────────────────
#
# The user's living SKILLS PORTFOLIO and standing TECH RULES, stored
# in the corpus so every AI session that connects to Cortex learns
# from problems earlier sessions already hit. These tools are the
# PRIMARY entry surface; /intro serves the active rules digest to
# every connecting AI automatically.
#
# When TO log:
#   - A real lesson, win, or breakthrough in one of the user's core
#     skills emerges in the work you're doing together
#   - Something went wrong in his stack, you found the fix, and the
#     default approach should change for every future session
# When NOT to log:
#   - Generic best practices not grounded in his actual experience
#   - One-off trivia with no reuse value
#   - Anything personal or confidential (this data serves broadly)


def _tech_get(method, route, payload=None, timeout=15):
    """Helper: call /plugins/overseer<route> for skills/rules."""
    bridge = _get_bridge_lazy()
    fn = getattr(bridge, "plugin_call", None)
    if fn is None:
        return None, ("Skills/rules routes need the WiFi bridge to the "
                      "Pi (BLE/serial fallback can't reach plugin "
                      "endpoints).")
    try:
        result = fn("overseer", method, route, payload, timeout=timeout)
    except Exception as e:
        return None, str(e)
    if not isinstance(result, dict):
        return None, "Bad response shape from Pi"
    if not result.get("ok"):
        return None, result.get("error", "unknown error")
    return result, None


@mcp.tool()
def cortex_skills(name: str = "") -> str:
    """The user's living skills portfolio (tech skills, proficiency,
    tools, lessons learned, wins).

    With no arguments: the portfolio index (every skill + proficiency
    + entry counts). With a name: the full entry for that skill,
    including its recent log of lessons/wins/projects.

    Read this when work touches one of the user's stacks so you build
    on what earlier sessions already learned instead of rediscovering
    it. Log new lessons with cortex_skill_log.

    Args:
        name: Skill to drill into (e.g. "React Native"). Empty lists
              all skills.
    """
    import json as _json
    if name.strip():
        result, err = _tech_get("GET", "/skills/get",
                                {"name": name.strip()})
    else:
        result, err = _tech_get("GET", "/skills", {})
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_skill_log(
    skill: str,
    content: str,
    kind: str = "note",
    project: str = "",
    proficiency: str = "",
    summary: str = "",
    tools: str = "",
    source: str = "claude-code",
) -> str:
    """Log a lesson, win, project use, or tooling note under one of
    the user's core skills. Creates the skill on first mention
    (check cortex_skills first to reuse an existing name rather than
    creating near-duplicates like "RN" vs "React Native").

    Use when a real lesson or breakthrough emerges in the work: a
    debugging insight tied to his stack, a tool/version decision, a
    project that exercised the skill, a capability he just proved
    out. Skip generic advice not grounded in this work.

    Args:
        skill: Core skill name (e.g. "PCB design", "React Native",
               "LLM agent architecture").
        content: The entry itself, 1-4 sentences, concrete.
        kind: lesson | win | project | tooling | note. Case is
               normalized; anything else is stored as 'note'.
        project: Project tag where it happened (optional).
        proficiency: If this work changes the picture, update it
               (freeform: "expert", "working", "learning").
        summary: Update the skill's living one-paragraph portfolio
               blurb (only when it genuinely improves it).
        tools: Update the skill's tools + versions line
               (e.g. "KiCad 8, JLCPCB"). Full replace, so include
               the existing tools you want to keep.
        source: Which agent/session logged it.
    """
    import json as _json
    payload = {
        "skill": skill, "content": content, "kind": kind,
        "project": project, "source": source,
    }
    # Empty optionals stay out of the payload: the refine rule is
    # that only non-empty fields overwrite the living header.
    for key, val in (("proficiency", proficiency),
                     ("summary", summary), ("tools", tools)):
        if val.strip():
            payload[key] = val.strip()
    result, err = _tech_get("POST", "/skills/log", payload, timeout=20)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_rules(stack: str = "", include_retired: bool = False) -> str:
    """The user's standing tech rules: hard-won defaults from things
    that actually went wrong in his stacks. Each rule carries its
    story (situation, what went wrong, what changed, why it is now
    the default).

    These apply to EVERY AI conversation connected to Cortex. Read
    them before advising on tooling, debugging, or architecture in a
    stack the user works in. The active digest is also served in
    cortex_intro; this tool gives the full stories.

    Args:
        stack: Optional substring filter on stack tags
               (e.g. "expo", "powershell", "azure").
        include_retired: Also show rules that no longer apply.
    """
    import json as _json
    result, err = _tech_get("GET", "/rules", {
        "stack": stack,
        "status": "all" if include_retired else "active",
    })
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


@mcp.tool()
def cortex_rule_add(
    title: str,
    rule: str,
    stack: str = "",
    situation: str = "",
    went_wrong: str = "",
    what_changed: str = "",
    rationale: str = "",
    status: str = "",
    source: str = "claude-code",
) -> str:
    """Record a tech-decision rule so every future AI session applies
    it. Structure it as a lesson: tech stack X in situation Y, what
    went wrong, what you changed, why it is now the default.

    Upserts on case-insensitive title: re-adding an existing title
    UPDATES it (non-empty fields overwrite), so you can refine a rule
    or retire it (status="retired") under its natural key. Check
    cortex_rules first to extend an existing rule rather than adding
    a near-duplicate.

    Use when something concretely went wrong and the default approach
    changed. Skip generic best practices the user never had to learn
    the hard way.

    Args:
        title: Natural key, e.g. "Expo SDK 51 permission prompts".
        rule: The imperative default, 1-2 sentences. What should
              every future session DO.
        stack: Comma tags, e.g. "expo,react-native,android".
        situation: When the rule applies.
        went_wrong: What failed, concretely.
        what_changed: The fix/approach adopted.
        rationale: Why this is now the default.
        status: Leave empty for active; "retired" to sunset a rule.
               Anything other than active/retired returns an error
               (never silently ignored).
        source: Which agent/session logged it.
    """
    import json as _json
    payload = {
        "title": title, "rule": rule, "stack": stack,
        "situation": situation, "went_wrong": went_wrong,
        "what_changed": what_changed, "rationale": rationale,
        "source": source,
    }
    if status.strip():
        payload["status"] = status.strip()
    result, err = _tech_get("POST", "/rules/add", payload, timeout=20)
    if err:
        return "Error: {}".format(err)
    return _json.dumps(result, indent=2, default=str)


def main():
    """Entry point for the cortex-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()
