# Cortex Voice Agent

A real-time, two-tier spoken interface to the Cortex overseer, built on
[pipecat](https://github.com/pipecat-ai/pipecat). You talk; it answers from your
memory.

It runs as a **sidecar process** (its own Python venv), not inside the Hub's
PyInstaller exe. The Hub launches and supervises it; the browser connects to it
over local WebRTC.

## Architecture

```
browser mic ─WebRTC─> [ Whisper STT ] -> [ tier-1 model ] -> [ Kokoro TTS ] ─> browser speaker
                            (local)          (cloud, cheap)       (local)
                                                  │ ask_overseer (memory question)
                                                  ▼
                                   Pi /plugins/overseer/chat  (Opus, full corpus)
```

- **Tier 1** (`google/gemini-2.5-flash` via OpenRouter) owns turn-taking,
  barge-in, and the conversation. It answers small talk itself and decides when
  a turn actually needs memory.
- **`ask_overseer`** is the one tool. For any memory/factual question it calls
  the full overseer agent on the Pi (Opus, full corpus) and relays the answer.
  Cheap front, deep model only when needed.
- **STT** is faster-whisper on-device (audio stays local), biased with a domain
  vocabulary. **TTS** is Kokoro on-device.
- **Privacy:** audio stays on the box; the transcribed text + corpus answers go
  through OpenRouter / the overseer (which already uses OpenRouter).

## Run standalone

```
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
# Windows: PYTHONUTF8=1 is required so the runner banner can print.
PYTHONUTF8=1 .venv/Scripts/python -m voice_agent.bot -t webrtc
```

Then open:
- http://localhost:7860/ - the voice UI (connect, allow mic, talk)
- http://localhost:7861/ - the activity monitor (live transcript + tool calls)

Run from the `cortex-desktop/` directory so `voice_agent` is importable.

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `CORTEX_HOST` / `CORTEX_PORT` | `10.0.0.25` / `8420` | the Pi running the overseer |
| `CORTEX_USER` / `CORTEX_PASS` | `cortex` / `cortex` | Basic auth |
| `OPENROUTER_API_KEY` | from `~/.cortex/secrets.toml [openrouter]` | tier-1 model |
| `TIER1_MODEL` | `google/gemini-2.5-flash` | any OpenRouter model |
| `VOICE_MODELS_DIR` | `%APPDATA%/Cortex/voice-models` | Kokoro voice files |
| `KOKORO_VOICE` | `am_michael` | Kokoro voice id |
| `VOICE_STT_MODEL` / `VOICE_STT_DEVICE` | `distil-medium.en` / `cpu` | Whisper |
| `VOICE_STT_VOCAB` | Cortex domain terms | STT initial-prompt bias |
| `VOICE_MONITOR_PORT` | `7861` | activity monitor |

## Status

Graduated from the `_spikes/` prototype (2026-06). Working: real-time barge-in
voice, two-tier routing, overseer retrieval, honest capability limits, live
activity monitor.

Pending: Hub launch/supervise wiring, a Hub voice tab (WebRTC client + activity
panel), a real `save_note` write tool (`/plugins/overseer/human-journal`), an
STT speed path (GPU Whisper or a cloud STT), and packaging (bundled venv).
