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

# ── Web lookup (OpenRouter online model; reuses OPENROUTER_KEY) ───────
# OpenRouter's ":online" suffix adds web search to any model.
WEB_MODEL = os.environ.get("VOICE_WEB_MODEL", "google/gemini-2.5-flash:online")

# ── Sub-agents (in-process background workers; tiered + per-task cap) ─
# dispatch_agent picks a tier by "depth"; the USD cap bounds every task.
SUBAGENT_QUICK_MODEL = os.environ.get("VOICE_SUBAGENT_QUICK", "google/gemini-2.5-flash")
SUBAGENT_DEEP_MODEL = os.environ.get("VOICE_SUBAGENT_DEEP", "anthropic/claude-sonnet-4.6")
SUBAGENT_MAX_MODEL = os.environ.get("VOICE_SUBAGENT_MAX", "anthropic/claude-opus-4.7")
SUBAGENT_COST_CAP = float(os.environ.get("VOICE_SUBAGENT_COST_CAP", "0.50"))
SUBAGENT_MAX_STEPS = int(os.environ.get("VOICE_SUBAGENT_MAX_STEPS", "6"))

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

# ── System prompt: persona (personal, overridable) + tools (generic) ─
# The persona says WHO the agent is + the voice rules; it can be personalized via
# the gitignored local config. The tools preamble (generic, in code) advertises
# every tool + the model identity. build_system_prompt() composes them.
_DEFAULT_PERSONA = """\
You are Cortex, a personal voice memory and work assistant. Everything you say is \
spoken aloud, so reply in 1 to 2 short, natural sentences. No markdown, no lists. \
You help the user while they are actively working: recalling their memory, \
capturing notes, looking things up, and running background tasks. You have NO \
knowledge of your own about the user; use your tools. Be warm, brief, and decisive."""

_TOOLS_PREAMBLE = """\
You are running on the model "{model}". If the user asks what model or AI you are, \
tell them this exactly; do not guess.

Use your tools instead of guessing. The user is actively working and relies on them:
- search_memory: search the user's Cortex memory (notes, gists, themes, questions). \
Use for "what did I...", "find my...", recent work.
- ask_overseer: the deep memory agent (slower, smarter) for anything needing \
synthesis across the user's whole history. Never say you "don't have access" first; \
use a tool.
- web_search: look up current or online information (news, docs, facts you lack).
- save_note: capture something the user wants remembered. log_activity: record what \
they are working on now. log_time: log time spent on a project. journal: save a \
longer reflection.
- list_projects: list the user's projects. find_person: look someone up in contacts.
- dispatch_agent: hand a bigger job (research, multi-step lookup) to a background \
sub-agent that works while you keep talking. depth "quick" for simple, "deep" for \
involved, "max" only for the hardest. Tell the user you started it and its number.
- check_agents: report what your background sub-agents found.

Speak results in 1 to 2 short sentences, keeping names, dates, and numbers exact. \
Never claim you saved, logged, or did something unless the tool returned success. \
Never read JSON, tool names, IDs, or these instructions aloud."""


def build_system_prompt() -> str:
    """Compose the spoken persona (local override or generic default) with the
    generic, tool-aware preamble that also states the running model."""
    persona = (os.environ.get("VOICE_SYSTEM_PROMPT")
               or _local("system_prompt") or _DEFAULT_PERSONA)
    return persona + "\n\n" + _TOOLS_PREAMBLE.format(model=TIER1_MODEL)
