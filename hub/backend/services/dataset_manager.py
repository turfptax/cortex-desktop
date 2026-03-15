"""Curated dataset manager for training examples.

Stores approved/edited/authored training examples in a JSONL file
that the training pipeline picks up as a 4th data source.

Each example is a ChatML conversation with metadata:
{
  "id": "uuid",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "metadata": {
    "source": "chat_approved" | "chat_corrected" | "manual",
    "mood": "content" | "curious" | ...,
    "stage": 1-5,
    "topic": "coding" | "personal" | ...,
    "quality": 1-5,
    "original_response": "..." (if corrected),
    "created_at": "ISO timestamp"
  }
}
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config import settings

logger = logging.getLogger("cortex.dataset")

CURATED_FILE = "curated_examples.jsonl"
SYNTHETIC_FILE = "synthetic_examples.jsonl"
LEARNED_FILE = "synthetic_examples.jsonl"  # in APPDATA/Cortex/learning/


def _get_path() -> Path:
    return Path(settings.training_dir) / "raw_data" / CURATED_FILE


def _get_synthetic_path() -> Path:
    return Path(settings.training_dir) / "raw_data" / SYNTHETIC_FILE


def _ensure_dir():
    path = _get_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_all() -> list[dict]:
    """Load all curated examples."""
    path = _get_path()
    if not path.exists():
        return []
    examples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return examples


def _load_from_pi(limit: int = 1000, offset: int = 0) -> list[dict] | None:
    """Try to load training examples from the Pi."""
    try:
        url = f"{settings.pi_base_url}/api/cmd"
        auth = (settings.pi_username, settings.pi_password)
        body = {
            "command": "training_examples",
            "payload": {"limit": limit, "offset": offset},
        }
        resp = httpx.post(url, json=body, auth=auth, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        resp_str = data.get("response", "")
        if isinstance(resp_str, str) and resp_str.startswith("RSP:training_examples:"):
            parsed = json.loads(resp_str[len("RSP:training_examples:"):])
            return parsed.get("examples", []), parsed.get("total", 0)
        return None, 0
    except Exception as e:
        logger.debug("Cannot load training examples from Pi: %s", e)
        return None, 0


def _load_from_local_learned() -> list[dict]:
    """Load learned examples from local APPDATA file."""
    learned_path = Path(os.environ.get("APPDATA", ".")) / "Cortex" / "learning" / LEARNED_FILE
    if not learned_path.exists():
        return []
    examples = []
    with open(learned_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return examples


def load_synthetic() -> list[dict]:
    """Load all synthetic/learned training examples.

    Priority: Pi database > local learned file > legacy synthetic file.
    """
    # Try Pi first
    pi_examples, pi_total = _load_from_pi(limit=1000)
    if pi_examples is not None and len(pi_examples) > 0:
        logger.info("Loaded %d training examples from Pi (total: %d)", len(pi_examples), pi_total)
        return _normalize_pi_examples(pi_examples)

    # Fall back to local learned examples
    local_learned = _load_from_local_learned()
    if local_learned:
        logger.info("Loaded %d learned examples from local file", len(local_learned))
        return _normalize_local_examples(local_learned)

    # Fall back to legacy synthetic file in training repo
    path = _get_synthetic_path()
    if not path.exists():
        return []
    examples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return _normalize_local_examples(examples)


def _normalize_pi_examples(rows: list[dict]) -> list[dict]:
    """Convert Pi DB rows to the curated schema format."""
    examples = []
    for row in rows:
        messages = row.get("messages", [])
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except json.JSONDecodeError:
                continue
        examples.append({
            "id": f"pi-{row.get('id', len(examples))}",
            "messages": messages,
            "metadata": {
                "source": "learned",
                "mood": "",
                "stage": 4,
                "topic": row.get("source", "learn-cycle"),
                "quality": 0,
                "created_at": row.get("created_at", ""),
                "cycle_id": row.get("cycle_id", 0),
                "model": row.get("model", ""),
            },
        })
    return examples


def _normalize_local_examples(raw_list: list[dict]) -> list[dict]:
    """Convert local JSONL examples to the curated schema format."""
    examples = []
    for raw in raw_list:
        examples.append({
            "id": f"syn-{len(examples)}",
            "messages": raw.get("messages", []),
            "metadata": {
                "source": "learned",
                "mood": "",
                "stage": 4,
                "topic": raw.get("source", "learn-cycle"),
                "quality": 0,
                "created_at": raw.get("generated_at", ""),
            },
        })
    return examples


def get_synthetic_stats() -> dict:
    """Get synthetic/learned dataset statistics."""
    # Try Pi for count first (faster than loading all)
    try:
        url = f"{settings.pi_base_url}/api/cmd"
        auth = (settings.pi_username, settings.pi_password)
        body = {"command": "training_stats", "payload": {}}
        resp = httpx.post(url, json=body, auth=auth, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        resp_str = data.get("response", "")
        if isinstance(resp_str, str) and resp_str.startswith("RSP:training_stats:"):
            stats = json.loads(resp_str[len("RSP:training_stats:"):])
            return {"total": stats.get("total", 0), "cycles": stats.get("cycles", [])}
    except Exception:
        pass

    # Fall back to counting local examples
    examples = load_synthetic()
    return {"total": len(examples)}


def _save_all(examples: list[dict]):
    """Rewrite the JSONL file with all examples."""
    path = _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")


def add_example(
    messages: list[dict],
    source: str = "manual",
    mood: str = "",
    stage: int = 4,
    topic: str = "",
    quality: int = 5,
    original_response: str = "",
) -> dict:
    """Add a new curated training example."""
    example = {
        "id": str(uuid.uuid4())[:8],
        "messages": messages,
        "metadata": {
            "source": source,
            "mood": mood,
            "stage": stage,
            "topic": topic,
            "quality": quality,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if original_response:
        example["metadata"]["original_response"] = original_response

    path = _ensure_dir()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(example, ensure_ascii=False) + "\n")

    return example


def update_example(example_id: str, updates: dict) -> dict | None:
    """Update an existing example by ID."""
    examples = load_all()
    for i, ex in enumerate(examples):
        if ex.get("id") == example_id:
            if "messages" in updates:
                ex["messages"] = updates["messages"]
            if "metadata" in updates:
                ex.setdefault("metadata", {}).update(updates["metadata"])
            examples[i] = ex
            _save_all(examples)
            return ex
    return None


def delete_example(example_id: str) -> bool:
    """Delete an example by ID."""
    examples = load_all()
    filtered = [ex for ex in examples if ex.get("id") != example_id]
    if len(filtered) == len(examples):
        return False
    _save_all(filtered)
    return True


def get_stats() -> dict:
    """Get dataset statistics."""
    examples = load_all()
    sources = {}
    moods = {}
    topics = {}
    qualities = []

    for ex in examples:
        meta = ex.get("metadata", {})
        src = meta.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        if meta.get("mood"):
            moods[meta["mood"]] = moods.get(meta["mood"], 0) + 1
        if meta.get("topic"):
            topics[meta["topic"]] = topics.get(meta["topic"], 0) + 1
        if meta.get("quality"):
            qualities.append(meta["quality"])

    return {
        "total": len(examples),
        "by_source": sources,
        "by_mood": moods,
        "by_topic": topics,
        "avg_quality": round(sum(qualities) / len(qualities), 1) if qualities else 0,
    }
