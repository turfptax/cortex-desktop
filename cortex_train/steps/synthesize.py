"""Step 1: Synthesize training data from Pi notes using LM Studio.

Uses a teacher model to generate diverse ChatML training pairs from raw notes.
Supports idempotent processing via a synthesis tracker file.
"""

import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from cortex_train.config import TrainSettings
from cortex_train.errors import SynthesisError
from cortex_train.formats import (
    chunk_text, load_jsonl, parse_llm_response, save_jsonl, wrap_as_chatml,
)
from cortex_train.lmstudio import build_teacher_messages, call_teacher, check_server
from cortex_train.paths import TrainPaths
from cortex_train.progress import ProgressCallback, make_step_progress, null_progress
from cortex_train.prompts import TEACHER_NOTE_TEMPLATE, TEACHER_SYSTEM_PROMPT


def _load_tracker(paths: TrainPaths) -> dict:
    path = paths.synthesis_tracker_path
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"processed_note_ids": [], "model": "", "last_run": "", "total_examples_generated": 0}


def _save_tracker(tracker: dict, paths: TrainPaths):
    with open(paths.synthesis_tracker_path, "w") as f:
        json.dump(tracker, f, indent=2)


def run_synthesize(
    settings: TrainSettings,
    paths: TrainPaths,
    on_progress: ProgressCallback = null_progress,
    max_notes: Optional[int] = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Synthesize training data from Pi notes using LM Studio teacher model.

    Args:
        max_notes: Limit number of notes to process
        force: Regenerate all, ignoring tracker
        dry_run: Preview without calling LLM

    Returns:
        {ok, examples_generated, notes_processed, failed}
    """
    emit = make_step_progress("synthesize", on_progress)
    url = settings.lmstudio.url.rstrip("/")
    n_examples = settings.lmstudio.examples_per_note
    temperature = settings.lmstudio.temperature
    max_tokens = settings.lmstudio.max_tokens

    # Check LM Studio
    emit(f"Checking LM Studio at {url}...")
    models = check_server(url)
    if models is None:
        raise SynthesisError(f"Cannot reach LM Studio at {url}")

    model = models[0] if models else None
    if not model:
        raise SynthesisError("No models loaded in LM Studio")
    emit(f"Using model: {model}")

    # Load notes
    notes = load_jsonl(paths.notes_path)
    if not notes:
        raise SynthesisError("No notes found. Run sync step first.")

    # Load tracker
    tracker = _load_tracker(paths)
    processed_ids = set(tracker.get("processed_note_ids", []))

    if force:
        emit("Force mode: clearing tracker")
        processed_ids = set()
        tracker = {"processed_note_ids": [], "model": "", "last_run": "", "total_examples_generated": 0}
        if paths.synthetic_path.exists():
            paths.synthetic_path.unlink()

    # Filter unprocessed
    to_process = [n for n in notes if n.get("id") not in processed_ids]
    if max_notes:
        to_process = to_process[:max_notes]

    emit(f"Notes: {len(notes)} total, {len(processed_ids)} done, {len(to_process)} to process")

    if not to_process:
        emit("Nothing to process. All notes already synthesized.", pct=100.0)
        return {"ok": True, "examples_generated": 0, "notes_processed": 0, "failed": 0}

    if dry_run:
        emit(f"Dry run: would process {len(to_process)} notes, ~{len(to_process) * n_examples} examples")
        return {"ok": True, "examples_generated": 0, "notes_processed": 0, "failed": 0, "dry_run": True}

    # Process notes
    total_generated = 0
    total_failed = 0
    start_time = time.time()

    for i, note in enumerate(to_process, 1):
        note_id = note.get("id", f"unknown-{i}")
        ntype = note.get("note_type", "note")
        project = note.get("project", "")
        content = note.get("content", "")

        pct = (i / len(to_process)) * 100
        emit(f"Note {i}/{len(to_process)} (id={note_id}, type={ntype})", pct=pct)

        if not content.strip():
            processed_ids.add(note_id)
            continue

        # Chunk long notes
        chunks = chunk_text(content)
        note_examples = []

        for chunk in chunks:
            user_prompt = TEACHER_NOTE_TEMPLATE.format(
                note_type=ntype,
                project=project or "(none)",
                content=chunk,
                n_examples=n_examples,
            )
            messages = build_teacher_messages(TEACHER_SYSTEM_PROMPT, user_prompt)

            response = call_teacher(url, model, messages, temperature, max_tokens)
            if response is None:
                total_failed += 1
                continue

            pairs = parse_llm_response(response, str(note_id))
            if not pairs:
                total_failed += 1
                continue

            for pair in pairs:
                example = wrap_as_chatml(pair, source="synthetic-notes", source_id=str(note_id))
                note_examples.append(example)

        # Append to output
        if note_examples:
            save_jsonl(note_examples, paths.synthetic_path, append=True)
            total_generated += len(note_examples)

        # Update tracker
        if note_examples or not content.strip():
            processed_ids.add(note_id)
            tracker["processed_note_ids"] = sorted(processed_ids)
            tracker["model"] = model
            tracker["last_run"] = datetime.now(timezone.utc).isoformat()
            tracker["total_examples_generated"] = tracker.get("total_examples_generated", 0) + len(note_examples)
            _save_tracker(tracker, paths)

    elapsed = time.time() - start_time
    emit(f"Synthesis complete: {total_generated} examples from {len(to_process)} notes ({elapsed:.0f}s)", pct=100.0)

    return {
        "ok": True,
        "examples_generated": total_generated,
        "notes_processed": len(to_process),
        "failed": total_failed,
        "elapsed_s": round(elapsed, 1),
    }
