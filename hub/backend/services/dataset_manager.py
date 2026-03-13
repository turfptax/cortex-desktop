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
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import settings

CURATED_FILE = "curated_examples.jsonl"
SYNTHETIC_FILE = "synthetic_examples.jsonl"


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


def load_synthetic() -> list[dict]:
    """Load all synthetic training examples (read-only)."""
    path = _get_synthetic_path()
    if not path.exists():
        return []
    examples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    raw = json.loads(line)
                    # Normalize to curated schema
                    example = {
                        "id": f"syn-{len(examples)}",
                        "messages": raw.get("messages", []),
                        "metadata": {
                            "source": "synthetic",
                            "mood": "",
                            "stage": 4,
                            "topic": raw.get("source", "synthetic-notes"),
                            "quality": 0,
                            "created_at": raw.get("generated_at", ""),
                        },
                    }
                    examples.append(example)
                except json.JSONDecodeError:
                    pass
    return examples


def get_synthetic_stats() -> dict:
    """Get synthetic dataset statistics."""
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
