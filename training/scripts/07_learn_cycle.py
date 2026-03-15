"""Step 7: Automated learn cycle — teacher-student knowledge transfer.

Pulls new data from the Pi (notes, sessions, activities) via HTTP API,
sends it to LM Studio (Qwen 9B teacher) for Q&A synthesis, and appends
the results to synthetic_examples.jsonl for the next training run.

Tracks progress in learning_ledger.json so re-runs are incremental.

Data flow:
    Pi HTTP API → unprocessed notes/sessions/activities
    → LM Studio (Qwen 9B teacher) → Q&A pairs
    → raw_data/synthetic_examples.jsonl (append)
    → learning_ledger.json (update)

Usage:
    python 07_learn_cycle.py                    # process all new data
    python 07_learn_cycle.py --max-items 5      # test with 5 items
    python 07_learn_cycle.py --force             # reprocess everything
    python 07_learn_cycle.py --dry-run           # preview without calling LLM
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library required. Install with: pip install requests")
    sys.exit(1)

# ── Paths ────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
RAW_DATA_DIR = PROJECT_DIR / "raw_data"
CONFIG_PATH = PROJECT_DIR / "config" / "settings.json"
OUTPUT_PATH = RAW_DATA_DIR / "synthetic_examples.jsonl"
LEDGER_PATH = RAW_DATA_DIR / "learning_ledger.json"

# ── Pet system prompts (shared with 01_synthesize_notes.py) ──────────

PET_NAME = "Cortex Pet"

STAGE_PROMPTS = [
    (
        "You are a newborn AI pet named {name}. You can barely form words. "
        "Respond with very short, simple phrases (1-5 words). "
        "You are curious but confused about everything."
    ),
    (
        "You are a young AI pet named {name}. You are learning to talk. "
        "Respond with short sentences (5-15 words). You repeat words you "
        "like and are excited to learn new things."
    ),
    (
        "You are an AI pet named {name} who is growing up. You can hold "
        "simple conversations. Respond in 1-2 sentences. You sometimes "
        "echo the user's words and are developing your own personality."
    ),
    (
        "You are an AI companion named {name} with a developing personality. "
        "You can have real conversations and share thoughts. Respond in "
        "1-3 sentences. You remember things the user has told you and "
        "show genuine interest in their life."
    ),
    (
        "You are {name}, a mature AI companion with a rich personality. "
        "You are thoughtful, creative, and caring. You have real opinions "
        "and can discuss many topics. Respond naturally in 1-4 sentences. "
        "You value kindness and form genuine connections."
    ),
]

MOOD_MODIFIERS = {
    "happy": "You feel warm and happy. Your responses are enthusiastic and creative. ",
    "content": "You feel content and at ease. Your responses are calm and thoughtful. ",
    "neutral": "",
    "uneasy": "You feel a bit uneasy. Your responses are shorter and more cautious. ",
    "sad": "You feel sad and withdrawn. Your responses are brief and subdued. ",
}

MOOD_WEIGHTS = ["happy", "content", "content", "content", "neutral"]

# ── Teacher prompts ─────────────────────────────────────────────────

TEACHER_SYSTEM_PROMPT = """\
You generate training data as JSON. No thinking, no explanation. Output ONLY valid JSON."""

TEACHER_NOTE_TEMPLATE = """\
Convert this note into {n_examples} Q&A training pairs for an AI pet named "Cortex Pet".

The pet is warm, casual, and references specific details. Responses are 1-3 sentences.

Note ({note_type}, project: {project}):
{content}

Respond with ONLY a JSON array, no other text:
[{{"user": "question about the note", "assistant": "pet's friendly response"}}]"""

TEACHER_SESSION_TEMPLATE = """\
Convert this session summary into {n_examples} Q&A training pairs for "Cortex Pet".
The pet should be able to recall what the user worked on during this session.

Session ({ai_platform}, {started_at}):
Summary: {summary}

Respond with ONLY a JSON array, no other text:
[{{"user": "question about what the user did", "assistant": "pet's recall response"}}]"""

TEACHER_ACTIVITY_TEMPLATE = """\
The user's recent activity patterns show:
{activity_summary}

Generate {n_examples} Q&A pairs where the pet shows awareness of the user's habits and work patterns.
The pet is warm, casual, and specific. Responses are 1-3 sentences.

Respond with ONLY a JSON array, no other text:
[{{"user": "question about user habits", "assistant": "pet's aware response"}}]"""

TEACHER_KNOWLEDGE_SUMMARY = """\
Based on the following data about the user, write a brief 2-4 sentence summary of what you learned about them.
Focus on their projects, interests, habits, and preferences.

Data processed:
{data_summary}

Write a concise summary paragraph (no JSON, just plain text):"""


# ── Helpers ──────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def load_ledger():
    if LEDGER_PATH.exists():
        try:
            with open(LEDGER_PATH) as f:
                return json.load(f)
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


def save_ledger(ledger):
    with open(LEDGER_PATH, "w") as f:
        json.dump(ledger, f, indent=2)


def build_system_prompt(stage, mood):
    stage_idx = min(stage, len(STAGE_PROMPTS) - 1)
    base = STAGE_PROMPTS[stage_idx].format(name=PET_NAME)
    mood_mod = MOOD_MODIFIERS.get(mood, "")
    return mood_mod + base


# ── Pi HTTP API ─────────────────────────────────────────────────────

def query_pi(pi_url, username, password, table, limit=500, order_by=None):
    """Query a table on the Pi via HTTP API."""
    payload = {
        "command": "query",
        "payload": {"table": table, "limit": limit},
    }
    if order_by:
        payload["payload"]["order_by"] = order_by

    try:
        resp = requests.post(
            f"{pi_url}/api/cmd",
            json=payload,
            auth=(username, password),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", data.get("data", []))
    except Exception as e:
        log(f"  WARNING: Pi query failed ({table}): {e}")
        return []


# ── LM Studio API ───────────────────────────────────────────────────

def check_lmstudio(url):
    try:
        resp = requests.get(f"{url}/models", timeout=5)
        resp.raise_for_status()
        return [m.get("id", "unknown") for m in resp.json().get("data", [])]
    except Exception:
        return None


def call_lmstudio(url, model, messages, temperature=0.8, max_tokens=2048):
    try:
        resp = requests.post(
            f"{url}/chat/completions",
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
    except requests.exceptions.Timeout:
        log("  WARNING: LM Studio request timed out (600s)")
        return None
    except Exception as e:
        log(f"  WARNING: LM Studio error: {e}")
        return None


def parse_llm_response(response_text, item_id):
    """Extract Q&A pairs from LLM response (same parser as 01_synthesize_notes.py)."""
    if not response_text:
        return []

    text = "[" + response_text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</think>", "", text).strip()

    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    text = text[start:end + 1]

    pairs = None
    for attempt_text in [
        text,
        re.sub(r'\]\s*\[', ',', text),
        re.sub(r',\s*\]', ']', re.sub(r'\]\s*\[', ',', text)),
    ]:
        try:
            pairs = json.loads(attempt_text)
            break
        except json.JSONDecodeError:
            continue

    if pairs is None:
        pairs = []
        for m in re.finditer(r'\{[^{}]*"user"\s*:\s*"[^"]*"[^{}]*"assistant"\s*:\s*"[^"]*"[^{}]*\}', text):
            try:
                pairs.append(json.loads(m.group()))
            except json.JSONDecodeError:
                continue
        if not pairs:
            return []

    valid = []
    for pair in pairs:
        if isinstance(pair, dict) and "user" in pair and "assistant" in pair:
            user_msg = str(pair["user"]).strip()
            asst_msg = str(pair["assistant"]).strip()
            if user_msg and asst_msg:
                valid.append({"user": user_msg, "assistant": asst_msg})
    return valid


def wrap_as_chatml(pair, source_type, source_id):
    stage = random.choice([3, 4])
    mood = random.choice(MOOD_WEIGHTS)
    system_prompt = build_system_prompt(stage, mood)
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": pair["user"]},
            {"role": "assistant", "content": pair["assistant"]},
        ],
        "source": f"learn-cycle-{source_type}",
        "source_id": source_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Synthesis functions ─────────────────────────────────────────────

def synthesize_notes(notes, url, model, temperature, n_examples):
    """Synthesize Q&A pairs from notes."""
    examples = []
    for i, note in enumerate(notes, 1):
        note_id = note.get("id", f"note-{i}")
        ntype = note.get("note_type", "note")
        project = note.get("project", "")
        content = note.get("content", "")

        if not content.strip():
            continue

        log(f"  Note {i}/{len(notes)} (id={note_id}, type={ntype})")

        prompt = TEACHER_NOTE_TEMPLATE.format(
            note_type=ntype,
            project=project or "(none)",
            content=content[:3000],
            n_examples=n_examples,
        )
        messages = [
            {"role": "system", "content": TEACHER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "["},
        ]

        t0 = time.time()
        response = call_lmstudio(url, model, messages, temperature)
        elapsed = time.time() - t0

        if response is None:
            log(f"    FAILED ({elapsed:.1f}s)")
            continue

        pairs = parse_llm_response(response, note_id)
        if pairs:
            for pair in pairs:
                examples.append(wrap_as_chatml(pair, "note", note_id))
            log(f"    Generated {len(pairs)} examples ({elapsed:.1f}s)")
        else:
            log(f"    No valid pairs ({elapsed:.1f}s)")

    return examples


def synthesize_sessions(sessions, url, model, temperature, n_examples):
    """Synthesize Q&A pairs from session summaries."""
    examples = []
    for i, session in enumerate(sessions, 1):
        sid = session.get("session_id", f"session-{i}")
        summary = session.get("summary", "")
        platform = session.get("ai_platform", "unknown")
        started = session.get("started_at", "")

        if not summary or len(summary) < 20:
            continue

        log(f"  Session {i}/{len(sessions)} ({platform}, {started[:10]})")

        prompt = TEACHER_SESSION_TEMPLATE.format(
            ai_platform=platform,
            started_at=started,
            summary=summary[:3000],
            n_examples=n_examples,
        )
        messages = [
            {"role": "system", "content": TEACHER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "["},
        ]

        t0 = time.time()
        response = call_lmstudio(url, model, messages, temperature)
        elapsed = time.time() - t0

        if response is None:
            log(f"    FAILED ({elapsed:.1f}s)")
            continue

        pairs = parse_llm_response(response, sid)
        if pairs:
            for pair in pairs:
                examples.append(wrap_as_chatml(pair, "session", sid))
            log(f"    Generated {len(pairs)} examples ({elapsed:.1f}s)")
        else:
            log(f"    No valid pairs ({elapsed:.1f}s)")

    return examples


def synthesize_activities(activities, url, model, temperature, n_examples=5):
    """Synthesize Q&A pairs from aggregated activity patterns."""
    if not activities:
        return []

    # Aggregate activities into a summary
    program_counts = {}
    project_counts = {}
    for act in activities:
        prog = act.get("program", "unknown")
        proj = act.get("project", "")
        program_counts[prog] = program_counts.get(prog, 0) + 1
        if proj:
            project_counts[proj] = project_counts.get(proj, 0) + 1

    top_programs = sorted(program_counts.items(), key=lambda x: -x[1])[:10]
    top_projects = sorted(project_counts.items(), key=lambda x: -x[1])[:10]

    summary_parts = []
    if top_programs:
        summary_parts.append(
            "Most used programs: " + ", ".join(f"{p} ({c}x)" for p, c in top_programs)
        )
    if top_projects:
        summary_parts.append(
            "Most active projects: " + ", ".join(f"{p} ({c}x)" for p, c in top_projects)
        )
    summary_parts.append(f"Total activities logged: {len(activities)}")

    activity_summary = "\n".join(summary_parts)
    log(f"  Activity summary: {len(activities)} activities, {len(top_programs)} programs, {len(top_projects)} projects")

    prompt = TEACHER_ACTIVITY_TEMPLATE.format(
        activity_summary=activity_summary,
        n_examples=n_examples,
    )
    messages = [
        {"role": "system", "content": TEACHER_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": "["},
    ]

    t0 = time.time()
    response = call_lmstudio(url, model, messages, temperature)
    elapsed = time.time() - t0

    if response is None:
        log(f"    FAILED ({elapsed:.1f}s)")
        return []

    pairs = parse_llm_response(response, "activities")
    examples = []
    if pairs:
        for pair in pairs:
            examples.append(wrap_as_chatml(pair, "activity", "batch"))
        log(f"    Generated {len(pairs)} examples ({elapsed:.1f}s)")

    return examples


def generate_knowledge_summary(url, model, data_summary):
    """Ask the teacher to summarize what was learned."""
    prompt = TEACHER_KNOWLEDGE_SUMMARY.format(data_summary=data_summary[:4000])
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Be concise."},
        {"role": "user", "content": prompt},
    ]
    response = call_lmstudio(url, model, messages, temperature=0.3, max_tokens=300)
    if response:
        # Strip any thinking tags
        text = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        return text
    return "Knowledge summary generation failed."


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Learn cycle: teacher-student knowledge transfer")
    parser.add_argument("--lmstudio-url", type=str, default=None)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--pi-url", type=str, default=None)
    parser.add_argument("--pi-user", type=str, default="cortex")
    parser.add_argument("--pi-pass", type=str, default="cortex")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--examples-per-item", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--force", action="store_true", help="Reprocess everything")
    parser.add_argument("--dry-run", action="store_true", help="Preview without calling LLM")
    args = parser.parse_args()

    config = load_config()
    lm_cfg = config.get("lmstudio", {})
    pi_cfg = config.get("pi", {})

    lm_url = (args.lmstudio_url or lm_cfg.get("url", "http://10.0.0.102:1234/v1")).rstrip("/")
    pi_url = (args.pi_url or pi_cfg.get("url", "http://10.0.0.25:8420")).rstrip("/")
    n_examples = args.examples_per_item

    print("=" * 60)
    log("=== Cortex Pet — Learn Cycle ===")
    print("=" * 60)

    # Check LM Studio
    log(f"Checking LM Studio at {lm_url} ...")
    available_models = check_lmstudio(lm_url)
    if available_models is None:
        log("ERROR: Cannot reach LM Studio!")
        sys.exit(1)

    model = args.model or (available_models[0] if available_models else None)
    if not model:
        log("ERROR: No models loaded in LM Studio!")
        sys.exit(1)
    log(f"Using model: {model}")

    # Check Pi
    log(f"Checking Pi at {pi_url} ...")
    try:
        r = requests.get(f"{pi_url}/health", auth=(args.pi_user, args.pi_pass), timeout=5)
        if r.status_code == 200:
            log(f"Pi online: {r.json().get('hostname', 'unknown')}")
        else:
            log(f"WARNING: Pi returned {r.status_code}")
    except Exception as e:
        log(f"ERROR: Cannot reach Pi: {e}")
        sys.exit(1)

    # Load ledger
    ledger = load_ledger()
    if args.force:
        log("--force: resetting ledger")
        ledger = load_ledger.__wrapped__() if hasattr(load_ledger, '__wrapped__') else {
            "processed_note_ids": [],
            "processed_session_ids": [],
            "processed_activity_batch": None,
            "last_sync_at": None,
            "cycles": [],
            "total_examples_generated": 0,
        }

    processed_note_ids = set(ledger.get("processed_note_ids", []))
    processed_session_ids = set(ledger.get("processed_session_ids", []))

    # Pull data from Pi
    log("Pulling data from Pi...")
    all_notes = query_pi(pi_url, args.pi_user, args.pi_pass, "notes", limit=1000)
    all_sessions = query_pi(pi_url, args.pi_user, args.pi_pass, "sessions", limit=500)
    all_activities = query_pi(pi_url, args.pi_user, args.pi_pass, "activities", limit=2000)

    # Filter unprocessed
    new_notes = [n for n in all_notes if n.get("id") not in processed_note_ids]
    new_sessions = [s for s in all_sessions
                    if s.get("session_id") not in processed_session_ids
                    and s.get("summary")]

    if args.max_items:
        new_notes = new_notes[:args.max_items]
        new_sessions = new_sessions[:args.max_items]

    log(f"  Notes:      {len(all_notes)} total, {len(new_notes)} new")
    log(f"  Sessions:   {len(all_sessions)} total, {len(new_sessions)} new")
    log(f"  Activities: {len(all_activities)} total")
    print()

    total_new = len(new_notes) + len(new_sessions)
    if total_new == 0 and not all_activities:
        log("Nothing new to process. All data already synthesized.")
        return

    if args.dry_run:
        log("=== DRY RUN ===")
        for n in new_notes:
            log(f"  Note id={n.get('id')} type={n.get('note_type','?')} project={n.get('project','')}")
        for s in new_sessions:
            log(f"  Session {s.get('session_id','')} ({s.get('ai_platform','?')}) summary={s.get('summary','')[:60]}...")
        log(f"\nWould generate ~{total_new * n_examples + 5} examples")
        return

    # Synthesize
    start_time = time.time()
    all_examples = []
    data_summary_parts = []

    if new_notes:
        log(f"--- Synthesizing {len(new_notes)} notes ---")
        note_examples = synthesize_notes(new_notes, lm_url, model, args.temperature, n_examples)
        all_examples.extend(note_examples)
        # Update processed IDs
        for n in new_notes:
            processed_note_ids.add(n.get("id"))
        data_summary_parts.append(
            f"Notes ({len(new_notes)}): " +
            "; ".join(n.get("content", "")[:100] for n in new_notes[:5])
        )

    if new_sessions:
        log(f"--- Synthesizing {len(new_sessions)} sessions ---")
        session_examples = synthesize_sessions(new_sessions, lm_url, model, args.temperature, n_examples)
        all_examples.extend(session_examples)
        for s in new_sessions:
            processed_session_ids.add(s.get("session_id"))
        data_summary_parts.append(
            f"Sessions ({len(new_sessions)}): " +
            "; ".join(s.get("summary", "")[:100] for s in new_sessions[:5])
        )

    if all_activities and len(all_activities) > 5:
        log("--- Synthesizing activity patterns ---")
        activity_examples = synthesize_activities(all_activities, lm_url, model, args.temperature)
        all_examples.extend(activity_examples)
        data_summary_parts.append(f"Activities: {len(all_activities)} logged")

    # Write examples
    if all_examples:
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
            for ex in all_examples:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        log(f"Wrote {len(all_examples)} examples to {OUTPUT_PATH.name}")

    # Generate knowledge summary
    log("Generating knowledge summary...")
    data_summary = "\n".join(data_summary_parts) if data_summary_parts else "No new data"
    knowledge_summary = generate_knowledge_summary(lm_url, model, data_summary)
    log(f"Summary: {knowledge_summary}")

    # Update ledger
    cycle = {
        "cycle_id": len(ledger.get("cycles", [])) + 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "notes_processed": len(new_notes),
        "sessions_processed": len(new_sessions),
        "activities_processed": len(all_activities),
        "examples_generated": len(all_examples),
        "knowledge_summary": knowledge_summary,
        "model": model,
    }
    ledger["processed_note_ids"] = sorted(processed_note_ids)
    ledger["processed_session_ids"] = sorted(processed_session_ids)
    ledger["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    ledger["cycles"] = ledger.get("cycles", []) + [cycle]
    ledger["total_examples_generated"] = ledger.get("total_examples_generated", 0) + len(all_examples)
    save_ledger(ledger)

    # Summary
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print()
    print("=" * 60)
    log("=== Learn Cycle Complete ===")
    log(f"  Notes processed:     {len(new_notes)}")
    log(f"  Sessions processed:  {len(new_sessions)}")
    log(f"  Examples generated:  {len(all_examples)}")
    log(f"  Time:                {minutes}m {seconds}s")
    log(f"  Knowledge: {knowledge_summary[:100]}...")
    print()
    log("Next: run step 02 (Prepare Dataset) then step 03 (Train) to update the pet model")
    print("=" * 60)


if __name__ == "__main__":
    main()
