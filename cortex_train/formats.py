"""Data format utilities: JSONL I/O, LLM response parsing, ChatML wrapping.

Extracted from 01_synthesize_notes.py and learn_cycle.py to eliminate duplication.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cortex_train.prompts import build_system_prompt, random_stage_mood


# ── JSONL I/O ───────────────────────────────────────────────────────

def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file as a list of dicts. Returns empty list if file missing."""
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


def save_jsonl(items: List[Dict], path: Path, append: bool = False) -> int:
    """Write items to a JSONL file. Returns count written."""
    mode = "a" if append else "w"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(items)


# ── LLM Response Parsing ────────────────────────────────────────────

def parse_llm_response(response_text: str, source_id: str = "") -> List[Dict[str, str]]:
    """Extract Q&A pairs from LLM response, handling common formatting issues.

    The assistant prefix trick seeds with "[" so the model's response continues
    from there. We prepend "[" to reconstruct the full JSON array.

    Handles:
    - <think> blocks and stray </think> tags (Qwen)
    - Markdown code fences
    - Multiple arrays (][) merged into one
    - Trailing commas before ]
    - Extra text before/after JSON
    """
    if not response_text:
        return []

    # Prepend "[" since assistant prefix trick seeds with it
    text = "[" + response_text.strip()

    # Strip Qwen <think>...</think> reasoning blocks
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
        for m in re.finditer(
            r'\{[^{}]*"user"\s*:\s*"[^"]*"[^{}]*"assistant"\s*:\s*"[^"]*"[^{}]*\}',
            text,
        ):
            try:
                obj = json.loads(m.group())
                pairs.append(obj)
            except json.JSONDecodeError:
                continue
        if not pairs:
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


# ── ChatML Formatting ───────────────────────────────────────────────

def wrap_as_chatml(
    pair: Dict[str, str],
    source: str = "synthetic",
    source_id: str = "",
    stage: Optional[int] = None,
    mood: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap a user/assistant pair into a full ChatML training example.

    Args:
        pair: Dict with "user" and "assistant" keys
        source: Source tag (e.g. "synthetic-notes", "curated", "interaction")
        source_id: ID of the source item (note ID, interaction ID, etc.)
        stage: Pet development stage (0-4). Random 3-4 if None.
        mood: Pet mood. Random from MOOD_WEIGHTS if None.
    """
    if stage is None or mood is None:
        s, m = random_stage_mood()
        stage = stage if stage is not None else s
        mood = mood if mood is not None else m

    system_prompt = build_system_prompt(stage, mood)

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": pair["user"]},
            {"role": "assistant", "content": pair["assistant"]},
        ],
        "source": source,
        "source_id": source_id,
        "stage": stage,
        "mood": mood,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def chunk_text(content: str, max_chars: int = 3000) -> List[str]:
    """Split long text into chunks on paragraph boundaries."""
    if len(content) <= max_chars:
        return [content]

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

    return chunks if chunks else [content]
