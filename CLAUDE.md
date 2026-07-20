# Cortex Desktop — AI Agent Guide

This file provides context for AI agents working in the cortex-desktop repository.

## ☁️ Cloud migration (active direction, 2026-07-14)

Cortex is moving off the home Pi into Azure. The desktop splits: the GUI moves
to the cloud, and a small LOCAL Claude-file ingester stays. The Pi-proxy Hub
backend and the sync live-forward daemon retire. Read `docs/CLOUD_MIGRATION.md`
(and `cortex-core/docs/CLOUD_MIGRATION.md` for the full cross-repo plan) before
any Pi-proxy or sync-daemon work. Desktop changes are gated on the cloud app
existing; until then keep the Pi behavior working.

## 📬 Team mail — check FIRST

Two work streams build in parallel (desktop = this repo; mobile =
cortex-mobile + cortex-gateway + cortex-link). Async messages live in
`docs/team-mail/`: read **`TO_DESKTOP.md`** at the start of every session and
write to **`TO_MOBILE.md`** instead of blocking on the other stream. Currently
open for you: the phone↔dongle bridge integration asks — see
`docs/CORTEX_LINK_PHONE_BRIDGE.md`.

## What This Repo Is

Cortex Desktop is the PC-side control hub for the Cortex wearable AI companion system. It packages five main components:

1. **Desktop App** (`cortex_desktop/`) — System tray app that launches the Hub
2. **Hub Backend** (`hub/backend/`) — FastAPI REST API serving the frontend and proxying to the Pi
3. **Hub Frontend** (`hub/frontend/`) — React + Vite + Tailwind SPA
4. **MCP Server** (`cortex_mcp/`) — MCP bridge for Claude Code / Claude Desktop
5. **Training Package** (`cortex_train/`) — Unified training pipeline (Python API + CLI)

The Hub now ships a sixth surface that lives on the Pi but is consumed end-to-end through this app:

6. **Overseer** (Pi-side plugin in [cortex-core](https://github.com/turfptax/cortex-core)): a memory-upkeep agent that reads the user's notes, sessions, and imported Claude Code conversations and produces interpretive layers (gists, themes, episodes, open questions, patterns, drift, project narratives). Since the 2026-07-10 IA overhaul the Hub's top-level nav is **Search / Corpus / Chat / Simples / Journal / System / Settings**; the Corpus page holds the interpretive sub-tabs (Overview, Insights, Projects with Classify folded in, Squeeze which replaced Dialectic, Ecosystem, Explorer, Bell, Contacts, Voice). See `RELEASE_NOTES_0.20.md` and the memory note `hub_ia_overhaul_2026-07-10`.

## Architecture

```
Browser (localhost:8003)
    |
    v
FastAPI Backend (hub/backend/)
    |--- /api/chat      -> LM Studio (10.0.0.102:1234)
    |--- /api/pi/*      -> Orange Pi (10.0.0.25:8420, Basic Auth cortex:cortex)
    |--- /api/training/* -> cortex_train steps / subprocess pipeline
    |--- /api/settings/* -> Config, updates, MCP setup
    |--- /api/data/*     -> Pi database browser
    |--- /api/learning/* -> LM Studio synthesis
    |--- /api/overseer/* -> Pi /plugins/overseer/* (proxied — see hub/backend/routers/overseer.py)
    |--- static files   -> React SPA (frontend_dist/)

System Tray (cortex_desktop/app.py)
    |--- Starts uvicorn in background thread
    |--- Opens browser to Hub
    |--- Polls Pi status every 30s (green/red dot)

MCP Server (cortex_mcp/)
    |--- WiFi bridge -> Pi HTTP API (preferred)
    |--- Daemon bridge -> USB serial -> ESP32 -> BLE -> Pi (fallback)
```

## Key Directories

```
cortex-desktop/
  cortex_desktop/       # Desktop app: tray, config, updater
    app.py              # Entry point (uvicorn + pystray)
    config.py           # %APPDATA%/Cortex/config.json management
    updater.py          # GitHub release download + Inno Setup launch
  cortex_mcp/           # MCP server for Claude Code
    server.py           # FastMCP tool definitions (30+ tools)
    wifi_bridge.py      # HTTP Basic Auth bridge to Pi
    bridge.py           # Serial/BLE bridge (fallback)
    protocol.py         # CMD:/RSP: protocol helpers
    daemon.py           # Shared serial port daemon
  cortex_train/         # Training pipeline (Phase 1: shared modules)
    config.py           # TrainSettings dataclass from settings.json
    paths.py            # Auto-detecting TrainPaths
    prompts.py          # Stage prompts, moods, personality examples
    formats.py          # JSONL I/O, LLM response parser, ChatML
    lmstudio.py         # Teacher model API client
    pi_client.py        # SSH/SCP + HTTP API for Pi
    progress.py         # ProgressEvent callbacks for CLI/SSE
    errors.py           # Typed exception hierarchy
    steps/              # Pipeline step functions (Phase 2: in progress)
  hub/
    backend/
      main.py           # FastAPI app setup
      config.py         # Pydantic settings (CORTEX_HUB_* env vars)
      routers/
        chat.py         # LM Studio chat proxy (SSE streaming)
        pi.py           # Pi command proxy, pet care, tuck-in
        training.py     # Training pipeline, dream cycle orchestration
        settings.py     # Config, network scan, update check (stable/dev channels)
        data.py         # Pi database CRUD
        learning.py     # LM Studio synthesis management
        games.py        # Pong game endpoints
        overseer.py     # Proxy to Pi's /plugins/overseer/* — Slice 3+4+5 routes
                        #   working_memory, journal, dialectic, insights, blindspots,
                        #   notifications, explorer/graph, projects/summary,
                        #   narrative/generate, temporal/*, human-journal/*
                        #   (~30 routes total)
      services/
        pi_client.py    # Async HTTP client to Pi
        lmstudio.py     # Async LM Studio streaming client
        process_manager.py  # Training subprocess runner + SSE streaming
        learn_cycle.py  # In-process synthesis with multi-server work-stealing
        dataset_manager.py  # Curated dataset CRUD
    frontend/
      src/
        App.tsx         # Root, Pi status polling, routing
                        # Top-level sidebar: Chat / Pi / Data / Overseer / Settings
                        # (5 tabs as of v0.16; Training + Games are now Pi inner tabs)
        components/
          chat/         # Chat page with prompt templates + presets
          pi/           # Pi page with inner tabs: System (status + firmware) /
                        # Pet Chat / Pet Care / Thoughts (heartbeat) / Notes /
                        # Training / Games
          data/         # Pi database browser
          settings/     # Settings page with UpdateCard
          overseer/     # Overseer page (Slice 3-5)
            OverseerPage.tsx   # Tab dispatch + most panels
            ProjectsTab.tsx    # Slice 4 CP2: per-project narrative + stats cards
            JournalTab.tsx     # Slice 5 CP3+CP4: 3 sections — Your journal /
                               #   Temporal narratives (D/W/M) / Overseer
                               #   reflections (the original tick journal)
            ExplorerPanel.tsx  # Force-directed graph view
          training/     # Training pipeline UI (6 tabs) — mounted inside Pi tab
          games/        # Pong game — mounted inside Pi tab
        lib/
          api.ts            # apiFetch() wrapper
          graphengine/      # Generic force-directed graph engine
            GraphCanvas.tsx     # react-flow + d3-force canvas + floating edges
            useForceLayout.ts   # d3-force tick loop with prev-position carry
            types.ts            # EngineNode/EngineEdge interfaces
        hooks/
          useChat.ts    # Chat state, template resolution, memory compaction
          usePi.ts      # Pi communication helpers
  .github/workflows/
    build-release.yml   # CI: build exe + installer, create GitHub release
  build.py              # Build orchestrator (frontend + PyInstaller)
  installer.iss         # Inno Setup config for Windows installer
  cortex_desktop.spec   # PyInstaller bundle config
  pyproject.toml        # Package config (version, deps, entry points)
```

## Versioning & Releases

- **Version**: Defined in TWO places (keep in sync):
  - `cortex_desktop/__init__.py` → `__version__ = "0.16.0"`
  - `pyproject.toml` → `version = "0.16.0"`
- **Note** — `pyproject.toml` uses PEP 440 form (`0.16.0.dev13` for dev releases), `__init__.py` uses semver form (`0.16.0-dev.13`). Keep both in sync at each bump.
- **Sub-package versions**: `cortex_mcp/__init__.py` (0.3.0), `cortex_train/__init__.py` (0.1.0)
- **Git tags**: `v{semver}` for stable, `v{semver}-dev.{n}` for pre-releases
- **Bloom**: Training cycle counter in GGUF filenames (e.g. bloom-18)

### Release process
```bash
# Update versions in cortex_desktop/__init__.py AND pyproject.toml
git commit -m "Bump to v0.17.0 — description"
git tag v0.17.0
git push origin master v0.17.0
# GitHub Actions builds exe + installer, creates release
```

### Dev/pre-release
```bash
git tag v0.17.0-dev.1
git push origin v0.17.0-dev.1
# Creates pre-release on GitHub, visible via Dev channel in Hub
```

### Recent stable releases
- **v0.20.x** (Jul 2026): the agent-harness cycle. **0.20.0** = agent-harness chat (threads + prompt library + per-turn feedback), Squeeze replaces Dialectic, MCP in both directions (Pi is an MCP client for external servers; a skills/rules layer serves standing rules to every connecting AI via cortex_intro), the phone's Simples planner mirrored on the desktop, real-time Pipecat voice, Lemon Squeezer export. **0.20.1** = permanent memory in Simples (a "This day in Cortex" panel + a whole-corpus Year heat, any year). **0.20.2** = the Weekly Rhythm redesign (day-of-week-aligned year grid with a detached weekend band, a validated teal sleep layer, per-weekday median/IQR rhythm card). See `RELEASE_NOTES_0.20.md`.
- **v0.19.0** (Jun 2026): "real software" cycle: search-first Hub, semantic recall (sqlite-vec), phone bridge. See `RELEASE_NOTES_0.19.md`.
- **v0.18.0** (May 2026) — Agent ecosystem, voice, cost discipline, sensitivity tiers. Slices 5/6/7/8/9.x/10/10.4/13/14/14.5/14.6/14.7 + the work that made them stable. New Hub sub-tabs: Map, Activity. Router layer (Flash in front of Opus, ~1500× cheaper on routine chat). Voice mode (push-to-talk continuous conversation). Sensitivity tiers (confidential-IP handling). See `RELEASE_NOTES_0.18.md`.
- **v0.16.0** (May 2026) — Slice 3 Overseer (full) + Slice 4 Project-Centric (rollups, narratives, Projects tab) + sidebar reorg (7→5 tabs) + Polish slice. See `RELEASE_NOTES_0.16.md`.
- **v0.15.0** (Apr 2026) — Polish CP1 closeout: project name canonicalization + skipped-imports fix.

## Update System

- **Production**: Downloads Inno Setup installer from GitHub releases
- **Dev installs**: Falls back to `git pull --ff-only`
- **Channels**: `stable` (default, /releases/latest) or `dev` (includes pre-releases)
- **Endpoints**: `GET /api/settings/check-update?channel=dev`, `POST /api/settings/apply-update`
- **Frontend**: UpdateCard.tsx with Stable/Dev toggle (persisted to localStorage)

## Pi Communication

The Pi (Orange Pi Zero 2W) runs cortex-core on port 8420 with HTTP Basic Auth (`cortex:cortex`).

**Protocol**: JSON over HTTP
```
POST /api/cmd
{"command": "note", "payload": {"content": "...", "type": "note"}}
-> {"ok": true, "response": "RSP:note:{...}"}
```

**Key commands**: `ping`, `status`, `note`, `query`, `pet_sleep`, `pet_wake`, `tuck_in`, `force_train`, `dream_complete`, `training_examples`, `training_upload`

## Training Pipeline (cortex_train)

The training pipeline fine-tunes Qwen3.5-0.8B with LoRA adapters using data from the Pi.

### Pipeline steps
| Step | Name | What it does |
|------|------|-------------|
| 00 | sync | SCP cortex.db from Pi, export to JSONL |
| 01 | synthesize | LM Studio teacher generates Q&A from notes |
| 02 | prepare | Merge 5 data sources into HuggingFace Dataset |
| 03 | train | LoRA fine-tuning (GPU required) |
| 04 | evaluate | Perplexity comparison base vs fine-tuned |
| 05 | research | Hyperparameter search (incomplete) |
| 06 | deploy | Merge LoRA -> GGUF -> SCP to Pi -> restart |

### Dream cycle
Triggered by tuck-in: runs sync -> synthesize -> prepare -> train -> eval -> deploy automatically.

### Configuration
Training config lives in `cortex-pet-training/config/settings.json` (sibling repo). The `cortex_train.paths` module auto-detects this via `CORTEX_TRAINING_DIR` env var or sibling directory search.

## Overseer (Slice 3 + 4 + 5 + 6 + 7 — added in v0.13–0.17)

The Overseer is a memory-upkeep agent that LIVES on the Pi (in `cortex-core/plugins/overseer/`) but is consumed end-to-end through this Hub.

**LOCKED PRINCIPLE (Slice 5)** — restated as `SHARED_PRINCIPLE` at the top of every temporal-narrative prompt: *the Overseer is a quiet, lightweight memory layer that captures, surfaces, and connects. It is NOT a journaling app or life coach.* If a feature proposal would push it toward streaks, nag-notifications, suggested-actions, or "today's goal" surfaces, that proposal violates the principle and should be declined.

### What it does
- Reads the user's notes, sessions, and imported Claude Code conversations
- Runs a background loop on the Pi that produces interpretive layers via OpenRouter (Opus 4.7 + Sonnet 4.6 dialectic):
  - **Gists** — per-session summaries
  - **Themes / Episodes** — cross-session patterns
  - **Open questions** — standing concerns the user is working through, with evidence trails
  - **Patterns / Drift** — observations across time
  - **Working memory** — boot-read context for chat
  - **Journal** — first-person reflection by the overseer instance
  - **Dialectic** — paired Opus + Gemma generation; the diff is the data (slice 3f)
  - **Project summaries** — Slice 4: per-project rollups (stats + Sonnet narrative)
  - **Blindspots** — meta-honesty layer (slice 3f.5)
  - **Notifications** — the Hub bell
- Backfilled / refreshed via `cortex-core/scripts/backfill_session_stats.py` (one-shot)

### Hub-side surface
The Overseer page (top-level sidebar tab) has these inner tabs:
- **Overview** — Stats grid + Working Memory view + Imported Claude Sessions panel + Background Loop status + LLM Cost (last 7 days)
- **Chat** — Direct chat with the overseer (Opus 4.7 default, blindspot-aware)
- **Dialectic** — Resolve Opus-vs-Gemma diffs (slice 3f)
- **Journal** — *Slice 5 CP3+CP4:* three stacked sections — "Your journal" (free-form textarea + recent entries) / "Temporal narratives" (Daily/Weekly/Monthly Sonnet rollups, latest of each shown by default, "Generate now" per-card) / "Overseer reflections" (the original tick-based first-person journal)
- **Insights** — Pending interpretation queue (gists, themes, episodes, blindspots) with accept/reject
- **Projects** — *Slice 4 CP2:* per-project cards with narrative + active hours + cost + top files (sorted by Active hours desc by default; Active-only / All toggle)
- **Classify** — Per-project treat-as: human / automation / ignore (slice 3e). Slice 4 CP3 will absorb this into per-card menus on Projects.
- **Explorer** — Force-directed graph of nodes (questions / projects / patterns / drift / themes / gists / episodes) and edges (evidence / derived_from / in_project). Project nodes sized by active hours; dormants fade.
- **Bell** — Notifications grouped by rule_name (60d auto-archive)

### Slice 4 (Project-Centric) — v0.16
- Per-session token/cost/file extraction added to `claude_jsonl.py` (`extract_extended_stats`)
- `project_summaries` table on Pi: stats + LLM narrative + active_minutes_total
- `pricing.py`: hardcoded Anthropic price table (`as_of: 2026-05-02`)
- `project_summary.py`: deterministic stats aggregator (no LLM)
- `project_narrative.py`: Sonnet narrative generator (3 paragraphs + open questions section)
- Loop step 8 (`_run_project_narrative_refresh`): 24h cadence + ≥3 new sessions trigger, 3 projects per tick
- Manual route `POST /plugins/overseer/narrative/generate`
- Hub Projects tab consumes everything via `GET /api/overseer/projects/summary`

### Slice 5 (Temporal Cadence + Human Journal) — v0.17.0-dev.1+dev.2
- `temporal.py`: local-TZ helpers + `should_attempt_daily/weekly/monthly/yearly` (22:00 local triggers, hardcoded `TRIGGER_HOUR_LOCAL=22`)
- `temporal_narrative.py`: 4 prompt templates + gatherers + generators (yearly added Slice 5.6). Daily uses today-slice numbers from `imported_sessions` (NOT lifetime). Weekly synthesizes 7 dailies + cross-project signals. Monthly synthesizes the weeklies + open-question lifecycle (gated: skip if no daily in past 14 days). Yearly synthesizes the monthlies + carrying-forward observation (gated: skip if year had zero monthlies).
- `temporal_narratives` table — `UNIQUE(kind, period_label)` prevents double-generation
- `human_journal_entries` table — free-form, multiple per day allowed
- Loop **Step 0** (`_run_temporal_cadence` — moved from step 9 in Slice 5.5): runs FIRST so time-anchored narratives get budget priority. **BYPASSES the daily LLM budget** entirely (Slice 5.6) — these are once-per-period and missing the trigger window is permanent. Per-call cost cap ($0.05) still applies as the safety bound. Cost still logged to `llm_calls` for full audit; tick log records `temporal_bypassed_budget=True` when fired.
- Routes: `GET/POST /plugins/overseer/temporal*` and `GET/POST /plugins/overseer/human-journal*`
- Hub `JournalTab.tsx` consumes everything; the entire Journal tab was restructured into 3 sections (Your journal / Temporal narratives / Overseer reflections)

### Slice 5.5 (cadence calibration — v0.17.0-dev.5)
- `tick_interval_s` 300 → 900 (5min → 15min)
- Journal capped at 6 entries / local-day with 90-min cooldown (`loop_journal_max_per_local_day`, `loop_journal_min_minutes_between`)
- Loop steps reordered: temporal cadence Step 9 → Step 0
- DailyBudget reset switched UTC → local-day so the budget calendar matches the user's calendar AND the temporal-narrative period system
- See [memory/slice_5_complete.md](https://github.com/turfptax/cortex-desktop/blob/master/.claude/projects/...) for the bug timeline (root cause: budget exhausted before 22:00 local trigger because UTC reset at 19:00 CDT and `overseer-journal` step burned 70-80% of the daily budget)

### Slice 6 (People as first-class memory entity) — v0.17.0-dev.3+dev.4
- `overseer_people` table — namespaced to avoid collision with the simpler `people` table CortexDB owns. Audit trail (`created_by_agent`, `created_by_session_id`) on every row.
- `overseer_project_people` junction (project, person_id, role)
- 9 Pi-side routes (`/plugins/overseer/people/*`) + matching Hub proxies
- **9 MCP tools** (`cortex_people_*`) — the PRIMARY entry surface. Agents working with Tory in his other repos call these to capture relationships during work. Tool descriptions opinionated about WHEN to use vs WHEN NOT (recurring people in his work — yes; casual mentions, fictional names, code variables, AI agents — no).
- `cortex_people_stats` returns: total, added_24h/7d, orphans (no project links), multi_project (≥2 connectors), top_projects, top_expertise_tags, recent_additions with audit info.
- **CP2 (Hub UI) deferred** until ~30+ people accumulate from real agent capture. **CP3 (narrative integration)** held until Slice 5 soak completes.

### Slice 7 (Voice journal entries via local Whisper) — v0.17.0-dev.7→dev.13
- **whisper.cpp bundled in installer** — single ~5MB native binary. No Python deps for transcription; sidesteps the PyInstaller-bundle-vs-system-Python issue we hit on dev.7. CI step builds whisper.cpp from a pinned tag (`scripts/build_whisper_cpp.py`) before PyInstaller runs. Vulkan SDK installed via `jakoch/install-vulkan-sdk-action@v1.4.0` so the binary has GPU support compiled in (works on NVIDIA / AMD / Intel; falls back to CPU if Vulkan device init fails).
- Default model: `large-v3` (~3GB GGML file). Auto-downloads from HuggingFace on first transcription, cached at `%APPDATA%\Cortex\whisper-models\`. Single network call ever; after that, fully offline.
- Default settings: `whisper-cli -t <cpu_count>` (was capped at 4 — meaningful CPU speedup on multi-core boxes). Vulkan auto-detects GPU at runtime if compiled in.
- **Async with live progress (CP4):** POST `/api/transcribe` returns 202 immediately, kicks off a daemon thread, frontend polls `/api/transcribe/status` every 1.5s. Progress percentage parsed from whisper-cli's stderr (`progress = N%` lines from `-pp` flag) and surfaced in the textarea placeholder + 🎤 button label.
- **Refresh resilience:** Hub backend's transcribe state survives a browser reload. On-mount effect in `JournalTab.tsx` checks `transcribe_state.in_progress` and rebinds the polling loop. Subprocess + state outlive the original POST request.
- Privacy posture: file never leaves the host. Single network call ever is the one-time HuggingFace model download.
- Hub UI: 🎤 button next to "Save entry" in the human journal section. Accepts audio + video; ffmpeg normalizes everything to 16kHz mono WAV before whisper-cli runs.

### When to update what
- **Add a new overseer route** → modify `cortex-core/plugins/overseer/__init__.py` (Pi-side) AND `cortex-desktop/hub/backend/routers/overseer.py` (Hub proxy)
- **New schema column** → add to `OVERSEER_SCHEMA_SQL` in `cortex-core/plugins/overseer/overseer_db.py` AND chain an `_migrate_*` function for existing installs
- **New Hub tab** → register in `Tab` union + tab strip + dispatch in `OverseerPage.tsx`
- **Any user-facing feature** → update `cortex-core/memory/HARNESS_MAP.md` (the overseer's single map of every screen/feature) and redeploy it to the Pi

## Build & CI/CD

- **GitHub Actions**: `.github/workflows/build-release.yml`
  - Triggers on push to master (build only) or tag push (build + release)
  - Windows runner, Python 3.12, Node 20
  - Builds: frontend (npm) -> exe (PyInstaller) -> installer (Inno Setup)
- **Branch protection**: master requires `build` status check, no force pushes
- **Artifacts**: `CortexHub-windows-x64.zip` + `CortexHub-Setup-{version}.exe`

## Development

### Prerequisites
- Python 3.10+, Node.js 18+
- Pi accessible at 10.0.0.25 (or configure in %APPDATA%/Cortex/config.json)

### Run in dev mode
```bash
# Terminal 1: Backend
cd hub/backend && uvicorn main:app --reload --port 8000

# Terminal 2: Frontend
cd hub/frontend && npm run dev   # port 5173

# Or run the full desktop app
python -m cortex_desktop
```

### Testing the MCP server
```bash
python -m cortex_mcp.server   # stdio MCP server
# Or via CLI:
python -m cortex_mcp ping
python -m cortex_mcp note "test note" --tags test
```

## Related Repos

| Repo | Purpose |
|------|---------|
| [cortex-core](https://github.com/turfptax/cortex-core) | Pi firmware: pet engine, LLM, display, heartbeat |
| [cortex-link](https://github.com/turfptax/cortex-link) | ESP32-S3 BLE bridge (USB serial <-> BLE) |
| [cortex-mcp](https://github.com/turfptax/cortex-mcp) | Standalone MCP server (cortex-desktop bundles its own copy) |
| [cortex-pet-training](https://github.com/turfptax/cortex-pet-training) | Training scripts + data (being consolidated into cortex_train) |

## Git Hygiene

**HARD RULE: never `git add -A` or `git add .` from the cortex-desktop root.** Always stage an explicit file list.

The root contains `scripts/video-annotator/` and other ancillary tooling that can carry `node_modules/` dirs not yet in `.gitignore`. Concrete failure: the dev.29 commit (`a5e8048`) accidentally swept in 1007 files / 384k insertions (~380KB transit). The recovery commit (`7ae0e8b`) added those paths to `.gitignore`, but the lesson generalizes — stage files by name, every time.

## Common Tasks

### Adding a new API endpoint
1. Create or edit router in `hub/backend/routers/`
2. Register in `hub/backend/main.py` if new router
3. Add frontend API call via `apiFetch()` in `hub/frontend/src/lib/api.ts`

### Adding a new MCP tool
1. Add `@mcp.tool()` function in `cortex_mcp/server.py`
2. Tool sends commands via `send_command(_get_bridge_lazy(), "command_name", payload)`
3. Pi must handle the command in `cortex_protocol.py`

### Adding a training step
1. Create `cortex_train/steps/<name>.py` with `run_<name>(settings, paths, on_progress)` function
2. Register in `cortex_train/steps/__init__.py` STEP_MAP
3. Add CLI subcommand in `cortex_train/cli.py`
4. Hub backend calls via `cortex_train.steps` import or subprocess fallback

### Deploying code to Pi
```bash
scp cortex-core/src/*.py turfptax@10.0.0.25:~/cortex-core/src/

## Working Style — coding discipline

(Adapted from [Karpathy's CLAUDE.md](https://github.com/multica-ai/andrej-karpathy-skills/blob/main/CLAUDE.md).
Behavioral guidelines to reduce common LLM coding mistakes.)

**Tradeoff:** these bias toward caution over speed. For trivial tasks,
use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask: "Would a senior engineer say this is overcomplicated?" If yes,
simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria
("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in
diffs, fewer rewrites due to overcomplication, and clarifying
questions come before implementation rather than after mistakes.

ssh turfptax@10.0.0.25 "sudo systemctl restart cortex-core"
```
