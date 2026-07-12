# Cortex Desktop v0.20.x

## v0.20.1 (July 2026): permanent memory in Simples

- **Year view reads the whole corpus, any year**: every day shaded by
  AI-session + logged hours (16h/session clamp against gap-straddling
  rows; session-hours sum across parallel sessions, so heavy days can
  top 24h). Hover for the mix (sleep, steps); faint marks for days
  with only health data. Corpus outages show a retry banner instead
  of rendering as an empty year.
- **"This day in Cortex" panel on Day view**: click any day, any
  year, and see everything the corpus holds for it: AI sessions with
  their gist one-liners, logged time, health chips (steps, sleep,
  scores), journal entries, and the daily narrative.
- Nav reset button reads Today / This week / This month / This year
  to match the active view.
- Pi-side companions in cortex-core (`dc84d79`): GET
  /plugins/overseer/day and /day/heat.

# Cortex Desktop v0.20.0

**Released:** July 2026
**Theme:** The agent harness: threads, feedback, MCP in both directions, and the planner on the desktop

The 0.20 cycle promotes 14 dev releases into a stable release. Cortex
becomes an agent harness proper: chat grows threads and a prompt
library, every AI interaction can be rated and discussed, the Pi
consumes external MCP servers while also serving skills and standing
tech rules to every AI that connects, and the phone's liquid planner
gets a full read-only mirror in the Hub.

Code lives across two repos:

- **cortex-desktop** (this repo): Hub UI + backend proxies + voice sidecar launcher + MCP server
- **[cortex-core](https://github.com/turfptax/cortex-core)**: Pi-side overseer plugin, schema, agent logic

---

## Headline capabilities

### Agent-harness chat: threads + prompt library

- Chat is promoted to a top-level sidebar page.
- **Threads**: create / switch / rename / delete, auto-titled from the
  first message. Pi-side schema with an active-thread pointer so every
  pre-thread consumer (voice, MCP, router) stays coherent; existing
  history was adopted into a legacy thread verbatim.
- **Prompt library**: reusable snippets stored Pi-side, pickable from
  the composer.
- Turn-scoped thread pinning end to end, so switching surfaces mid-turn
  can never split a conversation across threads.

### Interaction meta-feedback (in line with Lemon Squeezer)

- Any AI interaction can be rated: thumbs + optional note on Hub chat
  turns, phone voice chats, and Bell conversations.
- Note-first by design; **Discuss with Overseer** is the secondary
  action and opens a thread seeded with the full context of what was
  rated (screen, thread, the exchange itself).
- `interaction_feedback` spine on the Pi; the phone pushes its ratings
  over home-WiFi sync.
- **Squeeze** (which replaces the Dialectic tab) is the report card:
  model + task leaderboards from graded dispatches, plus a
  conversations section rolling up feedback by surface and model.

### MCP in both directions

- **Pi as MCP client**: register external streamable-HTTP MCP servers
  as connectors; their tools join the overseer's own tool loop as
  `mcp_<connector>_<tool>`. Auth headers are stored write-only and
  never echoed.
- **Skills + rules layer**: a living skills portfolio and a
  tech-decisions log on the Pi, written by AI sessions via new MCP
  tools (`cortex_skills`, `cortex_skill_log`, `cortex_rules`,
  `cortex_rule_add`). Every connecting AI receives the active rules
  digest in `cortex_intro`, so lessons learned in one session apply to
  all of them.
- **Harness map**: one succinct map of every screen and feature,
  served to the overseer so escalated conversations know what you were
  looking at.

### Simples on the desktop

- Read-only mirror of the phone's liquid planner; the phone pushes a
  snapshot on every home sync.
- **Day**: hour timeline with laned blocks over a downtime wash and a
  now-line. **Week**: 7 mini timelines. **Month**: planned-hours heat
  calendar. **Year** (new in stable): the year as texture, 12 month
  rows of day slivers whose brightness is that day's credited hours
  from your logged time. Click any day to open it.

### Real-time voice in the Hub

- Pipecat-based two-tier voice sidecar with barge-in and Kokoro TTS,
  launched from the Voice tab; full toolbelt, in-process sub-agents,
  multi-chat (save / list / resume), and a live dashboard.

## Also in this cycle

- **Hub IA overhaul**: Classify folded into Projects; Bell answerable
  from the phone; working-memory view leads with whole-corpus relevant
  context and renders recent decisions.
- **Lemon Squeezer export**: desktop connector, Settings toggle, and a
  System reporting panel for graded-dispatch sync.
- **People taxonomy**: dedicated Contacts panel with stats header;
  `person_notes` MCP tools with provenance/modality axes; aliases.
- **Search**: `cortex_search` gains semantic mode (vector recall
  parity with connectors); resubmitting a search refetches instead of
  replaying a cached error.
- **Sync**: the desktop daemon live-forwards phone sync to the Gateway
  per contract v2.
- **Performance**: code-split graph panels and vendor chunks (initial
  bundle down 41 percent); background polling pauses when the tab is
  hidden.
- **Guardrails**: personal-data/secret pre-commit hook + CI linter.
- Pi-side companions in cortex-core: chat threads schema, feedback
  routes with context injection, MCP connector client, tech
  skills/rules tables with the standing-rules intro digest, and
  `health_daily` (Fitbit Takeout backfill, 2,676 daily rollups).

## Upgrading

Settings -> Updates -> check on the **stable** channel, or download
`CortexHub-Setup-0.20.0.exe` from the release. Pi-side features need
cortex-core at commit `a6df73d` or later (already deployed on .25).
