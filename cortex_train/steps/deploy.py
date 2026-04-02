"""Step 6: Export LoRA adapter to GGUF and deploy to the Pi.

Supports two modes:
- LoRA adapter export (small ~3-10MB file)
- Merged full model (adapter baked into base, ~500-800MB)

Handles GGUF conversion via llama.cpp, SCP to Pi, and service restart.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from cortex_train.config import TrainSettings
from cortex_train.errors import DeployError
from cortex_train.paths import TrainPaths
from cortex_train.pi_client import scp_to_pi, ssh_command
from cortex_train.progress import ProgressCallback, make_step_progress, null_progress


def _current_bloom(paths: TrainPaths) -> Optional[int]:
    """Read bloom number from training log or detect from bloom dirs."""
    if paths.training_log_path.exists():
        try:
            with open(paths.training_log_path) as f:
                data = json.load(f)
            if "bloom_number" in data:
                return data["bloom_number"]
            if "bloom" in data:
                return data["bloom"]
        except (json.JSONDecodeError, OSError):
            pass
    existing = []
    for p in paths.models.glob("pet-lora-bloom-*"):
        m = re.match(r"pet-lora-bloom-(\d+)$", p.name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=None)


def _model_slug(base_model: str) -> str:
    """Derive short slug from HuggingFace model ID."""
    name = base_model.split("/")[-1]
    for suffix in ["-Instruct", "-instruct", "-Chat", "-chat", "-it"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name


def _find_llama_cpp(paths: TrainPaths) -> Optional[Path]:
    """Find llama.cpp installation."""
    search = [
        paths.training_dir / "llama.cpp",
        paths.training_dir.parent / "llama.cpp",
        Path.home() / "llama.cpp",
        Path(os.environ.get("LLAMA_CPP_DIR", "")) if os.environ.get("LLAMA_CPP_DIR") else None,
    ]
    for p in search:
        if p and p.exists():
            for converter in [p / "convert_hf_to_gguf.py", p / "convert_lora_to_gguf.py"]:
                if converter.exists():
                    return p
    return None


def _find_llama_quantize(llama_cpp_dir: Path) -> Optional[Path]:
    """Find llama-quantize binary for k-quant conversions."""
    candidates = [
        llama_cpp_dir / "build" / "bin" / "llama-quantize",
        llama_cpp_dir / "build" / "bin" / "llama-quantize.exe",
        llama_cpp_dir / "llama-quantize",
        llama_cpp_dir / "llama-quantize.exe",
        llama_cpp_dir / "build" / "bin" / "Release" / "llama-quantize.exe",
    ]
    for p in candidates:
        if p.exists():
            return p
    which = shutil.which("llama-quantize")
    return Path(which) if which else None


def _export_merged_gguf(adapter_dir, base_model, output_path, llama_cpp_dir,
                        quantization, models_dir, emit) -> Optional[Path]:
    """Merge LoRA into base and convert to GGUF."""
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel

    emit("Loading base model for merge...")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    model = AutoModelForCausalLM.from_pretrained(base_model, torch_dtype=torch.float16)
    emit("Loading adapter...")
    model = PeftModel.from_pretrained(model, str(adapter_dir))
    emit("Merging weights...")
    model = model.merge_and_unload()

    merged_dir = models_dir / "pet-merged-hf"
    merged_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(merged_dir))
    tokenizer.save_pretrained(str(merged_dir))
    emit(f"Merged model saved to {merged_dir}")

    # Convert to GGUF
    converter = llama_cpp_dir / "convert_hf_to_gguf.py"
    if not converter.exists():
        raise DeployError(f"convert_hf_to_gguf.py not found in {llama_cpp_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    direct_types = {"f32", "f16", "bf16", "q8_0"}
    quant_lower = quantization.lower().replace("-", "_")

    if quant_lower in direct_types:
        cmd = [sys.executable, str(converter), "--outfile", str(output_path),
               "--outtype", quant_lower, str(merged_dir)]
        emit(f"Converting to GGUF {quantization}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise DeployError(f"GGUF conversion failed: {result.stderr[:500]}")
    else:
        quantize_bin = _find_llama_quantize(llama_cpp_dir)
        if not quantize_bin:
            emit(f"llama-quantize not found for {quantization}, falling back to q8_0")
            quant_lower = "q8_0"
            output_path = output_path.parent / output_path.name.replace(
                quantization.lower(), "q8_0")
            cmd = [sys.executable, str(converter), "--outfile", str(output_path),
                   "--outtype", "q8_0", str(merged_dir)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise DeployError(f"GGUF conversion failed: {result.stderr[:500]}")
        else:
            f16_path = output_path.parent / output_path.name.replace(quant_lower, "f16")
            cmd = [sys.executable, str(converter), "--outfile", str(f16_path),
                   "--outtype", "f16", str(merged_dir)]
            emit("Step 1: Converting to f16...")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise DeployError(f"f16 conversion failed: {result.stderr[:500]}")

            quant_upper = quantization.upper().replace("-", "_")
            cmd = [str(quantize_bin), str(f16_path), str(output_path), quant_upper]
            emit(f"Step 2: Quantizing f16 → {quant_upper}...")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise DeployError(f"Quantization failed: {result.stderr[:500]}")

            if f16_path.exists() and output_path.exists():
                f16_path.unlink()

    if output_path.exists():
        size_mb = output_path.stat().st_size / 1e6
        emit(f"GGUF exported ({size_mb:.1f} MB)")
        return output_path

    return None


def _deploy_merged_to_pi(settings, gguf_path, emit):
    """Upload merged GGUF to Pi and restart llama-server."""
    pi = settings.pi
    remote_path = f"{pi.model_deploy_dir}/{gguf_path.name}"

    emit(f"Uploading to Pi: {remote_path}...")
    scp_to_pi(gguf_path, pi.user, pi.host, remote_path, timeout=120)

    # Update llama-server.service model path
    emit("Updating llama-server config...")
    try:
        sed_cmd = (f"sudo sed -i 's|--model [^ \\\\]*|--model {remote_path}|' "
                   f"{pi.llama_service_path}")
        ssh_command(pi.user, pi.host, sed_cmd, timeout=10)
        ssh_command(pi.user, pi.host, "sudo systemctl daemon-reload", timeout=10)
    except Exception as e:
        emit(f"WARNING: Could not update llama-server config: {e}")

    # Restart llama-server only (keep cortex-core running for dream state)
    emit(f"Restarting {pi.llama_service_name}...")
    try:
        ssh_command(pi.user, pi.host,
                    f"sudo systemctl restart {pi.llama_service_name}", timeout=15)
        time.sleep(5)
        status = ssh_command(pi.user, pi.host,
                             f"sudo systemctl is-active {pi.llama_service_name}", timeout=10)
        if "active" in status:
            emit(f"{pi.llama_service_name} running with new model")
        else:
            emit(f"WARNING: {pi.llama_service_name} status: {status.strip()}")
    except Exception as e:
        emit(f"WARNING: Could not restart {pi.llama_service_name}: {e}")

    return True


def run_deploy(
    settings: TrainSettings,
    paths: TrainPaths,
    on_progress: ProgressCallback = null_progress,
    merge: bool = True,
    export_only: bool = False,
    deploy_only: bool = False,
    quantization: Optional[str] = None,
) -> dict:
    """Export LoRA adapter to GGUF and deploy to Pi.

    Args:
        merge: Merge adapter into base model (default True, recommended)
        export_only: Convert to GGUF only, skip deploy
        deploy_only: Deploy existing GGUF only, skip export
        quantization: Override quantization type (q8_0, q4_k_m, etc.)

    Returns:
        {ok, gguf_path, gguf_size_mb, bloom, deployed}
    """
    emit = make_step_progress("deploy", on_progress)

    base_model = settings.model.base_model
    quant = quantization or settings.model.gguf_quantization
    slug = _model_slug(base_model).lower()
    bloom = _current_bloom(paths)
    bloom_suffix = f"-bloom-{bloom}" if bloom else ""
    quant_tag = quant.lower().replace("-", "_")

    if merge:
        gguf_path = paths.exports / f"{slug}-pet-finetuned{bloom_suffix}-{quant_tag}.gguf"
    else:
        gguf_path = paths.exports / f"pet-lora{bloom_suffix}.gguf"

    emit(f"Model: {base_model}, quant: {quant}, bloom: {bloom}")

    # Export
    if not deploy_only:
        if not paths.adapter_dir.exists():
            raise DeployError(f"Adapter not found at {paths.adapter_dir}. Run train step first.")

        llama_cpp_dir = _find_llama_cpp(paths)
        if not llama_cpp_dir:
            raise DeployError("llama.cpp not found. Set LLAMA_CPP_DIR env var or clone it.")

        if merge:
            emit("Merging adapter and converting to GGUF...", pct=10)
            try:
                actual = _export_merged_gguf(
                    paths.adapter_dir, base_model, gguf_path,
                    llama_cpp_dir, quant, paths.models, emit)
                if actual:
                    gguf_path = actual
            except ImportError as e:
                raise DeployError(f"Missing ML packages for merge: {e}")
        else:
            emit("Exporting LoRA adapter to GGUF...", pct=10)
            converter = llama_cpp_dir / "convert_lora_to_gguf.py"
            if not converter.exists():
                converter = llama_cpp_dir / "scripts" / "convert_lora_to_gguf.py"
            if not converter.exists():
                raise DeployError(f"convert_lora_to_gguf.py not found in {llama_cpp_dir}")

            gguf_path.parent.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(converter), "--outfile", str(gguf_path),
                   str(paths.adapter_dir)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise DeployError(f"LoRA GGUF export failed: {result.stderr[:500]}")

        if not gguf_path.exists():
            raise DeployError("Export produced no output file")

        size_mb = gguf_path.stat().st_size / 1e6
        emit(f"Exported: {gguf_path.name} ({size_mb:.1f} MB)", pct=60)

    # Deploy
    deployed = False
    if not export_only:
        if not gguf_path.exists():
            raise DeployError(f"GGUF not found at {gguf_path}. Run without --deploy-only.")

        emit("Deploying to Pi...", pct=70)
        if merge:
            deployed = _deploy_merged_to_pi(settings, gguf_path, emit)
        else:
            # LoRA adapter deploy: update config + restart cortex-core
            remote_path = f"{settings.pi.model_deploy_dir}/pet-lora.gguf"
            scp_to_pi(gguf_path, settings.pi.user, settings.pi.host, remote_path)
            try:
                ssh_command(settings.pi.user, settings.pi.host,
                            f"sudo systemctl restart {settings.pi.service_name}", timeout=15)
                deployed = True
            except Exception as e:
                emit(f"WARNING: Could not restart service: {e}")

    size_mb = gguf_path.stat().st_size / 1e6 if gguf_path.exists() else 0
    emit(f"Deploy complete: {gguf_path.name} ({size_mb:.1f} MB), deployed={deployed}", pct=100)

    return {
        "ok": True,
        "gguf_path": str(gguf_path),
        "gguf_size_mb": round(size_mb, 1),
        "bloom": bloom,
        "deployed": deployed,
    }
