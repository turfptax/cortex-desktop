"""Step 2: Prepare training dataset from synced data.

Merges up to 6 data sources into a ChatML-format HuggingFace Dataset:
1. Interaction replay — real pet conversations
2. Knowledge injection — Q&A from user notes
3. Personality shaping — hand-crafted trait examples
4. Curated examples — manually authored/approved via Hub
5. Synthetic notes — LM Studio generated Q&A
6. Heartbeat examples — autonomous pet thoughts
"""

import json
import random
from collections import defaultdict
from typing import Dict, List, Optional

from cortex_train.config import TrainSettings
from cortex_train.errors import DatasetError
from cortex_train.formats import load_jsonl
from cortex_train.paths import TrainPaths
from cortex_train.progress import ProgressCallback, make_step_progress, null_progress
from cortex_train.prompts import (
    MOOD_MODIFIERS, PET_NAME, PERSONALITY_EXAMPLES, STAGE_PROMPTS,
    build_system_prompt,
)


def _build_interaction_examples(interactions: List[dict], min_tokens: int = 1) -> List[dict]:
    """Convert pet_interactions into ChatML training examples."""
    examples = []
    for ix in interactions:
        if not ix.get("response") or ix.get("tokens_generated", 0) < min_tokens:
            continue
        system_prompt = build_system_prompt(ix.get("stage", 0), ix.get("mood", "neutral"))
        examples.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": ix["prompt"]},
                {"role": "assistant", "content": ix["response"]},
            ],
            "source": "interaction",
        })
    return examples


def _build_note_examples(notes: List[dict]) -> List[dict]:
    """Generate Q&A pairs from user notes grouped by project and type."""
    examples = []
    system_prompt = (
        f"You are {PET_NAME}, a companion who knows about your owner's "
        f"work and interests. Share what you know helpfully and naturally."
    )

    # Group by project
    by_project = defaultdict(list)
    for note in notes:
        project = note.get("project", "").strip()
        if project:
            by_project[project].append(note)

    for project, project_notes in by_project.items():
        contents = [n["content"] for n in project_notes if n.get("content")]
        if not contents:
            continue
        combined = " ".join(contents[:5])
        if len(combined) > 500:
            combined = combined[:500] + "..."

        for q in [f"What do you know about {project}?", f"Tell me about the {project} project."]:
            examples.append({
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": q},
                    {"role": "assistant", "content": f"From what you've told me about {project}: {combined}"},
                ],
                "source": "notes",
            })

    # Group by type
    type_questions = {
        "decision": "What decisions have I made recently?",
        "bug": "What bugs am I tracking?",
        "idea": "What ideas have I had lately?",
        "todo": "What's on my todo list?",
        "reminder": "Do I have any reminders?",
    }
    by_type = defaultdict(list)
    for note in notes:
        ntype = note.get("note_type", "note").strip()
        if ntype in type_questions:
            by_type[ntype].append(note)

    for ntype, typed_notes in by_type.items():
        contents = [n["content"] for n in typed_notes[:5] if n.get("content")]
        if not contents:
            continue
        combined = " | ".join(contents)
        if len(combined) > 400:
            combined = combined[:400] + "..."
        examples.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": type_questions[ntype]},
                {"role": "assistant", "content": f"Here's what I have: {combined}"},
            ],
            "source": "notes",
        })

    return examples


def _build_personality_examples() -> List[dict]:
    """Convert hand-crafted personality examples to ChatML format."""
    system_prompt = STAGE_PROMPTS[4].format(name=PET_NAME)
    mood_mod = MOOD_MODIFIERS["content"]
    return [{
        "messages": [
            {"role": "system", "content": mood_mod + system_prompt},
            {"role": "user", "content": ex["user"]},
            {"role": "assistant", "content": ex["assistant"]},
        ],
        "source": "personality",
    } for ex in PERSONALITY_EXAMPLES]


def _build_curated_examples(paths: TrainPaths, min_quality: int = 1) -> List[dict]:
    """Load curated examples from Hub web UI."""
    curated_data = load_jsonl(paths.curated_path)
    if not curated_data:
        return []

    examples = []
    default_system = STAGE_PROMPTS[4].format(name=PET_NAME)

    for item in curated_data:
        messages = item.get("messages", [])
        metadata = item.get("metadata", {})
        if metadata.get("quality", 5) < min_quality:
            continue
        roles = [m.get("role") for m in messages]
        if "user" not in roles or "assistant" not in roles:
            continue
        if messages[0].get("role") != "system":
            stage = metadata.get("stage", 4)
            mood = metadata.get("mood", "neutral")
            messages = [{"role": "system", "content": build_system_prompt(stage, mood)}] + messages

        source = metadata.get("source", "curated")
        examples.append({"messages": messages, "source": f"curated-{source}"})

    return examples


def _build_synthetic_examples(paths: TrainPaths) -> List[dict]:
    """Load LM Studio synthesized examples."""
    synth_data = load_jsonl(paths.synthetic_path)
    return [
        {"messages": item["messages"], "source": item.get("source", "synthetic-notes")}
        for item in synth_data
        if "messages" in item and len(item["messages"]) >= 2
    ]


def _build_heartbeat_examples(paths: TrainPaths) -> List[dict]:
    """Load heartbeat autonomous thought examples."""
    hb_data = load_jsonl(paths.heartbeat_path)
    examples = []
    for item in hb_data:
        prompt = item.get("prompt", "")
        response = item.get("response", "")
        if not prompt or not response:
            continue
        messages = []
        if item.get("system"):
            messages.append({"role": "system", "content": item["system"]})
        messages.append({"role": "user", "content": prompt})
        messages.append({"role": "assistant", "content": response})
        examples.append({"messages": messages, "source": "heartbeat"})
    return examples


def run_prepare(
    settings: TrainSettings,
    paths: TrainPaths,
    on_progress: ProgressCallback = null_progress,
    include_notes: Optional[bool] = None,
    include_personality: Optional[bool] = None,
    include_curated: Optional[bool] = None,
    include_synthetic: Optional[bool] = None,
    include_heartbeat: Optional[bool] = None,
    seed: int = 42,
) -> dict:
    """Prepare training dataset by merging all data sources.

    Args:
        include_*: Override config flags for each data source
        seed: Random seed for train/test split

    Returns:
        {ok, train_size, test_size, sources}
    """
    emit = make_step_progress("prepare", on_progress)
    data_cfg = settings.data

    # Resolve flags (CLI overrides > config)
    use_notes = include_notes if include_notes is not None else data_cfg.include_notes
    use_personality = include_personality if include_personality is not None else data_cfg.include_personality
    use_curated = include_curated if include_curated is not None else data_cfg.include_curated
    use_synthetic = include_synthetic if include_synthetic is not None else data_cfg.include_synthetic
    use_heartbeat = include_heartbeat if include_heartbeat is not None else data_cfg.include_heartbeat

    emit("Loading raw data...")
    interactions = load_jsonl(paths.interactions_path)
    notes = load_jsonl(paths.notes_path)
    emit(f"Raw data: {len(interactions)} interactions, {len(notes)} notes")

    all_examples = []
    sources = {}

    # Source 1: Interaction replay (always included)
    ix_examples = _build_interaction_examples(interactions, data_cfg.min_response_tokens)
    sources["interaction"] = len(ix_examples)
    all_examples.extend(ix_examples)
    emit(f"Interaction replay: {len(ix_examples)}", pct=15)

    # Source 2: Knowledge injection
    if use_notes:
        note_examples = _build_note_examples(notes)
        sources["notes"] = len(note_examples)
        all_examples.extend(note_examples)
        emit(f"Knowledge injection: {len(note_examples)}", pct=30)

    # Source 3: Personality shaping
    if use_personality:
        personality = _build_personality_examples()
        sources["personality"] = len(personality)
        all_examples.extend(personality)
        emit(f"Personality shaping: {len(personality)}", pct=45)

    # Source 4: Curated examples
    if use_curated:
        curated = _build_curated_examples(paths, data_cfg.curated_min_quality)
        sources["curated"] = len(curated)
        all_examples.extend(curated)
        emit(f"Curated examples: {len(curated)}", pct=55)

    # Source 5: Synthetic notes
    if use_synthetic:
        synthetic = _build_synthetic_examples(paths)
        sources["synthetic"] = len(synthetic)
        all_examples.extend(synthetic)
        emit(f"Synthetic notes: {len(synthetic)}", pct=70)

    # Source 6: Heartbeat
    if use_heartbeat:
        heartbeat = _build_heartbeat_examples(paths)
        sources["heartbeat"] = len(heartbeat)
        all_examples.extend(heartbeat)
        emit(f"Heartbeat examples: {len(heartbeat)}", pct=80)

    if not all_examples:
        raise DatasetError("No training examples generated! Run sync step first.")

    # Shuffle and split
    random.seed(seed)
    random.shuffle(all_examples)

    n_test = max(1, int(len(all_examples) * data_cfg.test_split))
    n_train = len(all_examples) - n_test
    train_examples = all_examples[:n_train]
    test_examples = all_examples[n_train:]

    emit(f"Dataset: {n_train} train, {n_test} test", pct=90)

    # Save as HuggingFace Dataset
    try:
        from datasets import Dataset, DatasetDict

        def strip_meta(examples):
            return [{"messages": ex["messages"]} for ex in examples]

        ds = DatasetDict({
            "train": Dataset.from_list(strip_meta(train_examples)),
            "test": Dataset.from_list(strip_meta(test_examples)),
        })
        paths.dataset.mkdir(parents=True, exist_ok=True)
        ds.save_to_disk(str(paths.dataset))
        emit(f"Saved HuggingFace Dataset to {paths.dataset}", pct=100)

    except ImportError:
        # Fallback: JSONL
        emit("datasets library not installed — saving as JSONL")
        paths.dataset.mkdir(parents=True, exist_ok=True)
        for name, data in [("train", train_examples), ("test", test_examples)]:
            out = paths.dataset / f"{name}.jsonl"
            with open(out, "w", encoding="utf-8") as f:
                for ex in data:
                    f.write(json.dumps({"messages": ex["messages"]}, ensure_ascii=False) + "\n")
        emit("Saved JSONL dataset", pct=100)

    return {
        "ok": True,
        "train_size": n_train,
        "test_size": n_test,
        "total": len(all_examples),
        "sources": sources,
    }
