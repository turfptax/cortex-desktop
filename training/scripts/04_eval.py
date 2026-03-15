"""Step 3: Evaluate fine-tuned pet model vs base model.

Loads both the base SmolLM2 and the fine-tuned LoRA adapter, runs
test prompts through both, and shows a side-by-side comparison.

Usage:
    python 03_eval.py                            # compare with default prompts
    python 03_eval.py --prompts "Hello" "Who are you?" # custom prompts
    python 03_eval.py --base-only                # only test base model
    python 03_eval.py --finetuned-only           # only test fine-tuned

Requirements:
    pip install torch transformers peft

Outputs:
    Prints side-by-side comparison to stdout.
    Optionally saves to ../models/pet-lora/eval_results.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
MODELS_DIR = PROJECT_DIR / "models"
DATASET_DIR = PROJECT_DIR / "dataset"
CONFIG_PATH = PROJECT_DIR / "config" / "settings.json"

# Default test prompts covering different aspects
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

# System prompt for evaluation (Stage 4, content mood)
EVAL_SYSTEM_PROMPT = (
    "You feel content and at ease. Your responses are calm and thoughtful. "
    "You are Cortex Pet, a mature AI companion with a rich personality. "
    "You are thoughtful, creative, and caring. You have real opinions "
    "and can discuss many topics. Respond naturally in 1-4 sentences. "
    "You value kindness and form genuine connections."
)


def check_deps():
    missing = []
    for pkg in ["torch", "transformers", "peft"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Missing: {', '.join(missing)}")
        print(f"Install: pip install {' '.join(missing)}")
        sys.exit(1)


check_deps()

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def generate_response(model, tokenizer, prompt, system_prompt, max_new_tokens=128):
    """Generate a response using chat template."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )

    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[1]

    start = time.time()
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.1,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    elapsed = time.time() - start

    # Decode only the generated tokens
    generated_ids = outputs[0][input_len:]
    response = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    tokens = len(generated_ids)

    return {
        "response": response,
        "tokens": tokens,
        "time_s": round(elapsed, 2),
        "tok_per_s": round(tokens / elapsed, 1) if elapsed > 0 else 0,
    }


def eval_test_set(model, tokenizer, dataset_dir, max_samples=50):
    """Evaluate perplexity on the test set."""
    try:
        from datasets import load_from_disk
        ds = load_from_disk(str(dataset_dir))
        test_data = ds["test"]
    except Exception:
        # Try JSONL fallback
        test_path = dataset_dir / "test.jsonl"
        if not test_path.exists():
            return None
        test_data = []
        with open(test_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    test_data.append(json.loads(line))

    if not test_data:
        return None

    total_loss = 0.0
    count = 0

    for sample in list(test_data)[:max_samples]:
        messages = sample["messages"]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        inputs = tokenizer(
            text, return_tensors="pt", truncation=True, max_length=2048
        ).to(model.device)

        with torch.no_grad():
            outputs = model(**inputs, labels=inputs["input_ids"])
            total_loss += outputs.loss.item()
            count += 1

    if count == 0:
        return None

    avg_loss = total_loss / count
    perplexity = torch.exp(torch.tensor(avg_loss)).item()
    return {"avg_loss": round(avg_loss, 4), "perplexity": round(perplexity, 2), "samples": count}


def main():
    parser = argparse.ArgumentParser(description="Evaluate pet LoRA adapter")
    parser.add_argument("--prompts", nargs="+", default=None,
                        help="Custom test prompts")
    parser.add_argument("--base-only", action="store_true",
                        help="Only test base model")
    parser.add_argument("--finetuned-only", action="store_true",
                        help="Only test fine-tuned model")
    parser.add_argument("--max-tokens", type=int, default=128,
                        help="Max tokens to generate (default: 128)")
    parser.add_argument("--adapter-dir", default=None,
                        help="Path to LoRA adapter (default: ../models/pet-lora)")
    parser.add_argument("--save", action="store_true",
                        help="Save results to eval_results.json")
    args = parser.parse_args()

    config = load_config()
    model_cfg = config.get("model", {})
    base_model_name = model_cfg.get("base_model", "HuggingFaceTB/SmolLM2-135M-Instruct")
    adapter_dir = Path(args.adapter_dir) if args.adapter_dir else MODELS_DIR / "pet-lora"
    prompts = args.prompts or DEFAULT_PROMPTS

    print("=== Cortex Pet — Model Evaluation ===")
    print(f"  Base model:  {base_model_name}")
    print(f"  Adapter:     {adapter_dir}")
    print(f"  Prompts:     {len(prompts)}")

    # Check GPU
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    if device == "cuda":
        print(f"  GPU:         {torch.cuda.get_device_name(0)}")

    results = {"prompts": []}

    # Load tokenizer
    print(f"\nLoading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(base_model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Test base model
    base_responses = {}
    if not args.finetuned_only:
        print(f"\n{'='*60}")
        print(f"  BASE MODEL: {base_model_name}")
        print(f"{'='*60}")

        base_model = AutoModelForCausalLM.from_pretrained(
            base_model_name,
            torch_dtype=dtype,
            device_map="auto",
        )

        # Test set perplexity
        if DATASET_DIR.exists():
            print("\nEvaluating on test set...")
            base_ppl = eval_test_set(base_model, tokenizer, DATASET_DIR)
            if base_ppl:
                print(f"  Loss: {base_ppl['avg_loss']}  Perplexity: {base_ppl['perplexity']}  ({base_ppl['samples']} samples)")
                results["base_perplexity"] = base_ppl

        # Generate responses
        for i, prompt in enumerate(prompts):
            print(f"\n  [{i+1}/{len(prompts)}] {prompt}")
            res = generate_response(
                base_model, tokenizer, prompt, EVAL_SYSTEM_PROMPT, args.max_tokens
            )
            print(f"  >> {res['response']}")
            print(f"    ({res['tokens']} tokens, {res['time_s']}s, {res['tok_per_s']} tok/s)")
            base_responses[prompt] = res

        # Free base model memory
        del base_model
        if device == "cuda":
            torch.cuda.empty_cache()

    # Test fine-tuned model
    ft_responses = {}
    if not args.base_only:
        if not adapter_dir.exists():
            print(f"\n  Adapter not found at {adapter_dir}")
            print("  Run 02_train_pet.py first.")
            if not args.finetuned_only:
                print("  (Showing base model results only)")
            else:
                sys.exit(1)
        else:
            print(f"\n{'='*60}")
            print(f"  FINE-TUNED MODEL (LoRA adapter)")
            print(f"{'='*60}")

            ft_model = AutoModelForCausalLM.from_pretrained(
                base_model_name,
                torch_dtype=dtype,
                device_map="auto",
            )
            ft_model = PeftModel.from_pretrained(ft_model, str(adapter_dir))
            ft_model = ft_model.merge_and_unload()

            # Test set perplexity
            if DATASET_DIR.exists():
                print("\nEvaluating on test set...")
                ft_ppl = eval_test_set(ft_model, tokenizer, DATASET_DIR)
                if ft_ppl:
                    print(f"  Loss: {ft_ppl['avg_loss']}  Perplexity: {ft_ppl['perplexity']}  ({ft_ppl['samples']} samples)")
                    results["finetuned_perplexity"] = ft_ppl

            # Generate responses
            for i, prompt in enumerate(prompts):
                print(f"\n  [{i+1}/{len(prompts)}] {prompt}")
                res = generate_response(
                    ft_model, tokenizer, prompt, EVAL_SYSTEM_PROMPT, args.max_tokens
                )
                print(f"  >>{res['response']}")
                print(f"    ({res['tokens']} tokens, {res['time_s']}s, {res['tok_per_s']} tok/s)")
                ft_responses[prompt] = res

            del ft_model
            if device == "cuda":
                torch.cuda.empty_cache()

    # Side-by-side comparison
    if base_responses and ft_responses:
        print(f"\n{'='*60}")
        print(f"  SIDE-BY-SIDE COMPARISON")
        print(f"{'='*60}")

        for prompt in prompts:
            print(f"\n  Prompt: \"{prompt}\"")
            if prompt in base_responses:
                print(f"  BASE:      {base_responses[prompt]['response']}")
            if prompt in ft_responses:
                print(f"  FINETUNED: {ft_responses[prompt]['response']}")
            print()

        # Speed comparison
        base_avg_tps = sum(r["tok_per_s"] for r in base_responses.values()) / len(base_responses)
        ft_avg_tps = sum(r["tok_per_s"] for r in ft_responses.values()) / len(ft_responses)
        print(f"  Avg tokens/sec — Base: {base_avg_tps:.1f}, Fine-tuned: {ft_avg_tps:.1f}")

    # Build results
    for prompt in prompts:
        entry = {"prompt": prompt}
        if prompt in base_responses:
            entry["base"] = base_responses[prompt]
        if prompt in ft_responses:
            entry["finetuned"] = ft_responses[prompt]
        results["prompts"].append(entry)

    # Save results
    if args.save:
        save_path = adapter_dir / "eval_results.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {save_path}")

    print(f"\nNext: python 04_export_deploy.py")


if __name__ == "__main__":
    main()
