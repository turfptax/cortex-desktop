# Cortex Desktop v0.18.0

**Released:** May 2026
**Theme:** Agent ecosystem, voice, cost discipline, sensitivity tiers

The 0.18 cycle promotes ~30 dev releases of work into a single stable release. It expands Cortex from a memory-layer with a single chat surface into a **multi-agent system with cost-aware routing, voice-mode conversation, on-device confidentiality, and a runtime/observability layer that lets you see what the agents are actually doing**.

Code lives across two repos:

- **cortex-desktop** (this repo) — Hub UI + backend proxies + Whisper bundling + MCP server
- **[cortex-core](https://github.com/turfptax/cortex-core)** — Pi-side overseer plugin, schema, all the agent logic

---

## Headline capabilities

### Sub-agents — Category B + C (Slice 10, 10.4)

The overseer now has staff. Two model tiers were added alongside the existing Opus-overseer + Claude Code siblings:

- **Category B agents** — stateless snapshot-on-demand specialists. Called as tools from chat or the journal step. Two ship in v0.18:
  - **`b_theme_check`** — calibration audit on a theme. Slices evidence by `contributed_at <= theme.created_at` as a structural defense against verdict-creep. Returns `[B:theme-check] <CALIBRATED | OVERCONFIDENT | UNDERCONFIDENT | INSUFFICIENT_EVIDENCE>`.
  - **`b_project_merge_check`** — independent verifier for project merges. Returns `[B:project-merge-check] <SAME | SUBPROJECT_OF_A | SUBPROJECT_OF_B | DISTINCT | INSUFFICIENT_DATA>`.
- **Category C agents** — patterns graduated from B. C-graduation is proposal-only (Tory accepts/rejects via Bell notification). Threshold: ≥10 dispatches + ≥7 rated 4+ in 7 days (lowered to 2/1/1 for shake-out testing).
- Every B/C output carries a syntactic `[B:...]` / `[C:...]` marker that survives consolidation passes — authorship attribution that downstream readers (Tory or another agent) can use to tell B/C work apart from overseer's own thinking.
- Marker preservation is enforced by prompt-level rules in every consolidation surface (gists, themes, episodes, temporal narratives, project narratives, insight scans) and backed by a meta-blindspot in `known_blindspots`.

### Router layer — Flash in front of Opus (Slice 14.7)

The dominant cost was the chat layer (~$0.107/turn at 113 turns/week = $12/week). The router addresses it head-on.

- New `POST /api/overseer/quick-chat` endpoint. Default chat path posts here.
- **Gemini 2.0 Flash** with **thin context** (~500–1000 tokens vs Opus's ~20k) answers routine factual/lookup questions in 1–3 sentences. Verified: **~$0.0001/turn vs $0.107/turn = ~1500× cheaper on routine.**
- Escalation rules:
  - Trigger words in user message: `overseer`, `@boss`, `think hard`, `deep think`, `strategize`, `long-term`, `reconcile`, `synthesize`/`synthesis`
  - 3+ consecutive router turns on the same thread → next turn auto-escalates
  - Flash itself emits `ESCALATE: <reason>` when its thin context can't answer well
  - `direct_override` flag from the Direct button bypasses the router
- Every assistant message carries an `answered_by` chip in the UI:
  - 🟢 **Router** (emerald) for Flash turns
  - 🟪 **Overseer ↑** (purple, with up-arrow) for escalated turns; hover shows the escalation reason
- Direct / Router toggle in the chat input row, persisted to localStorage.

### Voice mode for overseer (Slice 14)

A continuous spoken conversation with the overseer, on-device by default.

- One press on the **mic button** in the Overseer Chat tab enters voice mode; press stop to exit.
- Loop: **listening → transcribing → thinking → speaking → listening**
- Energy-based VAD (Web Audio AnalyserNode measuring RMS) ends each turn after ~1.2s of silence following speech. Echo suppression: mic stops listening during TTS playback.
- **On-device STT** via the bundled `whisper-cli` (large-v3, the same model the voice journal uses).
- **On-device TTS** via the browser's built-in `speechSynthesis` API.
- **Cloud upgrades available**: ElevenLabs TTS proxy at `/api/voice/tts`; key in `%APPDATA%/Cortex/config.json` enables it.
- When voice mode is on, the chat call carries `voice_mode: true` and the Pi-side persona appends a **succinctness directive**: 1–3 sentences, plain prose, no markdown, no preamble.
- Planned upgrade (locked, not yet built): Moonshine STT + Kokoro-82M TTS for higher-quality on-device voice. See `memory/slice_14_voice_plan.md` in the .claude memory.

### Sensitivity tiers — confidential-IP handling (Slice 13)

Cortex regularly sees confidential work (M&A, HIPAA-adjacent, executive comms). v0.18 ships a tiered system:

- New `imported_sessions.sensitivity` column: `public` / `internal` / `confidential` / `restricted`
- New `sensitivity_rules` table: cwd-pattern based, promote-only (rules can only raise tier, never lower)
- **Seeded rules:** `%lux%` → `restricted/no-import` (deliberately never imported); `%bwcs%`, `%/home/bwh%`, `%COO-Email%`, `%ToryMoghadam%`, `%Be Well%` → `confidential/gist-and-drop`; forward-looking rules for `rhd/hhs/nahm` contractor cwds.
- **Sanitized gist prompt** runs for confidential/restricted sessions: captures work *kind* + workstream + milestone; never figures, contract terms, party names, PHI, credentials, verbatim quotes. *"Confidential work session — \<domain\> — detail withheld by sensitivity policy"* is an explicitly acceptable complete gist.
- **Outbound sibling-dispatch filter**: scans the prompt + context_json for credentials, PII patterns, and references to confidential-tier session IDs before any `dispatch_sibling` call leaves the Pi. Refuses the dispatch on hit.
- Tier definitions are **provisional pending Tory's HIPAA/security review** — the plumbing is shipped; the legal-threshold layer is the user's call.

### Voice journal entries via local Whisper (Slice 7)

A 🎤 button next to "Save entry" in the human-journal section of the Journal tab.

- Accepts audio + video; ffmpeg normalizes to 16 kHz mono WAV.
- **whisper.cpp bundled in installer** — single ~5 MB native binary built in CI from a pinned tag, ships in the exe. No Python deps for transcription.
- Default model: `large-v3` (~3 GB GGML file). Auto-downloads once from HuggingFace on first transcription, cached at `%APPDATA%\Cortex\whisper-models\`. Single network call ever.
- **Vulkan SDK** in the CI build → GPU support compiled in. Falls back to CPU on Vulkan init failure or hard crash (with retry).
- **Async with live progress** — POST `/api/transcribe` returns 202 immediately, background thread runs whisper-cli, frontend polls every 1.5s for progress percentage. Survives browser refresh: state lives server-side, UI rebinds on mount.
- Privacy: file never leaves the host.

### Temporal narratives + Human Journal (Slice 5, 5.5)

The overseer now writes **daily / weekly / monthly / yearly** narrative rollups on local-time schedules.

- New table `temporal_narratives` with `UNIQUE(kind, period_label)` to prevent double-generation.
- Triggers at 22:00 local; once-per-period (missing the window is permanent — the loop has a Step 0 that BYPASSES the daily LLM budget so time-anchored narratives always get budget priority).
- Yearly skip-if-empty gate (no monthlies in the year → no yearly).
- Hub Journal tab restructured into three sections: **Your journal** (free-form textarea + recent entries) / **Temporal narratives** (D/W/M/Y cards with per-card "Generate now") / **Overseer reflections** (the original tick-based first-person journal).
- New `human_journal_entries` table — your free-form entries, multiple per day allowed.

### People as first-class memory entity (Slice 6)

The overseer can now track the recurring humans in your work.

- New `overseer_people` table with full audit trail (`created_by_agent`, `created_by_session_id`).
- `overseer_project_people` junction (project, person_id, role).
- **9 MCP tools** (`cortex_people_*`) — the primary entry surface. Agents working with Tory in his other repos call these to capture relationships during real work. Tool descriptions are opinionated about WHEN to use (recurring people, real relationships) vs WHEN NOT (casual mentions, fictional names, code variables, AI agents).

### Ecosystem Map + Activity tab (Slice 10.4)

Two new sub-tabs under Overseer that surface the agent layer.

- **Map** — static React Flow graph of the overseer's tool ecosystem. 5 hooks (boot / chat / journal_step / tick_scheduled / bell_action) × 16 tick steps × 36+ tools × B agents × C agents (live). Edges show callable relationships. Click any node for a detail sidebar.
- **Activity** — per-run trace viewer for the unified timeline of what overseer actually did. Filterable by run kind (B/C/sibling/chat/journal). Each run renders as a flow graph: trigger → LLM calls → tool calls → output. Click any node to see the data. Top-of-panel **"Export 24h bundle (JSON)"** button downloads a complete snapshot for offline review / bug reports.
- **Inline rating** on each rateable run (1-5 stars + comment + dataset_candidate flag), threading into `sibling_tasks.quality_rating` — same audit row the chat tool writes to.

### Chat attachments, markdown, slash commands, compression (Slice 8, 9.5)

- **File attachments** in the Overseer chat — drag-and-drop or paperclip button. Up to 10 files, 5 MB each. Text/PDF contents are inlined into the user message; images become multimodal content blocks. PDF text extraction via PyMuPDF, max 20 pages. Persisted as `chat_message_files` keyed to the user turn so history reloads correctly.
- **Markdown rendering** for assistant replies (GFM: tables, strikethrough, task lists). User messages stay plain to avoid surprising formatting.
- **Slash commands** in the chat input — intercepted before the LLM round-trip:
  - `/clear` / `/compress` — chat thread management
  - `/insights`, `/themes`, `/projects`, `/notifications`, `/journal` — Hub-internal jumps and lookups
  - `/dispatch <prompt>` — opens the sibling dispatch surface pre-filled with your prompt
- **`compress_chat`** — overseer can fold its own older turns into a Sonnet-summarized prefix when context bloats. Available via slash command and as a tool the overseer can call autonomously.

### Bell two-way + write tools (Slice 9.6, 9.7)

The notification system used to flow one direction (overseer alerts Tory). v0.18 closes the loop.

- **Custom action buttons** on notifications: `free_text` (textarea), `yes_no` (button pair), `dispatch_sibling`, plus arbitrary kinds that POST a payload overseer reads next tick.
- New `notification_responses` table; overseer reads pending responses each journal tick and acts on them.
- **Write tools** for overseer (callable from chat AND the tool-enabled journal step):
  - Project: `update_project_status`, `create_project`
  - Questions: `create_question`, `update_question_lifecycle`
  - Redaction: `redact_chat_attachment`, `delete_chat_message`, `redact_imported_session`, `redact_human_journal`
  - Notifications: `emit_notification` (with custom actions)
  - Synthesis: `file_evidence`, `propose_project_merge`
  - Notification responses: `get_pending_notification_responses`, `mark_notification_responses_processed`

### Tool-enabled journal step (Slice 9.9)

The overseer's tick journal can now CALL TOOLS, not just reflect. Same tool surface as chat (minus `dispatch_sibling` + `compress_chat`, which stay chat-only). Max 4 tool iterations per tick. This is what made the autonomous "respond to a Bell click overnight" workflow possible.

### Imported data (Slice 9.1)

- 906 grok-com conversations + 22 Twitter-Grok + 1,546 tweets = **3,012 new memory artifacts** brought 10.5 years of pre-Cortex history into the corpus.
- `imported_sessions` source taxonomy now distinguishes `claude-code` / `chatgpt` / `grok-com` / `grok-twitter` / `git:<owner>/<repo>` / `youtube:<channel>`.

### Git ingest (Slice 9.4)

Periodic GitHub commit ingester runs as part of the loop:

- `loop_git_ingest_enabled = true`, repos listed in `loop_git_ingest_repos`, polled every `loop_git_ingest_interval_hours`.
- Adds a `source:git:<owner>/<name>` channel to the gist origin distribution — captures *what Tory ships*, not just what he surfaces.
- Skipped-repos logged in overseer state so the freshness block can show what is NOT being seen.

### Time, with timezones, everywhere (Slice 9.4.1)

A discipline lock: every timestamp in the DB stores **both UTC and local-with-offset** (paired columns). Every display surface renders the local variant. Frontend `lib/time.ts` has shared helpers (`fmtTime`, `fmtRelative`). Reference pattern: `human_journal_entries`. The pre-fix bug: naked UTC at display read as future-dated entries between 19:00 CDT and midnight; that class of error is now structurally impossible.

### Cost discipline (Slice 14.5, 14.6, 14.7)

- **Daily LLM cap: $3/day** (down from $5). Target: ~$1/day typical.
- **Routine purposes routed to Gemini 2.0 Flash** (~30× cheaper than Sonnet): `auto-tag-notes`, `evidence-routing`, `insight-scan`, `distill-corrections`, `router-chat`.
- **Model attribution endpoint** `GET /api/overseer/llm/attribution?days=N` returns per-(model × purpose) breakdown with calls, total/avg cost, latency, avg input/output tokens, plus a `by_layer` rollup (router / overseer / routine / dialectic / other) with % of spend.
- **CEO directive** in the durable system prompts: overseer operates as the executive layer; delegates routine work to cheap models; reserves itself for high-judgment calls. *"Do not do work yourself that can be delegated. Stay expensive but worth every cent."*
- **Karpathy-adapted discipline principles** in the durable prompts: read the row (don't recall the frame); smallest claim that survives the evidence; close the loop; one artifact per truth; stupid-simple baseline first; cheap experiments first; honest about what you don't know.
- **Paired-generation dialectic disabled** (Slice 3f): 3 weeks of accumulation produced 160 unresolved diff rows whose divergences were uniformly stylistic, not interpretive. The 160 backlog was bulk-resolved as `no-action`; `loop_paired_generation = false` going forward.

---

## Hub UI surface map (as of v0.18.0)

Top-level sidebar: **Chat / Pi / Data / Overseer / Settings**

Overseer inner tabs:

- **Overview** — stats grid + working memory + import panel + loop status + LLM cost
- **Chat** — Opus chat (now via the **router by default** with **Direct** override + per-message layer badges)
- **Dialectic** — paired-gen diff queue (now empty by design; paired generation disabled)
- **Journal** — Your journal (free-form + voice) / Temporal narratives (D/W/M/Y) / Overseer reflections
- **Insights** — pending interpretation queue (themes, patterns, drift, merge proposals, blindspot proposals) with accept/reject
- **Projects** — per-project narrative cards
- **Classify** — per-project human/automation/ignore
- **Explorer** — force-directed graph of the interpretive layer
- **Map** *(NEW in 0.18)* — the overseer's tool ecosystem
- **Activity** *(NEW in 0.18)* — per-run trace viewer with rating + 24h export
- **Bell** — notifications with custom action buttons

---

## Cost reduction expected from v0.18

Pre-router baseline: ~$1.94/day average, **70% on the chat layer** (`overseer-chat` at $0.107/turn × ~16 turns/day).

With the router layer active (v0.18.0):

- Routine turns: $0.107 → $0.0001 (~1500× cheaper)
- Typical 20-turn day (15 routine + 5 needing-overseer): **$2.14 → $0.54 chat layer**
- Routine loop work (Flash) stays ~$0.20–0.30/day
- **Total target: ~$0.75–1.00/day** under typical use

---

## Versioning

- **Stable**: v0.18.0 (this release)
- **Previous stable**: v0.16.0 (May 2026)
- **Dev cycle that fed this release**: v0.17.0-dev.1 → v0.18.0-dev.32 (~30 dev releases)
- **Sub-package versions**: `cortex_mcp/__init__.py` 0.5.0, `cortex_train/__init__.py` 0.1.0
- **Pi-side cortex-core**: deployed via scp; current tip is on master post-`f34b8c8` (paired-gen disabled).

---

## Migration notes

For installs upgrading from v0.16.0:

1. **Update the Hub** via Settings → Check for update (stable channel).
2. **Pi-side updates** are continuous via scp / `sudo systemctl restart cortex-core`. The cortex-core repo at HEAD has all the schema migrations chained — they apply on next service start. No manual SQL needed.
3. **Whisper model** auto-downloads on first voice transcription (~3 GB). Subsequent transcriptions are fully offline.
4. **Sensitivity backfill**: existing imported_sessions get tagged on next `/sensitivity/backfill` POST (or one-time at install via the migration's seeded rules). Verified during this cycle: 52 work-machine sessions correctly tagged `confidential`; 3,278 → `public`.
5. **Dialectic queue**: anyone upgrading will find their queue is empty by design; paired generation is off. To re-enable, flip `loop_paired_generation = true` in `plugins/overseer/plugin.toml`.

---

## Repos used during this cycle

- [cortex-core](https://github.com/turfptax/cortex-core) — Pi plugin
- [cortex-desktop](https://github.com/turfptax/cortex-desktop) — Hub, this repo
- [cortex-link](https://github.com/turfptax/cortex-link) — ESP32-S3 BLE bridge
- [cortex-pet-training](https://github.com/turfptax/cortex-pet-training) — Training scripts + data
- [cortex-pet](https://github.com/turfptax/cortex-pet) — Pet plugin (extracted from cortex-core in Slice 11)
