"""Step 4: Export LoRA adapter to GGUF and deploy to the Pi.

Converts the PEFT LoRA adapter to GGUF format using llama.cpp's
conversion script, then SCPs it to the Pi and updates the config.

Usage:
    python 04_export_deploy.py                # full export + deploy
    python 04_export_deploy.py --export-only  # convert to GGUF only
    python 04_export_deploy.py --deploy-only  # SCP existing GGUF to Pi
    python 04_export_deploy.py --merge        # merge adapter into base and export full model

Requirements:
    pip install torch transformers peft
    llama.cpp repo (cloned automatically if not found)
    SSH access to the Pi

Outputs:
    ../exports/pet-lora.gguf  - GGUF LoRA adapter file
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
MODELS_DIR = PROJECT_DIR / "models"
EXPORTS_DIR = PROJECT_DIR / "exports"
CONFIG_PATH = PROJECT_DIR / "config" / "settings.json"
ADAPTER_DIR = MODELS_DIR / "pet-lora"


def _current_bloom():
    """Read the bloom number from the latest training log, or detect from bloom dirs."""
    log_path = ADAPTER_DIR / "training_log.json"
    if log_path.exists():
        try:
            with open(log_path) as f:
                data = json.load(f)
            if "bloom" in data:
                return data["bloom"]
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback: find highest pet-lora-bloom-* directory
    existing = []
    for p in MODELS_DIR.glob("pet-lora-bloom-*"):
        m = re.match(r"pet-lora-bloom-(\d+)$", p.name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=None)


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def find_llama_cpp():
    """Find or clone llama.cpp for the conversion script."""
    # Check common locations
    search_paths = [
        PROJECT_DIR / "llama.cpp",
        PROJECT_DIR.parent / "llama.cpp",
        Path.home() / "llama.cpp",
        Path(os.environ.get("LLAMA_CPP_DIR", "")) if os.environ.get("LLAMA_CPP_DIR") else None,
    ]

    for p in search_paths:
        if p and p.exists():
            converter = p / "convert_lora_to_gguf.py"
            if converter.exists():
                return p
            # Also check gguf-py based converter
            converter2 = p / "scripts" / "convert_lora_to_gguf.py"
            if converter2.exists():
                return p

    return None


def clone_llama_cpp():
    """Clone llama.cpp repo for conversion tools."""
    target = PROJECT_DIR / "llama.cpp"
    print(f"\n  Cloning llama.cpp to {target}...")
    print("  (Only need the conversion script, this is a shallow clone)")

    try:
        subprocess.run(
            ["git", "clone", "--depth=1",
             "https://github.com/ggerganov/llama.cpp.git",
             str(target)],
            check=True,
        )
        # Install gguf Python package
        subprocess.run(
            [sys.executable, "-m", "pip", "install",
             str(target / "gguf-py")],
            check=True,
        )
        return target
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Failed to clone llama.cpp: {e}")
        return None
    except FileNotFoundError:
        print("  ERROR: 'git' not found. Install git first.")
        return None


def export_lora_gguf(adapter_dir, output_path, llama_cpp_dir):
    """Convert PEFT LoRA adapter to GGUF format."""
    print(f"\n=== Exporting LoRA to GGUF ===")
    print(f"  Adapter: {adapter_dir}")
    print(f"  Output:  {output_path}")

    converter = llama_cpp_dir / "convert_lora_to_gguf.py"
    if not converter.exists():
        converter = llama_cpp_dir / "scripts" / "convert_lora_to_gguf.py"
    if not converter.exists():
        print(f"  ERROR: convert_lora_to_gguf.py not found in {llama_cpp_dir}")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        cmd = [
            sys.executable, str(converter),
            "--outfile", str(output_path),
            str(adapter_dir),
        ]
        print(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"  STDERR: {result.stderr}")
            # Try alternative approach
            print("  Trying with --outtype f16...")
            cmd_f16 = cmd + ["--outtype", "f16"]
            result = subprocess.run(cmd_f16, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  ERROR: Conversion failed: {result.stderr}")
                return False

        if output_path.exists():
            size_mb = output_path.stat().st_size / 1e6
            print(f"  GGUF adapter exported ({size_mb:.1f} MB)")
            return True
        else:
            print("  ERROR: Output file not created")
            return False

    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def export_merged_gguf(adapter_dir, base_model, output_path, llama_cpp_dir):
    """Merge LoRA into base model and convert full model to GGUF."""
    print(f"\n=== Merging adapter and exporting full GGUF ===")

    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from peft import PeftModel
    except ImportError:
        print("  ERROR: Need torch, transformers, peft. Install them first.")
        return False

    # 1. Load and merge
    print(f"  Loading base model: {base_model}")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.float16
    )
    print(f"  Loading adapter: {adapter_dir}")
    model = PeftModel.from_pretrained(model, str(adapter_dir))
    print("  Merging weights...")
    model = model.merge_and_unload()

    # 2. Save merged model as HF format
    merged_dir = MODELS_DIR / "pet-merged-hf"
    merged_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(merged_dir))
    tokenizer.save_pretrained(str(merged_dir))
    print(f"  Merged model saved to {merged_dir}")

    # 3. Convert to GGUF
    converter = llama_cpp_dir / "convert_hf_to_gguf.py"
    if not converter.exists():
        print(f"  ERROR: convert_hf_to_gguf.py not found in {llama_cpp_dir}")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        cmd = [
            sys.executable, str(converter),
            "--outfile", str(output_path),
            "--outtype", "q8_0",
            str(merged_dir),
        ]
        print(f"  Converting to GGUF Q8_0...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}")
            return False

        if output_path.exists():
            size_mb = output_path.stat().st_size / 1e6
            print(f"  Full GGUF model exported ({size_mb:.1f} MB)")
            return True

    except Exception as e:
        print(f"  ERROR: {e}")
        return False

    return False


def deploy_to_pi(config, gguf_path, is_full_model=False, base_model=""):
    """SCP the GGUF file to the Pi and update config."""
    pi = config["pi"]
    pi_addr = f"{pi['user']}@{pi['host']}"
    deploy_dir = pi["model_deploy_dir"]

    if is_full_model:
        # Use the GGUF filename as-is (already model-specific from main())
        remote_filename = gguf_path.name
    else:
        remote_filename = "pet-lora.gguf"

    remote_path = f"{deploy_dir}/{remote_filename}"

    print(f"\n=== Deploying to Pi ===")
    print(f"  Source: {gguf_path}")
    print(f"  Target: {pi_addr}:{remote_path}")

    # Ensure directory exists
    try:
        subprocess.run(
            ["ssh", pi_addr, f"mkdir -p {deploy_dir}"],
            check=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        print("  ERROR: SSH timed out. Is the Pi reachable?")
        return False
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: SSH failed: {e}")
        return False

    # SCP the file
    print("  Uploading GGUF file...")
    try:
        result = subprocess.run(
            ["scp", str(gguf_path), f"{pi_addr}:{remote_path}"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  ERROR: SCP failed: {result.stderr}")
            return False
        print("  Upload complete.")
    except subprocess.TimeoutExpired:
        print("  ERROR: SCP timed out.")
        return False

    # Update config.py on Pi
    if not is_full_model:
        config_path = pi["config_path"]
        print(f"  Updating config: PET_LORA_PATH = \"{remote_path}\"")
        try:
            # Use sed to update the PET_LORA_PATH line in config.py
            sed_cmd = (
                f'sed -i \'s|^PET_LORA_PATH = .*|'
                f'PET_LORA_PATH = "{remote_path}"|g\' {config_path}'
            )
            subprocess.run(
                ["ssh", pi_addr, sed_cmd],
                check=True, timeout=10,
            )
            print("  Config updated.")
        except Exception as e:
            print(f"  WARNING: Could not update config: {e}")
            print(f"  Manually set PET_LORA_PATH = \"{remote_path}\" in {config_path}")
    else:
        config_path = pi["config_path"]
        print(f"  Updating config: PET_MODEL_PATH to use merged model")
        try:
            sed_cmd = (
                f'sed -i \'s|^PET_MODEL_PATH = .*|'
                f'PET_MODEL_PATH = os.path.join(PET_MODEL_DIR, "{remote_filename}")|g\' {config_path}'
            )
            subprocess.run(
                ["ssh", pi_addr, sed_cmd],
                check=True, timeout=10,
            )
            print("  Config updated.")
        except Exception as e:
            print(f"  WARNING: Could not update config: {e}")

    # Restart the appropriate service
    import time

    if is_full_model:
        # For merged models: update llama-server.service to load the new model,
        # then restart ONLY llama-server. We intentionally do NOT restart
        # cortex-core so that the heartbeat dream state is preserved — the
        # dream_complete notification (sent after this step) needs a running
        # cortex-core to properly wake the pet.
        llama_service_path = pi.get("llama_service_path",
                                    "/etc/systemd/system/llama-server.service")
        llama_service_name = pi.get("llama_service_name", "llama-server")

        print(f"  Updating llama-server model path → {remote_path}")
        try:
            sed_cmd = (
                f"sudo sed -i 's|--model [^ \\\\]*|--model {remote_path}|' "
                f"{llama_service_path}"
            )
            subprocess.run(
                ["ssh", pi_addr, sed_cmd],
                check=True, timeout=10,
            )
            subprocess.run(
                ["ssh", pi_addr, "sudo systemctl daemon-reload"],
                check=True, timeout=10,
            )
            print("  llama-server.service updated.")
        except Exception as e:
            print(f"  WARNING: Could not update llama-server.service: {e}")
            print(f"  Manually update --model in {llama_service_path}")

        print(f"  Restarting {llama_service_name}...")
        try:
            subprocess.run(
                ["ssh", pi_addr,
                 f"sudo systemctl restart {llama_service_name}"],
                check=True, timeout=15,
            )
            time.sleep(5)  # Wait for model to load
            result = subprocess.run(
                ["ssh", pi_addr,
                 f"sudo systemctl is-active {llama_service_name}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip() == "active":
                print(f"  {llama_service_name} is running with new model.")
            else:
                print(f"  WARNING: {llama_service_name} may not have started"
                      f" correctly (status: {result.stdout.strip()})")
        except Exception as e:
            print(f"  WARNING: Could not restart {llama_service_name}: {e}")
            print(f"  Manually run: sudo systemctl restart"
                  f" {llama_service_name}")
    else:
        # For LoRA adapter deploys: restart cortex-core to pick up new config
        service_name = pi.get("service_name", "cortex-core")
        print(f"  Restarting {service_name}...")
        try:
            subprocess.run(
                ["ssh", pi_addr,
                 f"sudo systemctl restart {service_name}"],
                check=True, timeout=15,
            )
            time.sleep(3)
            result = subprocess.run(
                ["ssh", pi_addr,
                 f"sudo systemctl status {service_name} --no-pager -l"],
                capture_output=True, text=True, timeout=10,
            )
            print(result.stdout)
        except Exception as e:
            print(f"  WARNING: Could not restart service: {e}")
            print(f"  Manually run: sudo systemctl restart {service_name}")

    return True


def _resolve_lmstudio_dir(user_dir=None):
    """Find or validate LM Studio models directory."""
    if user_dir:
        p = Path(user_dir)
        if p.exists():
            return p
        print(f"  WARNING: --lmstudio-dir {user_dir} not found")
        return None

    # Auto-detect common LM Studio paths on Windows/Mac/Linux
    candidates = [
        Path.home() / ".cache" / "lm-studio" / "models",
        Path.home() / ".lmstudio" / "models",
        Path(os.environ.get("LOCALAPPDATA", "")) / "LM Studio" / "models",
        Path(os.environ.get("APPDATA", "")) / "LM Studio" / "models",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _model_slug(base_model: str) -> str:
    """Derive a short slug from a HuggingFace model ID.

    'HuggingFaceTB/SmolLM2-135M-Instruct' → 'SmolLM2-135M'
    'Qwen/Qwen2.5-0.5B-Instruct' → 'Qwen2.5-0.5B'
    """
    name = base_model.split("/")[-1]  # Drop publisher prefix
    # Remove common suffixes like -Instruct, -Chat, -it
    for suffix in ["-Instruct", "-instruct", "-Chat", "-chat", "-it"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _copy_to_lmstudio(gguf_path, lmstudio_dir, base_model="", bloom=None):
    """Copy GGUF to LM Studio models directory using proper naming convention.

    LM Studio expects: models/{publisher}/{ModelName-GGUF}/{filename}.gguf
    The GGUF always overwrites the same path so LM Studio sees one model.
    The bloom number is included in the filename for identification.
    """
    slug = _model_slug(base_model) if base_model else "Pet-Finetuned"
    bloom_tag = f"-Bloom-{bloom}" if bloom else ""
    repo_name = f"{slug}-Pet-Finetuned-GGUF"
    dest_filename = f"{slug}-Pet-Finetuned{bloom_tag}-Q8_0.gguf"
    dest_dir = lmstudio_dir / "cortex-pet" / repo_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / dest_filename

    print(f"\n=== Copying to LM Studio ===")
    print(f"  Source: {gguf_path}")
    print(f"  Dest:   {dest}")

    try:
        # Remove previous bloom GGUFs so LM Studio only sees the latest
        for old in dest_dir.glob(f"{slug}-Pet-Finetuned*-Q8_0.gguf"):
            if old.name != dest_filename:
                old.unlink()
                print(f"  Removed old: {old.name}")
        shutil.copy2(str(gguf_path), str(dest))
        size_mb = dest.stat().st_size / 1e6
        print(f"  Copied ({size_mb:.1f} MB)")
        print(f"  Restart LM Studio and load the model to use it in Cortex Hub Chat")
    except Exception as e:
        print(f"  WARNING: Could not copy to LM Studio: {e}")
        print(f"  Manually copy {gguf_path} to {lmstudio_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Export LoRA to GGUF and deploy to Pi"
    )
    parser.add_argument("--export-only", action="store_true",
                        help="Only convert to GGUF, skip deploy")
    parser.add_argument("--deploy-only", action="store_true",
                        help="Only deploy existing GGUF to Pi")
    parser.add_argument("--merge", action="store_true",
                        help="Merge adapter into base model and export full GGUF")
    parser.add_argument("--adapter-dir", default=None,
                        help="Path to LoRA adapter (default: ../models/pet-lora)")
    parser.add_argument("--llama-cpp-dir", default=None,
                        help="Path to llama.cpp repo")
    parser.add_argument("--lmstudio-dir", default=None,
                        help="Path to LM Studio models dir (auto-copies GGUF for local chat)")
    args = parser.parse_args()

    config = load_config()
    model_cfg = config.get("model", {})
    base_model = model_cfg.get("base_model", "HuggingFaceTB/SmolLM2-135M-Instruct")
    adapter_dir = Path(args.adapter_dir) if args.adapter_dir else ADAPTER_DIR

    print("=== Cortex Pet — Export & Deploy ===")

    # Determine output paths with bloom suffix
    slug = _model_slug(base_model).lower()
    bloom = _current_bloom()
    bloom_suffix = f"-bloom-{bloom}" if bloom else ""
    if args.merge:
        gguf_path = EXPORTS_DIR / f"{slug}-pet-finetuned{bloom_suffix}-q8_0.gguf"
    else:
        gguf_path = EXPORTS_DIR / f"pet-lora{bloom_suffix}.gguf"

    if bloom:
        print(f"  Bloom:          {bloom}")
    # Also keep a copy as pet-lora.gguf for backwards compat (deploy expects it)
    gguf_latest = EXPORTS_DIR / "pet-lora.gguf" if not args.merge else None

    # Export step
    if not args.deploy_only:
        # Verify adapter exists
        if not adapter_dir.exists():
            print(f"\nERROR: Adapter not found at {adapter_dir}")
            print("Run 02_train_pet.py first.")
            sys.exit(1)

        # Find llama.cpp
        if args.llama_cpp_dir:
            llama_cpp_dir = Path(args.llama_cpp_dir)
        else:
            llama_cpp_dir = find_llama_cpp()

        if llama_cpp_dir is None:
            print("\nllama.cpp not found. Need it for GGUF conversion.")
            print("Options:")
            print("  1. Let me clone it (will run: git clone llama.cpp)")
            print("  2. Set --llama-cpp-dir /path/to/llama.cpp")
            print("  3. Set LLAMA_CPP_DIR environment variable")

            # Auto-clone when running non-interactively (e.g., from pipeline UI)
            # Note: on Windows, isatty() returns True even for NUL device,
            # so we also catch EOFError as a fallback for non-interactive mode.
            try:
                answer = input("\nClone llama.cpp now? [Y/n] ").strip().lower()
            except EOFError:
                answer = "y"  # Auto-clone in non-interactive mode
                print("\n  (Non-interactive mode — auto-cloning llama.cpp)")

            if answer in ("", "y", "yes"):
                llama_cpp_dir = clone_llama_cpp()
                if llama_cpp_dir is None:
                    sys.exit(1)
            else:
                sys.exit(1)

        if args.merge:
            success = export_merged_gguf(
                adapter_dir, base_model, gguf_path, llama_cpp_dir
            )
        else:
            success = export_lora_gguf(adapter_dir, gguf_path, llama_cpp_dir)

        if not success:
            print("\nExport failed!")
            sys.exit(1)

        # Copy versioned GGUF to generic name for deploy compatibility
        if gguf_latest and gguf_path != gguf_latest and gguf_path.exists():
            shutil.copy2(gguf_path, gguf_latest)
            print(f"  Copied to {gguf_latest.name} (for deploy)")

    # Copy to LM Studio models directory (optional)
    if not args.deploy_only and gguf_path.exists():
        lmstudio_dir = _resolve_lmstudio_dir(args.lmstudio_dir)
        if lmstudio_dir:
            _copy_to_lmstudio(gguf_path, lmstudio_dir, base_model, bloom=bloom)

    # Deploy step
    if not args.export_only:
        if not gguf_path.exists():
            print(f"\nERROR: GGUF file not found at {gguf_path}")
            print("Run without --deploy-only to export first.")
            sys.exit(1)

        success = deploy_to_pi(config, gguf_path, is_full_model=args.merge, base_model=base_model)
        if not success:
            print("\nDeploy failed!")
            sys.exit(1)

    # Summary
    print(f"\n=== Complete ===")
    if not args.deploy_only:
        size_mb = gguf_path.stat().st_size / 1e6 if gguf_path.exists() else 0
        print(f"  GGUF file: {gguf_path} ({size_mb:.1f} MB)")
    if not args.export_only:
        print(f"  Deployed to Pi: {config['pi']['host']}")
        if args.merge:
            print(f"  Mode: Full merged model (replaced base)")
        else:
            print(f"  Mode: LoRA adapter (loaded alongside base)")
        print(f"\n  Test with: pet_ask via MCP or BLE")
        print(f"  Check status: pet_status via MCP")


if __name__ == "__main__":
    main()
