# Cortex Voice Agent

A real-time, hands-free voice console for a [Cortex](https://github.com/turfptax/cortex-core)
memory system. You talk; it answers from your memory, looks things up online,
captures notes, and can spin up background sub-agents, all while you keep working.

Built on [pipecat](https://github.com/pipecat-ai/pipecat). It runs as a **sidecar
process** (its own Python venv), launched by the Cortex Hub or standalone; the
browser connects to it over local WebRTC.

## What it does

- **Real-time spoken conversation** with barge-in (interrupt any time).
- **A toolbelt** on the fast front model:
  - `search_memory` / `ask_overseer`: recall from your Cortex memory (fast search
    plus deep synthesis).
  - `web_search`: current information from the web.
  - `save_note` / `log_activity` / `log_time` / `journal`: capture into Cortex.
  - `list_projects` / `find_person`: look up your projects and contacts.
  - `dispatch_agent` / `check_agents`: hand a bigger job to a background sub-agent
    that researches while you keep talking (tiered models, per-task cost cap).
- **Multi-chat**: conversations autosave; start new ones or resume past ones from
  the activity panel, with no reconnect.
- **A live activity monitor**: every transcript turn, tool call, and sub-agent
  step, beside the voice UI.
- **States the model it runs on**, so "what model are you?" is answered honestly.

## Architecture

```
browser mic ─WebRTC─> [ Whisper STT ] -> [ front model + tools ] -> [ Kokoro TTS ] ─> speaker
                          (local)              (cloud, cheap)            (local)
                                                     │
                       ┌─────────────────────────────┼───────────────────────────┐
                       ▼                             ▼                            ▼
              your Cortex (cortex-core)        web_search (online)        background sub-agents
              search / notes / projects /      via OpenRouter             (tiered, cost-capped,
              people / deep overseer chat                                  visible in the monitor)
```

- The **front model** (`google/gemini-2.5-flash` via OpenRouter by default) owns
  turn-taking, barge-in, and tool selection. It answers small talk itself and
  calls tools when a turn needs memory, the web, or capture.
- **STT** is faster-whisper on-device (audio stays local). **TTS** is Kokoro
  on-device. Only transcribed text and answers leave the machine.
- **Sub-agents** run in-process: a background tool-using loop on an OpenRouter
  model (quick = Flash, deep = Sonnet, max = Opus), bounded by a per-task USD cap.

## Prerequisites

- **Python 3.12** (for the sidecar venv).
- **A running Cortex** (`cortex-core`) reachable over HTTP. See
  [Set up your own Cortex](#set-up-your-own-cortex) below.
- **An OpenRouter API key** ([openrouter.ai](https://openrouter.ai)). The front
  model, web search, and sub-agents all use it. The deep `ask_overseer` path uses
  whatever your Cortex is configured with.

## Setup

### One command

From the `cortex-desktop/` directory:

```
python voice_agent/setup.py
```

This creates the venv, installs dependencies, scaffolds the config files (without
overwriting anything you already have), and prints the next steps. Then add your
OpenRouter key (see below) and run.

### Manual

```
# 1. venv + deps (run from cortex-desktop/)
py -3.12 -m venv voice_agent/.venv
voice_agent/.venv/Scripts/python -m pip install -r voice_agent/requirements.txt

# 2. OpenRouter key  ->  ~/.cortex/secrets.toml
#    [openrouter]
#    api_key = "sk-or-v1-..."

# 3. (optional) personalize the voice  ->  %APPDATA%/Cortex/voice.local.toml
#    copy voice_agent/voice.local.toml.example and edit it

# 4. run (Windows: PYTHONUTF8=1 lets the startup banner print)
PYTHONUTF8=1 voice_agent/.venv/Scripts/python -m voice_agent.bot -t webrtc
```

Then open:
- `http://localhost:7860/` the voice UI (connect, allow mic, talk)
- `http://localhost:7861/` the activity monitor (chats, sub-agent tasks, transcript, tool calls)

## Configuration

Three optional surfaces; sensible defaults work out of the box once the OpenRouter
key is set.

**`~/.cortex/secrets.toml`** (required: the OpenRouter key)
```toml
[openrouter]
api_key = "sk-or-v1-..."
```

**`%APPDATA%/Cortex/voice.local.toml`** (optional, gitignored: personalize the
voice). Copy `voice.local.toml.example`. Without it, a generic persona is used.
```toml
[voice]
stt_vocab = "names, products, and jargon to bias speech recognition"
system_prompt = "You are ... (your spoken persona)"
```

**`%APPDATA%/Cortex/config.json`** (so the Hub can launch the sidecar)
```json
{ "voice_agent_python": "C:\\path\\to\\cortex-desktop\\voice_agent\\.venv\\Scripts\\python.exe" }
```

**Environment overrides**

| Var | Default | Notes |
|-----|---------|-------|
| `CORTEX_HOST` / `CORTEX_PORT` | `10.0.0.25` / `8420` | your Cortex (cortex-core) host |
| `CORTEX_USER` / `CORTEX_PASS` | `cortex` / `cortex` | Basic auth to Cortex |
| `OPENROUTER_API_KEY` | from `secrets.toml` | front model, web, sub-agents |
| `TIER1_MODEL` | `google/gemini-2.5-flash` | the front model |
| `VOICE_WEB_MODEL` | `google/gemini-2.5-flash:online` | web-search model |
| `VOICE_SUBAGENT_QUICK/DEEP/MAX` | Flash / Sonnet / Opus | sub-agent tiers |
| `VOICE_SUBAGENT_COST_CAP` | `0.50` | USD cap per sub-agent task |
| `KOKORO_VOICE` | `am_michael` | Kokoro voice id |
| `VOICE_STT_MODEL` / `VOICE_STT_DEVICE` | `distil-medium.en` / `cpu` | Whisper |
| `VOICE_STT_VOCAB` | generic | STT initial-prompt bias (or use `voice.local.toml`) |
| `VOICE_AGENT_PORT` / `VOICE_MONITOR_PORT` | `7860` / `7861` | ports |

## Running

- **Standalone**: the command in [Manual](#manual) above.
- **From the Cortex Hub**: set `voice_agent_python` in `config.json` (above), then
  open the Hub's Overseer > Voice tab and click Start. The Hub launches and
  supervises the sidecar; the Voice tab embeds the playground and the monitor.

### Using it

- **Talk** for memory ("what did I work on last week?"), the web ("what is the
  latest X?"), or capture ("save a note that ...").
- **Mute** to pause (do not disconnect; the prebuilt playground needs a page
  reload to reconnect, which then resumes your active chat).
- **Sub-agents**: "spin up an agent to research X." Watch it work in the Tasks
  panel, then ask "what did my agent find?"
- **Chats**: the monitor's Chats panel lists past conversations; click one to
  resume, or "+ New" to start fresh. Both switch in place, no reconnect.

## Set up your own Cortex

The voice agent is a front end to a Cortex memory system. You need two pieces:

1. **`cortex-core`** (the memory backend): runs on a Raspberry Pi or any host and
   serves the overseer, notes, projects, and people over HTTP. See
   [cortex-core](https://github.com/turfptax/cortex-core). Point `CORTEX_HOST` /
   `CORTEX_PORT` at it.
2. **The Cortex Hub** (this repo): the PC-side control surface. Install it from the
   [releases](https://github.com/turfptax/cortex-desktop/releases) page, or run it
   from source (see the repo root). The Hub launches this voice agent.

With those running and an OpenRouter key set, the voice agent connects to your own
memory, not anyone else's. Nothing here ships with personal data; persona and
vocabulary live only in your local `voice.local.toml`.

## Privacy

Audio never leaves the machine (Whisper STT and Kokoro TTS are on-device).
Transcribed text and answers go through OpenRouter (front model, web search,
sub-agents) and your own Cortex (the deep overseer path). Conversations autosave
locally under `%APPDATA%/Cortex/voice-chats/`.
