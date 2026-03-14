"""Self-contained learn cycle — teacher-student knowledge transfer.

Runs entirely in-process (no external script needed).
Pulls data from Pi, sends to LM Studio teacher for Q&A synthesis,
saves results to a ledger in %APPDATA%/Cortex/learning/.
"""

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("cortex.learn")

# ── Paths (use APPDATA so it works in installed exe) ─────────────
_APP_DIR = Path(__import__("os").environ.get("APPDATA", ".")) / "Cortex" / "learning"
LEDGER_PATH = _APP_DIR / "learning_ledger.json"
OUTPUT_PATH = _APP_DIR / "synthetic_examples.jsonl"

# ── State ────────────────────────────────────────────────────────
_running = False
_last_error: str | None = None


def is_running() -> bool:
    return _running


def last_error() -> str | None:
    return _last_error


# ── Pet system prompts ───────────────────────────────────────────
PET_NAME = "Cortex Pet"

STAGE_PROMPTS = [
    "You are a newborn AI pet named {name}. You can barely form words. "
    "Respond with very short, simple phrases (1-5 words). "
    "You are curious but confused about everything.",

    "You are a young AI pet named {name}. You are learning to talk. "
    "Respond with short sentences (5-15 words). You repeat words you "
    "like and are excited to learn new things.",

    "You are an AI pet named {name} who is growing up. You can hold "
    "simple conversations. Respond in 1-2 sentences. You sometimes "
    "echo the user's words and are developing your own personality.",

    "You are an AI companion named {name} with a developing personality. "
    "You can have real conversations and share thoughts. Respond in "
    "1-3 sentences. You remember things the user has told you and "
    "show genuine interest in their life.",

    "You are {name}, a mature AI companion with a rich personality. "
    "You are thoughtful, creative, and caring. You have real opinions "
    "and can discuss many topics. Respond naturally in 1-4 sentences. "
    "You value kindness and form genuine connections.",
]

MOOD_MODIFIERS = {
    "happy": "You feel warm and happy. Your responses are enthusiastic and creative. ",
    "content": "You feel content and at ease. Your responses are calm and thoughtful. ",
    "neutral": "",
    "uneasy": "You feel a bit uneasy. Your responses are shorter and more cautious. ",
    "sad": "You feel sad and withdrawn. Your responses are brief and subdued. ",
}
MOOD_WEIGHTS = ["happy", "content", "content", "content", "neutral"]

# ── Teacher prompts ──────────────────────────────────────────────
TEACHER_SYSTEM = "You generate training data as JSON. No thinking, no explanation. Output ONLY valid JSON."

TEACHER_NOTE_TPL = (
    'Convert this note into {n} Q&A training pairs for an AI pet named "Cortex Pet".\n'
    "The pet is warm, casual, and references specific details. Responses are 1-3 sentences.\n\n"
    "Note ({note_type}, project: {project}):\n{content}\n\n"
    'Respond with ONLY a JSON array, no other text:\n'
    '[{{"user": "question about the note", "assistant": "pet\'s friendly response"}}]'
)

TEACHER_SESSION_TPL = (
    'Convert this session summary into {n} Q&A training pairs for "Cortex Pet".\n'
    "The pet should be able to recall what the user worked on during this session.\n\n"
    "Session ({platform}, {started_at}):\nSummary: {summary}\n\n"
    'Respond with ONLY a JSON array, no other text:\n'
    '[{{"user": "question about what the user did", "assistant": "pet\'s recall response"}}]'
)

TEACHER_ACTIVITY_TPL = (
    "The user's recent activity patterns show:\n{summary}\n\n"
    "Generate {n} Q&A pairs where the pet shows awareness of the user's habits.\n"
    "The pet is warm, casual, and specific. Responses are 1-3 sentences.\n\n"
    'Respond with ONLY a JSON array, no other text:\n'
    '[{{"user": "question about user habits", "assistant": "pet\'s aware response"}}]'
)

TEACHER_KNOWLEDGE_TPL = (
    "Based on the following data about the user, write a brief 2-4 sentence summary "
    "of what you learned about them. Focus on projects, interests, habits.\n\n"
    "Data processed:\n{data}\n\nWrite a concise summary paragraph (no JSON, just plain text):"
)


# ── Helpers ──────────────────────────────────────────────────────

def _load_ledger() -> dict:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "processed_note_ids": [],
        "processed_session_ids": [],
        "processed_activity_batch": None,
        "last_sync_at": None,
        "cycles": [],
        "total_examples_generated": 0,
    }


def _save_ledger(ledger: dict):
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def _build_system_prompt(stage: int, mood: str) -> str:
    idx = min(stage, len(STAGE_PROMPTS) - 1)
    base = STAGE_PROMPTS[idx].format(name=PET_NAME)
    return MOOD_MODIFIERS.get(mood, "") + base


def _parse_llm_response(text: str) -> list[dict]:
    """Extract Q&A pairs from LLM response."""
    if not text:
        return []

    text = "[" + text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</think>", "", text).strip()

    if text.startswith("```"):
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    text = text[start:end + 1]

    pairs = None
    for attempt in [
        text,
        re.sub(r'\]\s*\[', ',', text),
        re.sub(r',\s*\]', ']', re.sub(r'\]\s*\[', ',', text)),
    ]:
        try:
            pairs = json.loads(attempt)
            break
        except json.JSONDecodeError:
            continue

    if pairs is None:
        pairs = []
        for m in re.finditer(
            r'\{[^{}]*"user"\s*:\s*"[^"]*"[^{}]*"assistant"\s*:\s*"[^"]*"[^{}]*\}', text
        ):
            try:
                pairs.append(json.loads(m.group()))
            except json.JSONDecodeError:
                continue
        if not pairs:
            return []

    valid = []
    for p in pairs:
        if isinstance(p, dict) and p.get("user") and p.get("assistant"):
            valid.append({"user": str(p["user"]).strip(), "assistant": str(p["assistant"]).strip()})
    return valid


def _wrap_chatml(pair: dict, source_type: str, source_id: Any) -> dict:
    stage = random.choice([3, 4])
    mood = random.choice(MOOD_WEIGHTS)
    return {
        "messages": [
            {"role": "system", "content": _build_system_prompt(stage, mood)},
            {"role": "user", "content": pair["user"]},
            {"role": "assistant", "content": pair["assistant"]},
        ],
        "source": f"learn-cycle-{source_type}",
        "source_id": str(source_id),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Pi queries (sync httpx, runs in thread) ─────────────────────

def _query_pi(table: str, limit: int = 500) -> list[dict]:
    url = f"{settings.pi_base_url}/api/cmd"
    auth = (settings.pi_username, settings.pi_password)
    body = {"command": "query", "payload": {"table": table, "limit": limit}}
    try:
        resp = httpx.post(url, json=body, auth=auth, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Parse RSP:query:{json} protocol
        resp_str = data.get("response", "")
        if isinstance(resp_str, str) and resp_str.startswith("RSP:query:"):
            parsed = json.loads(resp_str[len("RSP:query:"):])
            return parsed if isinstance(parsed, list) else parsed.get("rows", [])
        return data.get("rows", data.get("data", []))
    except Exception as e:
        logger.warning("Pi query failed (%s): %s", table, e)
        return []


# ── LM Studio calls (sync httpx, runs in thread) ────────────────

def _check_lmstudio() -> list[str] | None:
    try:
        resp = httpx.get(f"{settings.lmstudio_url}/models", timeout=5)
        resp.raise_for_status()
        return [m.get("id", "unknown") for m in resp.json().get("data", [])]
    except Exception:
        return None


def _call_teacher(model: str, messages: list[dict], temperature: float = 0.8,
                  max_tokens: int = 2048) -> str | None:
    try:
        resp = httpx.post(
            f"{settings.lmstudio_url}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        logger.warning("LM Studio request timed out (600s)")
        return None
    except Exception as e:
        logger.warning("LM Studio error: %s", e)
        return None


# ── Synthesis ────────────────────────────────────────────────────

def _synthesize_notes(notes: list[dict], url: str, model: str,
                      temp: float, n: int) -> list[dict]:
    examples = []
    for i, note in enumerate(notes, 1):
        nid = note.get("id", f"note-{i}")
        content = note.get("content", "").strip()
        if not content:
            continue
        logger.info("  Note %d/%d (id=%s)", i, len(notes), nid)
        prompt = TEACHER_NOTE_TPL.format(
            note_type=note.get("note_type", "note"),
            project=note.get("project") or "(none)",
            content=content[:3000], n=n,
        )
        msgs = [
            {"role": "system", "content": TEACHER_SYSTEM},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "["},
        ]
        t0 = time.time()
        resp = _call_teacher(model, msgs, temp)
        dt = time.time() - t0
        pairs = _parse_llm_response(resp) if resp else []
        if pairs:
            for p in pairs:
                examples.append(_wrap_chatml(p, "note", nid))
            logger.info("    Generated %d examples (%.1fs)", len(pairs), dt)
        else:
            logger.info("    No valid pairs (%.1fs)", dt)
    return examples


def _synthesize_sessions(sessions: list[dict], url: str, model: str,
                         temp: float, n: int) -> list[dict]:
    examples = []
    for i, sess in enumerate(sessions, 1):
        sid = sess.get("session_id", f"session-{i}")
        summary = sess.get("summary", "")
        if not summary or len(summary) < 20:
            continue
        logger.info("  Session %d/%d (%s)", i, len(sessions),
                     sess.get("ai_platform", "?"))
        prompt = TEACHER_SESSION_TPL.format(
            platform=sess.get("ai_platform", "unknown"),
            started_at=sess.get("started_at", ""),
            summary=summary[:3000], n=n,
        )
        msgs = [
            {"role": "system", "content": TEACHER_SYSTEM},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "["},
        ]
        t0 = time.time()
        resp = _call_teacher(model, msgs, temp)
        dt = time.time() - t0
        pairs = _parse_llm_response(resp) if resp else []
        if pairs:
            for p in pairs:
                examples.append(_wrap_chatml(p, "session", sid))
            logger.info("    Generated %d examples (%.1fs)", len(pairs), dt)
        else:
            logger.info("    No valid pairs (%.1fs)", dt)
    return examples


def _synthesize_activities(activities: list[dict], url: str, model: str,
                           temp: float, n: int = 5) -> list[dict]:
    if not activities or len(activities) < 5:
        return []
    prog_counts: dict[str, int] = {}
    proj_counts: dict[str, int] = {}
    for a in activities:
        prog = a.get("program", "unknown")
        proj = a.get("project", "")
        prog_counts[prog] = prog_counts.get(prog, 0) + 1
        if proj:
            proj_counts[proj] = proj_counts.get(proj, 0) + 1

    parts = []
    top_progs = sorted(prog_counts.items(), key=lambda x: -x[1])[:10]
    top_projs = sorted(proj_counts.items(), key=lambda x: -x[1])[:10]
    if top_progs:
        parts.append("Most used programs: " + ", ".join(f"{p} ({c}x)" for p, c in top_progs))
    if top_projs:
        parts.append("Most active projects: " + ", ".join(f"{p} ({c}x)" for p, c in top_projs))
    parts.append(f"Total activities logged: {len(activities)}")

    logger.info("  Activities: %d total, %d programs, %d projects",
                 len(activities), len(top_progs), len(top_projs))
    prompt = TEACHER_ACTIVITY_TPL.format(summary="\n".join(parts), n=n)
    msgs = [
        {"role": "system", "content": TEACHER_SYSTEM},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": "["},
    ]
    t0 = time.time()
    resp = _call_teacher(model, msgs, temp)
    dt = time.time() - t0
    pairs = _parse_llm_response(resp) if resp else []
    examples = [_wrap_chatml(p, "activity", "batch") for p in pairs]
    if examples:
        logger.info("    Generated %d examples (%.1fs)", len(examples), dt)
    return examples


# ── Main entry point ─────────────────────────────────────────────

def run_learn_cycle() -> dict:
    """Run the full learn cycle synchronously (call from a thread).

    Returns a result dict with cycle stats or error info.
    """
    global _running, _last_error
    _running = True
    _last_error = None

    try:
        logger.info("=== Learn Cycle starting ===")

        # Check LM Studio
        logger.info("Checking LM Studio at %s ...", settings.lmstudio_url)
        models = _check_lmstudio()
        if models is None:
            msg = f"Cannot reach LM Studio at {settings.lmstudio_url}"
            logger.error(msg)
            _last_error = msg
            return {"ok": False, "error": msg}
        if not models:
            msg = "No models loaded in LM Studio"
            logger.error(msg)
            _last_error = msg
            return {"ok": False, "error": msg}

        model = models[0]
        logger.info("Using model: %s", model)

        # Check Pi
        logger.info("Checking Pi at %s ...", settings.pi_base_url)
        try:
            r = httpx.get(
                f"{settings.pi_base_url}/health",
                auth=(settings.pi_username, settings.pi_password),
                timeout=5,
            )
            if r.status_code == 200:
                logger.info("Pi online: %s", r.json().get("hostname", "unknown"))
            else:
                msg = f"Pi returned HTTP {r.status_code}"
                logger.error(msg)
                _last_error = msg
                return {"ok": False, "error": msg}
        except Exception as e:
            msg = f"Cannot reach Pi: {e}"
            logger.error(msg)
            _last_error = msg
            return {"ok": False, "error": msg}

        # Load ledger
        ledger = _load_ledger()
        processed_note_ids = set(ledger.get("processed_note_ids", []))
        processed_session_ids = set(ledger.get("processed_session_ids", []))

        # Pull data
        logger.info("Pulling data from Pi...")
        all_notes = _query_pi("notes", limit=1000)
        all_sessions = _query_pi("sessions", limit=500)
        all_activities = _query_pi("activities", limit=2000)

        new_notes = [n for n in all_notes if n.get("id") not in processed_note_ids]
        new_sessions = [s for s in all_sessions
                        if s.get("session_id") not in processed_session_ids
                        and s.get("summary")]

        logger.info("  Notes: %d total, %d new", len(all_notes), len(new_notes))
        logger.info("  Sessions: %d total, %d new", len(all_sessions), len(new_sessions))
        logger.info("  Activities: %d total", len(all_activities))

        total_new = len(new_notes) + len(new_sessions)
        if total_new == 0 and len(all_activities) < 5:
            logger.info("Nothing new to process.")
            return {"ok": True, "message": "Nothing new to process",
                    "notes": 0, "sessions": 0, "examples": 0}

        # Synthesize
        lm_url = settings.lmstudio_url
        temp = 0.8
        n_examples = 3
        start_time = time.time()
        all_examples: list[dict] = []
        data_summary_parts: list[str] = []

        if new_notes:
            logger.info("--- Synthesizing %d notes ---", len(new_notes))
            note_ex = _synthesize_notes(new_notes, lm_url, model, temp, n_examples)
            all_examples.extend(note_ex)
            for n in new_notes:
                processed_note_ids.add(n.get("id"))
            data_summary_parts.append(
                f"Notes ({len(new_notes)}): "
                + "; ".join(n.get("content", "")[:100] for n in new_notes[:5])
            )

        if new_sessions:
            logger.info("--- Synthesizing %d sessions ---", len(new_sessions))
            sess_ex = _synthesize_sessions(new_sessions, lm_url, model, temp, n_examples)
            all_examples.extend(sess_ex)
            for s in new_sessions:
                processed_session_ids.add(s.get("session_id"))
            data_summary_parts.append(
                f"Sessions ({len(new_sessions)}): "
                + "; ".join(s.get("summary", "")[:100] for s in new_sessions[:5])
            )

        if all_activities and len(all_activities) >= 5:
            logger.info("--- Synthesizing activity patterns ---")
            act_ex = _synthesize_activities(all_activities, lm_url, model, temp)
            all_examples.extend(act_ex)
            data_summary_parts.append(f"Activities: {len(all_activities)} logged")

        # Write examples
        if all_examples:
            _APP_DIR.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
                for ex in all_examples:
                    f.write(json.dumps(ex, ensure_ascii=False) + "\n")
            logger.info("Wrote %d examples to %s", len(all_examples), OUTPUT_PATH.name)

        # Knowledge summary
        logger.info("Generating knowledge summary...")
        data_summary = "\n".join(data_summary_parts) if data_summary_parts else "No new data"
        prompt = TEACHER_KNOWLEDGE_TPL.format(data=data_summary[:4000])
        knowledge = _call_teacher(
            model,
            [{"role": "system", "content": "You are a helpful assistant. Be concise."},
             {"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=300,
        )
        if knowledge:
            knowledge = re.sub(r"<think>.*?</think>", "", knowledge, flags=re.DOTALL).strip()
        else:
            knowledge = "Knowledge summary generation failed."
        logger.info("Summary: %s", knowledge)

        # Update ledger
        cycle = {
            "cycle_id": len(ledger.get("cycles", [])) + 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "notes_processed": len(new_notes),
            "sessions_processed": len(new_sessions),
            "activities_processed": len(all_activities),
            "examples_generated": len(all_examples),
            "knowledge_summary": knowledge,
            "model": model,
        }
        ledger["processed_note_ids"] = sorted(processed_note_ids)
        ledger["processed_session_ids"] = sorted(processed_session_ids)
        ledger["last_sync_at"] = datetime.now(timezone.utc).isoformat()
        ledger["cycles"] = ledger.get("cycles", []) + [cycle]
        ledger["total_examples_generated"] = ledger.get("total_examples_generated", 0) + len(all_examples)
        _save_ledger(ledger)

        elapsed = time.time() - start_time
        logger.info("=== Learn Cycle Complete — %d examples in %.0fs ===",
                     len(all_examples), elapsed)

        return {
            "ok": True,
            "notes_processed": len(new_notes),
            "sessions_processed": len(new_sessions),
            "examples_generated": len(all_examples),
            "knowledge_summary": knowledge,
            "elapsed_s": round(elapsed, 1),
        }

    except Exception as e:
        logger.exception("Learn cycle failed")
        _last_error = str(e)
        return {"ok": False, "error": str(e)}
    finally:
        _running = False
