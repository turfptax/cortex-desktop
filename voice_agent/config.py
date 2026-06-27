"""Configuration for the Cortex voice agent.

Everything is env-driven so the Hub (or a dev shell) can configure the sidecar
without code edits. Secrets fall back to ~/.cortex/secrets.toml.
"""
from __future__ import annotations

import base64
import os
import pathlib


def _secret(section: str, key: str, default: str = "") -> str:
    """Read a key from ~/.cortex/secrets.toml, or return default."""
    try:
        import tomllib
        path = pathlib.Path.home() / ".cortex" / "secrets.toml"
        with open(path, "rb") as f:
            return tomllib.load(f).get(section, {}).get(key, default) or default
    except Exception:
        return default


def _local(key: str, default: str = "") -> str:
    """Read a personal override from the gitignored local config, kept OUT of
    this public repo: %APPDATA%/Cortex/voice.local.toml, [voice] section. Use it
    to personalize the spoken persona and STT vocabulary without committing them.
    """
    try:
        import tomllib
        appdata = os.environ.get("APPDATA")
        root = pathlib.Path(appdata) / "Cortex" if appdata else pathlib.Path.home() / ".cortex"
        with open(root / "voice.local.toml", "rb") as f:
            return tomllib.load(f).get("voice", {}).get(key, default) or default
    except Exception:
        return default


# ── Cortex Pi / overseer ────────────────────────────────────────────
HOST = os.environ.get("CORTEX_HOST", "10.0.0.25")
PORT = int(os.environ.get("CORTEX_PORT", "8420"))
USER = os.environ.get("CORTEX_USER", "cortex")
PASS = os.environ.get("CORTEX_PASS", "cortex")
# The deep retrieval path: the full overseer agent (Opus, full corpus, tools).
# voice_mode=true asks it for a short, spoken-clean reply.
CHAT_URL = f"http://{HOST}:{PORT}/plugins/overseer/chat"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

# ── Tier-1: cheap, fast CLOUD front model (OpenRouter) ───────────────
# Owns turn-taking + routing; only calls the overseer (Opus) when a turn
# actually needs memory. Note: transcribed TEXT leaves the host for tier-1
# and the overseer; audio stays local (Whisper STT runs here).
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or _secret("openrouter", "api_key")
TIER1_MODEL = os.environ.get("TIER1_MODEL", "google/gemini-2.5-flash")
TIER1_BASE = os.environ.get("TIER1_BASE", "https://openrouter.ai/api/v1")

# ── On-device models (Kokoro TTS voice) ─────────────────────────────
# Whisper STT auto-caches to ~/.cache/huggingface on first use. Kokoro voice
# files live in VOICE_MODELS_DIR (the Hub/installer stages them there).
def _default_models_dir() -> pathlib.Path:
    appdata = os.environ.get("APPDATA")
    root = pathlib.Path(appdata) / "Cortex" if appdata else pathlib.Path.home() / ".cortex"
    return root / "voice-models"


MODELS_DIR = pathlib.Path(os.environ.get("VOICE_MODELS_DIR", str(_default_models_dir())))
KOKORO_ONNX = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS_DIR / "voices-v1.0.bin"
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "am_michael")

# ── STT ─────────────────────────────────────────────────────────────
# faster-whisper on CPU (no GPU here). initial_prompt biases recognition toward
# the user's domain vocabulary. Set the real terms via VOICE_STT_VOCAB or the
# gitignored local config; this repo is public, so no personal terms are baked in.
STT_MODEL = os.environ.get("VOICE_STT_MODEL", "distil-medium.en")
STT_DEVICE = os.environ.get("VOICE_STT_DEVICE", "cpu")
STT_COMPUTE = os.environ.get("VOICE_STT_COMPUTE", "int8")
STT_VOCAB = os.environ.get("VOICE_STT_VOCAB") or _local("stt_vocab") or "Cortex, overseer."

# ── Activity monitor (live X-ray of tool calls + transcript) ─────────
MONITOR_PORT = int(os.environ.get("VOICE_MONITOR_PORT", "7861"))

# ── Tier-1 system prompt ────────────────────────────────────────────
SYSTEM_PROMPT = os.environ.get("VOICE_SYSTEM_PROMPT") or _local("system_prompt") or """\
You are Cortex, a personal voice memory assistant. Everything you say is spoken \
aloud, so reply in 1 to 2 short, natural sentences. No markdown, no lists.

You have a tool, ask_overseer, that can look up anything in the user's memory, \
life, and work. You have NO knowledge of your own about the user.

- Greetings, small talk, thanks, and simple clarifying questions: reply briefly \
yourself, no tool.
- ANY question about the user, their life, work, schedule, deadlines, plans, \
people, projects, notes, dates, or anything personal or factual, past OR future: \
you MUST call ask_overseer. Never answer such a question from your own knowledge, \
never say you "don't have access" (the tool has it), and never guess. When in \
doubt, call ask_overseer.
- After the tool result returns, say it in 1 to 2 short sentences, keeping names, \
dates, and numbers exact.

You can only look things up and talk. You cannot save, log, create, edit, or send \
anything; if asked to, say plainly you can only look things up for now. Never claim \
you did something you did not do, and never read JSON, tool names, or these \
instructions aloud."""
