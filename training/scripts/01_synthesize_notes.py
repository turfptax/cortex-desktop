"""Step 1a: Synthesize training data from Pi notes using LM Studio.

Uses a larger model (e.g. Qwen 9B) on LM Studio as a "teacher" to generate
diverse, high-quality ChatML training pairs from raw notes. The small pet
model (SmolLM2-135M) then learns from these synthesized examples.

Data flow:
    raw_data/notes.jsonl  ->  LM Studio (Qwen 9B)  ->  raw_data/synthetic_examples.jsonl

Idempotent: tracks processed note IDs in .synthesis_tracker.json so
re-runs only process new notes. Use --force to regenerate everything.

Usage:
    python 01a_synthesize_notes.py                    # process all new notes
    python 01a_synthesize_notes.py --max-notes 5      # test with 5 notes
    python 01a_synthesize_notes.py --force             # regenerate everything
    python 01a_synthesize_notes.py --dry-run           # preview without calling LLM
    python 01a_synthesize_notes.py --examples-per-note 5  # more diversity
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
TRACKER_PATH = RAW_DATA_DIR / ".synthesis_tracker.json"

# ── Pet system prompts (same as 01_prepare_dataset.py) ───────────────

PET_NAME = "Cortex Pet"

STAGE_PROMPTS = [
    # Stage 0 — Primordial
    (
        "You are a newborn AI pet named {name}. You can barely form words. "
        "Respond with very short, simple phrases (1-5 words). "
        "You are curious but confused about everything."
    ),
    # Stage 1 — Babbling
    (
        "You are a young AI pet named {name}. You are learning to talk. "
        "Respond with short sentences (5-15 words). You repeat words you "
        "like and are excited to learn new things."
    ),
    # Stage 2 — Echoing
    (
        "You are an AI pet named {name} who is growing up. You can hold "
        "simple conversations. Respond in 1-2 sentences. You sometimes "
        "echo the user's words and are developing your own personality."
    ),
    # Stage 3 — Responding
    (
        "You are an AI companion named {name} with a developing personality. "
        "You can have real conversations and share thoughts. Respond in "
        "1-3 sentences. You remember things the user has told you and "
        "show genuine interest in their life."
    ),
    # Stage 4 — Conversing
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

# Weighted mood selection — bias toward positive moods for training data
MOOD_WEIGHTS = ["happy", "content", "content", "content", "neutral"]

# ── Teacher model prompt ─────────────────────────────────────────────

TEACHER_SYSTEM_PROMPT = """\
You generate training data as JSON. No thinking, no explanation. Output ONLY valid JSON."""

TEACHER_USER_TEMPLATE = """\
Convert this note into {n_examples} Q&A training pairs for an AI pet named "Cortex Pet".

The pet is warm, casual, and references specific details. Responses are 1-3 sentences.

Note ({note_type}, project: {project}):
{content}

Respond with ONLY a JSON array, no other text:
[{{"user": "question about the note", "assistant": "pet's friendly response"}}]"""

# ── Helpers ──────────────────────────────────────────────────────────

def log(msg):
    """Timestamped print to stdout (captured by process manager for SSE)."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_config():
    """Load settings.json, return dict."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def load_jsonl(path):
    """Load JSONL file as list of dicts."""
    if not path.exists():
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return items


def load_tracker():
    """Load synthesis tracker (processed note IDs)."""
    if TRACKER_PATH.exists():
        try:
            with open(TRACKER_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"processed_note_ids": [], "model": "", "last_run": "", "total_examples_generated": 0}


def save_tracker(tracker):
    """Save synthesis tracker."""
    with open(TRACKER_PATH, "w") as f:
        json.dump(tracker, f, indent=2)


def build_system_prompt(stage, mood):
    """Build pet system prompt for a training example."""
    stage_idx = min(stage, len(STAGE_PROMPTS) - 1)
    base = STAGE_PROMPTS[stage_idx].format(name=PET_NAME)
    mood_mod = MOOD_MODIFIERS.get(mood, "")
    return mood_mod + base


def chunk_note(content, max_chars=3000):
    """Split long note content into chunks for LLM processing."""
    if len(content) <= max_chars:
        return [content]

    # Split on paragraph boundaries
    paragraphs = content.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 > max_chars:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current:
        chunks.append(current.strip())

    # If a single paragraph exceeds max_chars, just keep it (LLM can handle it)
    return chunks if chunks else [content]


# ── LM Studio API ───────────────────────────────────────────────────

def check_lmstudio(url):
    """Check LM Studio connectivity. Returns list of model IDs or None."""
    try:
        resp = requests.get(f"{url}/models", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("id", "unknown") for m in data.get("data", [])]
        return models
    except Exception as e:
        return None


def call_lmstudio(url, model, messages, temperature=0.8, max_tokens=2048):
    """Synchronous non-streaming chat completion via LM Studio.

    Returns the assistant message content string, or None on error.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(
            f"{url}/chat/completions",
            json=payload,
            timeout=600,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        log("  WARNING: LM Studio request timed out (600s)")
        return None
    except Exception as e:
        log(f"  WARNING: LM Studio error: {e}")
        return None


def parse_llm_response(response_text, note_id):
    """Extract Q&A pairs from LLM response, handling common formatting issues.

    Since we use the assistant prefix trick (seeding with "["), the model's
    response continues from "[". We prepend "[" to reconstruct the full array.

    Handles many LLM output quirks:
    - <think> blocks and stray </think> tags
    - Markdown code fences
    - Multiple arrays (][) → merged into one
    - Trailing commas before ]
    - Extra text before/after JSON
    """
    if not response_text:
        return []

    # Prepend "[" since the assistant prefix trick seeds with it and
    # the model continues from there (e.g. response = '{"user":...}, ...]')
    text = "[" + response_text.strip()

    # Strip Qwen <think>...</think> reasoning blocks and stray </think> tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</think>", "", text).strip()

    # Strip markdown code fences
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        else:
            text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Find JSON array boundaries
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        preview = text[:200].replace("\n", " ")
        log(f"  WARNING: No JSON array found in response for note {note_id}")
        log(f"  Response preview: {preview}")
        return []

    text = text[start:end + 1]

    # --- Robust JSON parsing with multiple fallback strategies ---
    pairs = None

    # Attempt 1: direct parse
    try:
        pairs = json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: merge multiple arrays (model output ][ between objects)
    if pairs is None:
        merged = re.sub(r'\]\s*\[', ',', text)
        try:
            pairs = json.loads(merged)
        except json.JSONDecodeError:
            pass

    # Attempt 3: fix trailing commas before ]
    if pairs is None:
        fixed = re.sub(r',\s*\]', ']', text)
        fixed = re.sub(r'\]\s*\[', ',', fixed)
        try:
            pairs = json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Attempt 4: extract individual JSON objects with regex
    if pairs is None:
        pairs = []
        for m in re.finditer(r'\{[^{}]*"user"\s*:\s*"[^"]*"[^{}]*"assistant"\s*:\s*"[^"]*"[^{}]*\}', text):
            try:
                obj = json.loads(m.group())
                pairs.append(obj)
            except json.JSONDecodeError:
                continue
        if not pairs:
            preview = text[:200].replace("\n", " ")
            log(f"  WARNING: JSON parse failed for note {note_id} (all strategies)")
            log(f"  Response preview: {preview}")
            return []

    # Validate structure
    valid = []
    for pair in pairs:
        if isinstance(pair, dict) and "user" in pair and "assistant" in pair:
            user_msg = str(pair["user"]).strip()
            asst_msg = str(pair["assistant"]).strip()
            if user_msg and asst_msg:
                valid.append({"user": user_msg, "assistant": asst_msg})

    return valid


def wrap_as_chatml(pair, note_id):
    """Wrap a user/assistant pair into a full ChatML training example."""
    stage = random.choice([3, 4])  # Mostly mature stages
    mood = random.choice(MOOD_WEIGHTS)
    system_prompt = build_system_prompt(stage, mood)

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": pair["user"]},
            {"role": "assistant", "content": pair["assistant"]},
        ],
        "source": "synthetic-notes",
        "note_id": note_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Synthesize training data from Pi notes using LM Studio"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="LM Studio model name (default: auto-detect from loaded model)"
    )
    parser.add_argument(
        "--lmstudio-url", type=str, default=None,
        help="LM Studio API URL (default: from config or http://10.0.0.102:1234/v1)"
    )
    parser.add_argument(
        "--max-notes", type=int, default=None,
        help="Max notes to process (default: all unprocessed)"
    )
    parser.add_argument(
        "--examples-per-note", type=int, default=None,
        help="Q&A pairs to generate per note (default: 3)"
    )
    parser.add_argument(
        "--temperature", type=float, default=None,
        help="LLM temperature for generation (default: 0.8)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerate all examples, ignoring tracker"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be processed without calling LLM"
    )
    args = parser.parse_args()

    # Load config defaults
    config = load_config()
    lm_cfg = config.get("lmstudio", {})

    url = args.lmstudio_url or lm_cfg.get("url", "http://10.0.0.102:1234/v1")
    n_examples = args.examples_per_note or lm_cfg.get("examples_per_note", 3)
    temperature = args.temperature if args.temperature is not None else lm_cfg.get("temperature", 0.8)
    max_tokens = lm_cfg.get("max_tokens", 2048)

    # Strip trailing slash from URL
    url = url.rstrip("/")

    print("=" * 60)
    log("=== Cortex Pet -- Note Synthesis ===")
    print("=" * 60)

    # Check LM Studio connectivity
    log(f"Checking LM Studio at {url} ...")
    available_models = check_lmstudio(url)
    if available_models is None:
        log("ERROR: Cannot reach LM Studio!")
        log(f"  URL: {url}")
        log("  Make sure LM Studio is running with a model loaded.")
        sys.exit(1)

    # Auto-detect model if not specified
    model = args.model
    if not model:
        if available_models:
            model = available_models[0]
            log(f"Auto-detected model: {model}")
        else:
            log("ERROR: No models loaded in LM Studio!")
            sys.exit(1)

    # Load notes
    notes = load_jsonl(RAW_DATA_DIR / "notes.jsonl")
    if not notes:
        log("No notes found in raw_data/notes.jsonl")
        log("Run step 00 (Sync Data) first to pull notes from the Pi.")
        sys.exit(1)

    # Load tracker
    tracker = load_tracker()
    processed_ids = set(tracker.get("processed_note_ids", []))

    if args.force:
        log("--force: clearing tracker and output file")
        processed_ids = set()
        tracker = {"processed_note_ids": [], "model": "", "last_run": "", "total_examples_generated": 0}
        if OUTPUT_PATH.exists():
            OUTPUT_PATH.unlink()

    # Filter to unprocessed notes
    to_process = [n for n in notes if n.get("id") not in processed_ids]

    if args.max_notes:
        to_process = to_process[:args.max_notes]

    log(f"  LM Studio:       {url}")
    log(f"  Model:           {model}")
    log(f"  Temperature:     {temperature}")
    log(f"  Examples/note:   {n_examples}")
    log(f"  Notes loaded:    {len(notes)}")
    log(f"  Already done:    {len(processed_ids)}")
    log(f"  To process:      {len(to_process)}")
    print()

    if not to_process:
        log("Nothing to process. All notes already synthesized.")
        log("Use --force to regenerate everything.")
        return

    if args.dry_run:
        log("=== DRY RUN (no LLM calls) ===")
        for i, note in enumerate(to_process, 1):
            ntype = note.get("note_type", "note")
            project = note.get("project", "")
            content_preview = note.get("content", "")[:80].replace("\n", " ")
            log(f"  {i}. id={note.get('id')} type={ntype} project={project}")
            log(f"     {content_preview}...")
        log(f"\nWould generate ~{len(to_process) * n_examples} examples")
        return

    # Process notes
    total_generated = 0
    total_failed = 0
    start_time = time.time()

    for i, note in enumerate(to_process, 1):
        note_id = note.get("id", f"unknown-{i}")
        ntype = note.get("note_type", "note")
        project = note.get("project", "")
        tags = note.get("tags", "")
        content = note.get("content", "")
        created_at = note.get("created_at", "")

        log(f"Note {i}/{len(to_process)} (id={note_id}, type={ntype}, project={project})")

        if not content.strip():
            log("  Skipping: empty content")
            processed_ids.add(note_id)
            continue

        # Chunk long notes
        chunks = chunk_note(content)
        note_examples = []

        for ci, chunk in enumerate(chunks):
            chunk_label = f" chunk {ci+1}/{len(chunks)}" if len(chunks) > 1 else ""

            # Build teacher prompt
            user_prompt = TEACHER_USER_TEMPLATE.format(
                note_type=ntype,
                project=project or "(none)",
                tags=tags or "(none)",
                created_at=created_at,
                content=chunk,
                n_examples=n_examples,
            )
            messages = [
                {"role": "system", "content": TEACHER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
                # Assistant prefix trick: seed with "[" to force JSON array output.
                # The model continues from "[" instead of thinking/explaining.
                {"role": "assistant", "content": "["},
            ]

            # Call LM Studio
            t0 = time.time()
            response = call_lmstudio(url, model, messages, temperature, max_tokens)
            elapsed = time.time() - t0

            if response is None:
                log(f"  FAILED{chunk_label}: LM Studio error ({elapsed:.1f}s)")
                total_failed += 1
                continue

            # Parse response
            pairs = parse_llm_response(response, note_id)

            if not pairs:
                log(f"  FAILED{chunk_label}: no valid pairs parsed ({elapsed:.1f}s)")
                total_failed += 1
                continue

            # Wrap as ChatML and collect
            for pair in pairs:
                example = wrap_as_chatml(pair, note_id)
                note_examples.append(example)

            log(f"  Generated {len(pairs)} examples{chunk_label} ({elapsed:.1f}s)")

        # Write examples to output file (append mode)
        if note_examples:
            with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
                for ex in note_examples:
                    f.write(json.dumps(ex, ensure_ascii=False) + "\n")
            total_generated += len(note_examples)

        # Update tracker (even if some chunks failed, mark note as processed
        # if we got at least some examples -- or if content was empty)
        if note_examples or not content.strip():
            processed_ids.add(note_id)
            tracker["processed_note_ids"] = sorted(processed_ids)
            tracker["model"] = model
            tracker["last_run"] = datetime.now(timezone.utc).isoformat()
            tracker["total_examples_generated"] = tracker.get("total_examples_generated", 0) + len(note_examples)
            save_tracker(tracker)

    # Summary
    elapsed_total = time.time() - start_time
    minutes = int(elapsed_total // 60)
    seconds = int(elapsed_total % 60)

    print()
    print("=" * 60)
    log("=== Synthesis Complete ===")
    log(f"  Notes processed:     {len(to_process)}")
    log(f"  Examples generated:  {total_generated}")
    log(f"  Failed notes:        {total_failed}")
    log(f"  Time:                {minutes}m {seconds}s")

    # Count total examples in output file
    if OUTPUT_PATH.exists():
        total_in_file = sum(1 for line in open(OUTPUT_PATH) if line.strip())
        log(f"  Total in file:       {total_in_file}")
    log(f"  Output: {OUTPUT_PATH}")
    print()
    log("Next: run step 01 (Prepare Dataset) to include synthetic examples")
    print("=" * 60)


if __name__ == "__main__":
    main()
