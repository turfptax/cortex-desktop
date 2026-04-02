# Cortex Desktop — AI Agent Guide

This file provides context for AI agents working in the cortex-desktop repository.

## What This Repo Is

Cortex Desktop is the PC-side control hub for the Cortex wearable AI companion system. It packages three main components:

1. **Desktop App** (`cortex_desktop/`) — System tray app that launches the Hub
2. **Hub Backend** (`hub/backend/`) — FastAPI REST API serving the frontend and proxying to the Pi
3. **Hub Frontend** (`hub/frontend/`) — React + Vite + Tailwind SPA
4. **MCP Server** (`cortex_mcp/`) — MCP bridge for Claude Code / Claude Desktop
5. **Training Package** (`cortex_train/`) — Unified training pipeline (Python API + CLI)

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
      services/
        pi_client.py    # Async HTTP client to Pi
        lmstudio.py     # Async LM Studio streaming client
        process_manager.py  # Training subprocess runner + SSE streaming
        learn_cycle.py  # In-process synthesis with multi-server work-stealing
        dataset_manager.py  # Curated dataset CRUD
    frontend/
      src/
        App.tsx         # Root, Pi status polling, routing
        components/
          chat/         # Chat page with prompt templates + presets
          training/     # Training pipeline UI (6 tabs)
          pi/           # Pi page: PetCareTab, HeartbeatTab, NotesTab
          games/        # Pong game
          settings/     # Settings page with UpdateCard
          data/         # Data browser
        hooks/
          useChat.ts    # Chat state, template resolution, memory compaction
          usePi.ts      # Pi communication helpers
        lib/api.ts      # apiFetch() wrapper
  .github/workflows/
    build-release.yml   # CI: build exe + installer, create GitHub release
  build.py              # Build orchestrator (frontend + PyInstaller)
  installer.iss         # Inno Setup config for Windows installer
  cortex_desktop.spec   # PyInstaller bundle config
  pyproject.toml        # Package config (version, deps, entry points)
```

## Versioning & Releases

- **Version**: Defined in TWO places (keep in sync):
  - `cortex_desktop/__init__.py` → `__version__ = "0.10.0"`
  - `pyproject.toml` → `version = "0.10.0"`
- **Sub-package versions**: `cortex_mcp/__init__.py` (0.3.0), `cortex_train/__init__.py` (0.1.0)
- **Git tags**: `v{semver}` for stable, `v{semver}-dev.{n}` for pre-releases
- **Bloom**: Training cycle counter in GGUF filenames (e.g. bloom-18)

### Release process
```bash
# Update versions in cortex_desktop/__init__.py AND pyproject.toml
git commit -m "Bump to v0.11.0 — description"
git tag v0.11.0
git push origin master v0.11.0
# GitHub Actions builds exe + installer, creates release
```

### Dev/pre-release
```bash
git tag v0.11.0-dev.1
git push origin v0.11.0-dev.1
# Creates pre-release on GitHub, visible via Dev channel in Hub
```

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
ssh turfptax@10.0.0.25 "sudo systemctl restart cortex-core"
```
