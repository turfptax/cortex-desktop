# Reinventing cortex-desktop: the local ingestor for CortexGraph

Written 2026-08-28 at Tory's direction. Supersedes CORTEX_AGENT_PLAN.md
(2026-07-24), which targeted Cortex-Cloud; that backend retired in the
2026-08-11 succession (CortexGraph/docs/CORTEX-SUCCESSION.md). CP1 of the
old plan shipped as v0.22.0 and fed the old corpus; its transport
(/files/uploads + imports/from-path, Basic auth) has no equivalent on
CortexGraph and is dead code walking. This document is the new spec of
record for what cortex-desktop becomes.

Grounded in a source-verified sweep of C:/dev/ttx/CortexGraph,
C:/dev/ttx/cortex-intake, and the deployed surface (2026-08-28). File
paths cited are real.

---

## 1. The mission, reframed

The old desktop was a window onto the corpus (Hub, DB browser, chat).
The new Cortex has no need for any of that: the graph has its own web
UI, its own chat (Overseer), and every AI reaches it over MCP.

What remains is the one thing only a process on this machine can do:
**turn local reality into graph food.** Claude Code sessions, files,
media, exports, device data. The cloud cannot read this disk; the
desktop can. So:

> cortex-desktop becomes the TRANSDUCER TIER: it watches and receives
> local sources, converts anything non-text into text and structure
> locally, and feeds the graph through the same MCP connector surface
> every other client uses. It renders nothing, stores nothing of
> record, and holds no schema opinion the graph does not already have.

The one-line test for any proposed feature: does it move local bytes
toward the graph as text and structure? If not, it belongs in
CortexGraph's web app, not here.

## 2. What the graph actually accepts (the ground truth)

Verified against src/cortexgraph/v2/ (the v2 build is the only target;
v1 is being retired at Phase G):

- **The only write door is MCP tools** at https://cortex.turfptax.com/mcp
  (streamable HTTP; OAuth 2.1 via the self-hosted Entra facade, scope
  memory.access, single-owner UPN lock). There is no REST ingest, no
  file upload route, no sync contract. The house principle is MCP
  COMPLETENESS: capabilities ship as connector tools for every client,
  never bespoke endpoints. The desktop must live by that rule too.
- **Write tools that matter to us:** log_memory (the content door,
  never policy-gated; text + project + organization + occurred_at),
  journal_add, task_add, activity_log (minutes against a project or
  simple, onto a day), episode_log (the agent plane: goal, outcome,
  harness, touched ids, uncapped narrative), skills_import,
  skill_nominate. Entity minting (project/person/org/goal/simple) is
  policy-gated; log_memory never mints, it reports unlinked names.
- **Storage:** Cosmos Gremlin holds the graph (what it knows), Postgres
  + pgvector holds the research layer and embeddings (how it learned
  it), Azure Blob holds payload text. The payload store is TEXT ONLY
  (PayloadStore.put(key, text: str)); there is no bytes path, no
  attachment label, no media anywhere in the schema.
- **Everything is an episode first**; the graph is a rebuildable
  projection of an append-only log. Contradictions invalidate, nothing
  is overwritten. Embeddings are swept (v2 vectorize cron), not
  write-time, so fresh captures lag search by up to a sweep. Enrichment
  (linking) lags by up to 15 minutes by design.
- **Auditing:** every tool call lands in a hash-chained tool_calls log
  with caller_client (OAuth identity) vs caller_claimed (self-reported
  harness). The desktop should always claim agent_harness
  cortex-desktop honestly.

Two implications fall straight out of the text-only payload store:

1. **Media ingestion means local transduction, not upload.** Whisper
   turns audio into transcripts, the vision stack turns video into
   scene narratives, OCR turns PDFs and screenshots into text. The
   graph never sees a byte of media; it sees what the media SAID. This
   is not a workaround, it is the architecture: the desktop owns the
   GPU and the bytes, the graph owns meaning. (If raw-artifact
   preservation is ever wanted, that is a server-side decision to add
   an attachment path as an MCP tool; section 9, open question 3.)
2. **Conversations are distilled, not dumped.** The old world pushed
   whole .jsonl files for server-side gisting. The new world has
   cortex-intake's conversations recipe (triage then distill) and the
   CLAUDE-TIME-BRIEF rails. A 204MB session file is no longer a
   transport problem, because the transport carries conclusions, not
   transcripts.

## 3. The three-plane model for Claude Code sessions

The richest local source, and the graph gives it three distinct
landing rails (this is the "novel way": one session feeds three
planes instead of one blob):

| Plane | Tool | What the desktop extracts |
|---|---|---|
| Content | log_memory | Distilled decisions, findings, and facts from the session (the conversations recipe's triage + distill pass), each stamped occurred_at and project |
| Agent | episode_log | One episode per session: goal, outcome, harness, model, touched node ids, narrative. The graph's own provenance vocabulary |
| Time | activity_log | Human-involvement minutes per project, computed from the session file (origin.kind == "human" events; the CLAUDE-TIME-BRIEF cap/floor model) |

cortex-intake/docs/CLAUDE-TIME-BRIEF.md (2026-08-20) already specs the
time parser and names this machine's watcher heritage explicitly. The
brief's four open questions (CAP value, attribution granularity,
backfill horizon, credential strategy) get answers in section 9.

What survives from the v0.22 watcher is the DISCIPLINE, not the
transport: idle gating, (size, mtime) pre-filter before hashing,
newest-first ordering, per-cycle caps, poison cooldown, atomic state
writes, a rotating agent.log. That loop was verified in production for
a month; it wraps the new extraction instead of the old upload.

## 4. The relationship with cortex-intake (decide once, early)

cortex-intake (C:/dev/ttx/cortex-intake) already is a local ingestion
engine: recipe framework (fitbit, conversations, gists), SQLite
staging with per-item idempotency keys, approve/reject flow, a
localhost review UI on 127.0.0.1:8811, and a Gateway seam. Rebuilding
any of that in cortex-desktop would be a fork of a working engine.

**Recommendation: converge.** cortex-intake becomes the ENGINE
(library + recipes + staging + gateway seam); cortex-desktop becomes
the SHIP (installer, tray, auto-update, background watcher scheduling,
media transducers, first-run auth). The desktop repo's crown jewels
are exactly the things intake lacks: the Inno installer + tagged
release pipeline, the stable/dev auto-updater, the tray shell,
whisper.cpp bundling with GPU fallback, and a month-proven watcher
loop. Intake's crown jewels are exactly what the desktop needs next:
recipes, staging idempotency, review flow, and the graph vocabulary.

Concretely: cortex-desktop grows a dependency on cortex-intake (path
dep in dev, vendored or published package at ship time), schedules its
recipes from the tray process, and contributes two new recipe families
(claude-time and media transduction) upstream into intake.

## 5. The credential problem is the first real build (McpGateway)

Today cortex-intake writes through DirectGateway: raw Cosmos and
Postgres credentials from .env. That is fine on the machine that owns
the graph and unacceptable everywhere else. The brief's option (c) is
the right long-term: a **McpGateway** implementing intake's Gateway
protocol by calling the connector tools at cortex.turfptax.com/mcp
over OAuth. The seam is already documented in
cortex-intake/src/cortex_intake/gateway.py; only DirectGateway exists.

Why this is the keystone and not a nicety:

- It is the ONLY path that lets a second machine (or a friend's
  machine, in the SaaS future) ingest without holding master database
  credentials.
- It makes the desktop a plain MCP client, identical in kind to the
  new phone. One auth story, one audit trail, one completeness gate.
- It exercises the connector surface the product actually sells.

Auth flow for a headless-capable desktop app: the facade already
supports dynamic client registration + PKCE (it exists precisely
because MCP clients need it). First run: register a client, loopback
browser flow (RFC 8252, the pattern from the July prep doc), owner
signs in with Entra, store the refresh token under DPAPI. The facade
mints opaque, revocable tokens; AUTH_ALLOWED_UPN already locks the
instance to Tory. If the facade lacks refresh-token issuance for
long-lived headless clients, that is a small CortexGraph work item to
confirm early (section 9, open question 2).

Batching note: MCP tool calls are one-at-a-time writes. For backfills
(hundreds of memories) DirectGateway on the graph's home machine
remains the bulk path; McpGateway is the steady-state and
remote-machine path. Both implement the same Gateway protocol, so
recipes do not care.

## 6. Media: the transducer catalog

Every transducer ends in text + structure landed via log_memory (and
activity_log/episode_log where time or agency is real). All local, all
idempotent by content hash in intake staging.

Ship in order of proven value:

1. **Audio/voice** (exists in-repo): whisper.cpp, bundled, GPU with
   CPU fallback. Voice memos, meeting recordings. Lands transcript
   with occurred_at = recording time.
2. **Video** (exists via cortex-vision sidecar): scene detection +
   description + audio transcript; lands the narrative text. The
   empty-narrative fallback (deterministic concat) carries over from
   the old plan.
3. **Documents**: PDF/DOCX/TXT dropped on the tray or a watched
   folder; text extraction (+ OCR for scans); lands as memory per doc
   or chunked by section for long ones.
4. **Screenshots/images**: OCR + a one-line local caption when a
   vision model is available; degrade to OCR-only.
5. **Claude Desktop chats**: the Anthropic Data Export ZIP route
   (unzip, convert to conversations.jsonl, feed the conversations
   recipe). The old plan's deferred item, now nearly free because the
   recipe exists.

## 7. The source brainstorm (beyond media)

Ranked by signal-per-effort; each is a recipe, so each is independent:

- **Claude Code sessions** (section 3). The flagship, three planes.
- **Git history**: one episode_log per meaningful commit cluster in
  watched repos (goal from message, touched from paths, harness from
  the trailer convention). Cheap, high-signal provenance the graph's
  agent plane was literally shaped for.
- **Fitbit/Google Takeout**: recipe exists in intake, zero LLM cost.
- **Browser history export**: daily digest of research trails into a
  memory per day (triage hard; default OFF, owner opt-in).
- **Email/calendar exports**: .ics and mbox drops through the
  document recipe with sender/attendee extraction. Minting policy
  keeps stray names from becoming person nodes.
- **OpenMuscle / sensor data**: future; lands as simples/activity
  rather than memories.
- **Clipboard / screenshot hotkey capture**: a manual "remember this"
  accelerator in the tray. Small, delightful, entirely local.

Everything above obeys the same three rules: idempotent staging keys,
respect minting policy (never yolo-mint entities from noisy sources),
and occurred_at is the source's time, not ingest time.

## 8. What survives, what dies (the strip-down, revised)

The old CORTEX_AGENT_PLAN section 6 delete list mostly stands; the
succession makes it MORE radical because the proxy client itself dies:

- KEEP: tray shell, installer + CI release pipeline, auto-updater,
  whisper bundling + build script, watcher-loop mechanics (as a
  library), the Claude-session parsing knowledge, cortex-vision
  sidecar management.
- DELETE (after the new path is live, never before): the entire hub/
  tree, pi_client and every /core-proxy caller, the sync-contract
  code (wifi_bridge CMD/RSP, daemon), the 56-tool desktop MCP server
  (the 2026-08-04 sunset ledger finishes itself: CortexGraph serves
  the real MCP surface; a desktop MCP server is split-brain by
  definition), lemon exporter (confirm rehoming first), the v0.22
  two-step pusher.
- The People data question from the sunset ledger carries forward:
  contacts and person notes were deliberately never ported to the old
  cloud. The graph HAS a person label but minting is policy-gated and
  People-adjacent ingestion stays owner-approved, never automatic.

## 9. Open questions for Tory (defaults proposed)

1. **Repo convergence** (gates everything): bless the engine/ship
   split in section 4? Default: yes; cortex-desktop depends on
   cortex-intake, both stay separate repos.
2. **Auth for headless ingest**: confirm the facade can mint
   refresh-capable tokens for a registered desktop client (or add
   that). Fallback until then: run desktop ingest only on the machine
   that holds DirectGateway credentials. Do NOT ship master DB creds
   to a second machine.
3. **Raw artifact policy**: is distill-only the permanent posture, or
   should CortexGraph grow an attachment path (bytes in blob, an
   attachment node, an MCP tool so every client gets it)? Default:
   distill-only now; revisit when a concrete recall need appears. The
   204MB session archive (C:/Users/User/CortexArchive) stays a local
   archive either way.
4. **CLAUDE-TIME-BRIEF's four opens**: proposed defaults: CAP 15 min
   per human gap, per-project attribution by session cwd mapping
   (.claude/cortex.json convention from the hooks integration),
   backfill to 2026-08-11 (the succession; older time lives in the
   old corpus import), credentials per question 2.
5. **The localhost review UI**: intake's 8811 approval surface stays
   (it is an ingest control, not a Hub regrowth), but confirm the old
   "agent listens on no port" guardrail is formally relaxed to
   "localhost-only, ingest-control-only".

## 10. Sequencing (checkpoints, each shippable alone)

- **GI0 (CortexGraph side, small): confirm/extend facade auth** for a
  registered headless client (refresh tokens). Blocker for GI2+ off
  the home machine; nothing else waits on it if GI1 runs where
  DirectGateway already works.
- **GI1: claude-time + conversations watcher, home machine.** Build
  the claude-time recipe per the brief, wire the v0.22 watcher loop
  around intake's conversations + claude-time recipes, DirectGateway
  transport, tray-scheduled. Exit: a day of real sessions lands
  memories + episodes + minutes, idempotent on re-run, visible in
  day_detail.
- **GI2: McpGateway.** Implement the Gateway protocol over /mcp with
  OAuth; contract-test it against DirectGateway on the same recipe
  run (same staged items, same graph deltas). Exit: the watcher runs
  end to end with zero database credentials on disk.
- **GI3: media transducers.** Whisper + document + image recipes
  through intake staging; drop zone + review flow. Exit: an audio
  memo and a PDF land as searchable memories after the next sweep.
- **GI4: the ship.** Rebrand the installer (Cortex Ingest Agent),
  first-run OAuth, strip-down per section 8, release as v1.0.0 of
  the reinvented app. Exit: clean install on a second machine feeds
  the graph with no secrets beyond its own OAuth grant.
- **GI5: source expansion.** Git-history episodes, takeout recipes,
  clipboard capture, per section 7 priorities.

## 11. Risks and flags

- **Secrets on disk (flagged 2026-08-28, needs Tory's eyes):**
  C:/dev/ttx/CortexGraph/.env and C:/dev/ttx/cortex-intake/.env both
  carry the live Cosmos key and the Postgres password in plaintext.
  Verify both files are gitignored in their repos and consider
  rotation if either ever left this machine. McpGateway (GI2) is the
  structural fix: ingest stops needing them at all.
- **Split-brain window**: until GI4 deletes it, the old desktop MCP
  server still serves 56 tools against a dead backend to any client
  still configured for it. Worth an early kill notice in Claude
  Desktop/Code configs rather than waiting for the strip-down.
- **Search lag**: captures are not searchable until the vectorize
  sweep; the ingest UX must not promise instant recall (status line:
  "landed, searchable within the hour").
- **LLM cost of distillation**: the conversations recipe spends
  tokens per session. The watcher inherits the old budget discipline:
  per-cycle caps, a daily distillation budget, and triage-first so
  low-signal sessions cost near zero.
- **Minting hygiene**: bulk sources + yolo commits could flood
  pending entities. Recipes default to interactive approval for any
  run that would mint; fully automatic runs restrict themselves to
  capture labels (memory, journal, episode, activity).
