"""Step 4: Evaluate fine-tuned pet model vs base model.

Compares perplexity on the test set and generates side-by-side responses.
All heavy deps are lazy-imported.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from cortex_train.config import TrainSettings
from cortex_train.errors import EvalError
from cortex_train.paths import TrainPaths
from cortex_train.prompts import MOOD_MODIFIERS, PET_NAME, STAGE_PROMPTS
from cortex_train.progress import ProgressCallback, make_step_progress, null_progress

# Default test prompts
DEFAULT_PROMPTS = [
    "Hello! How are you?",
    "Who are you?",
    "What makes you happy?",
    "I'm working on a new project today.",
    "Tell me something interesting.",
    "I had a rough day.",
    "Do you remember what I told you before?",
    "What do you think about coding?",
    "Good night!",
]

EVAL_SYSTEM_PROMPT = (
    "You feel content and at ease. Your responses are calm and thoughtful. "
    "You are Cortex Pet, a mature AI companion with a rich personality. "
    "You are thoughtful, creative, and caring. You have real opinions "
    "and can discuss many topics. Respond naturally in 1-4 sentences. "
    "You value kindness and form genuine connections."
)


def run_evaluate(
    settings: TrainSettings,
    paths: TrainPaths,
    on_progress: ProgressCallback = null_progress,
    prompts: Optional[List[str]] = None,
    save: bool = True,
    max_tokens: int = 128,
) -> dict:
    """Evaluate fine-tuned model vs base, with perplexity and response comparison.

    Returns:
        {ok, base_perplexity, finetuned_perplexity, prompts_results}
    """
    emit = make_step_progress("evaluate", on_progress)

    # Lazy imports
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from peft import PeftModel
    except ImportError as e:
        raise EvalError(f"Missing packages: {e}")

    base_model_name = settings.model.base_model
    adapter_dir = paths.adapter_dir
    test_prompts = prompts or DEFAULT_PROMPTS

    emit(f"Evaluating {base_model_name} with adapter at {adapter_dir}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    tokenizer = AutoTokenizer.from_pretrained(base_model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    results = {"prompts": []}

    def _generate(model, prompt):
        messages = [
            {"role": "system", "content": EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        input_len = inputs["input_ids"].shape[1]
        start = time.time()
        with torch.no_grad():
            outputs = model.generate(
                **inputs, max_new_tokens=max_tokens, temperature=0.7,
                top_p=0.9, repetition_penalty=1.1, do_sample=True,
                pad_token_id=tokenizer.eos_token_id,
            )
        elapsed = time.time() - start
        generated_ids = outputs[0][input_len:]
        response = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        tokens = len(generated_ids)
        return {"response": response, "tokens": tokens, "time_s": round(elapsed, 2),
                "tok_per_s": round(tokens / elapsed, 1) if elapsed > 0 else 0}

    def _eval_perplexity(model, max_samples=50):
        try:
            from datasets import load_from_disk
            ds = load_from_disk(str(paths.dataset))
            test_data = list(ds["test"])
        except Exception:
            test_path = paths.dataset / "test.jsonl"
            if not test_path.exists():
                return None
            from cortex_train.formats import load_jsonl
            test_data = load_jsonl(test_path)

        if not test_data:
            return None

        total_loss, count = 0.0, 0
        for sample in test_data[:max_samples]:
            text = tokenizer.apply_chat_template(
                sample["messages"], tokenize=False, add_generation_prompt=False)
            inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=2048).to(model.device)
            with torch.no_grad():
                outputs = model(**inputs, labels=inputs["input_ids"])
                total_loss += outputs.loss.item()
                count += 1

        if count == 0:
            return None
        avg_loss = total_loss / count
        return {"avg_loss": round(avg_loss, 4),
                "perplexity": round(torch.exp(torch.tensor(avg_loss)).item(), 2),
                "samples": count}

    # Base model evaluation
    emit("Loading base model...", pct=10)
    base_model = AutoModelForCausalLM.from_pretrained(base_model_name, torch_dtype=dtype, device_map="auto")

    emit("Evaluating base model perplexity...", pct=20)
    base_ppl = _eval_perplexity(base_model)
    if base_ppl:
        results["base_perplexity"] = base_ppl
        emit(f"Base perplexity: {base_ppl['perplexity']}")

    base_responses = {}
    for i, prompt in enumerate(test_prompts):
        pct = 25 + (i / len(test_prompts)) * 15
        emit(f"Base [{i+1}/{len(test_prompts)}]: {prompt[:40]}...", pct=pct)
        base_responses[prompt] = _generate(base_model, prompt)

    del base_model
    if device == "cuda":
        torch.cuda.empty_cache()

    # Fine-tuned model evaluation
    if not adapter_dir.exists():
        emit("No adapter found — base-only evaluation")
    else:
        emit("Loading fine-tuned model...", pct=50)
        ft_model = AutoModelForCausalLM.from_pretrained(base_model_name, torch_dtype=dtype, device_map="auto")
        ft_model = PeftModel.from_pretrained(ft_model, str(adapter_dir))
        ft_model = ft_model.merge_and_unload()

        emit("Evaluating fine-tuned perplexity...", pct=60)
        ft_ppl = _eval_perplexity(ft_model)
        if ft_ppl:
            results["finetuned_perplexity"] = ft_ppl
            emit(f"Fine-tuned perplexity: {ft_ppl['perplexity']}")

        ft_responses = {}
        for i, prompt in enumerate(test_prompts):
            pct = 65 + (i / len(test_prompts)) * 25
            emit(f"Fine-tuned [{i+1}/{len(test_prompts)}]: {prompt[:40]}...", pct=pct)
            ft_responses[prompt] = _generate(ft_model, prompt)

        del ft_model
        if device == "cuda":
            torch.cuda.empty_cache()

        # Build prompt results
        for prompt in test_prompts:
            entry = {"prompt": prompt}
            if prompt in base_responses:
                entry["base"] = base_responses[prompt]
            if prompt in ft_responses:
                entry["finetuned"] = ft_responses[prompt]
            results["prompts"].append(entry)

    # Save results
    if save:
        save_path = paths.eval_results_path
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w") as f:
            json.dump(results, f, indent=2)
        emit(f"Results saved to {save_path}", pct=100)

    return {
        "ok": True,
        "base_perplexity": results.get("base_perplexity"),
        "finetuned_perplexity": results.get("finetuned_perplexity"),
        "prompts_results": results.get("prompts", []),
    }
