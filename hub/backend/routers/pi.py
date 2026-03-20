"""Pi proxy router — interact with Cortex Pi Zero."""

import subprocess

from fastapi import APIRouter
from pydantic import BaseModel

from config import settings
from services import pi_client

router = APIRouter()

# SSH address for Pi management commands (git pull, service restart)
_PI_SSH_USER = "turfptax"
_PI_SSH_ADDR = f"{_PI_SSH_USER}@{settings.pi_host}"
_PI_CORE_DIR = "/home/turfptax/cortex-core"


class PetAskRequest(BaseModel):
    prompt: str


class NoteRequest(BaseModel):
    content: str
    tags: str = ""
    project: str = ""
    note_type: str = "note"


class PetFeedRequest(BaseModel):
    type: str = "chat_snack"


class PetCleanRequest(BaseModel):
    discard_ids: list[int] = []


class PetUpdateIntelligenceRequest(BaseModel):
    final_loss: float | None = None
    perplexity_base: float | None = None
    perplexity_finetuned: float | None = None
    lora_version: str = "unknown"
    training_time_s: float | None = None
    dataset_size: int | None = None


class CommandRequest(BaseModel):
    command: str
    payload: dict | None = None


class QueryRequest(BaseModel):
    table: str
    filters: str = ""
    limit: int = 20
    order_by: str = "created_at DESC"


@router.get("/status")
async def get_status():
    """Get Pi status (health + system info)."""
    return await pi_client.get_status()


@router.get("/online")
async def check_online():
    """Quick connectivity check."""
    online = await pi_client.check_online()
    return {"online": online}


@router.post("/pet/ask")
async def pet_ask(req: PetAskRequest):
    """Send a message to the pet on the Pi."""
    return await pi_client.pet_ask(req.prompt)


@router.get("/pet/status")
async def pet_status():
    """Get pet status (stage, mood, XP)."""
    return await pi_client.pet_status()


@router.get("/pet/history")
async def pet_history(limit: int = 20):
    """Get pet conversation history."""
    return await pi_client.pet_history(limit)


# ── Pet Vitals (Tamagotchi) ──────────────────────────────────────


@router.get("/pet/vitals")
async def pet_vitals():
    """Get current pet vitals (hunger, cleanliness, energy, etc.)."""
    return await pi_client.send_command_parsed("pet_vitals")


@router.post("/pet/feed")
async def pet_feed(req: PetFeedRequest):
    """Feed the pet."""
    return await pi_client.send_command_parsed(
        "pet_feed", {"type": req.type}
    )


@router.post("/pet/clean")
async def pet_clean(req: PetCleanRequest):
    """Clean the pet by discarding bad interactions."""
    return await pi_client.send_command_parsed(
        "pet_clean", {"discard_ids": req.discard_ids}
    )


@router.post("/pet/rest")
async def pet_rest():
    """Rest the pet — instant energy boost (+10%)."""
    return await pi_client.send_command_parsed("pet_rest")


@router.get("/pet/intelligence")
async def pet_intelligence():
    """Get pet intelligence score breakdown."""
    return await pi_client.send_command_parsed("pet_intelligence")


@router.post("/pet/update-intelligence")
async def pet_update_intelligence(req: PetUpdateIntelligenceRequest):
    """Push training metrics to update pet intelligence."""
    return await pi_client.send_command_parsed(
        "pet_update_intelligence", req.model_dump(exclude_none=True)
    )


@router.get("/pet/vitals-history")
async def pet_vitals_history(hours: int = 24):
    """Get vitals history for charting."""
    return await pi_client.send_command_parsed(
        "pet_vitals_history", {"hours": hours}
    )


@router.get("/pet/coma-status")
async def pet_coma_status():
    """Get detailed coma status."""
    return await pi_client.send_command_parsed("pet_coma_status")


@router.get("/pet/coma-history")
async def pet_coma_history():
    """Get past coma events."""
    return await pi_client.send_command_parsed("pet_coma_history")


@router.get("/pet/training-history")
async def pet_training_history():
    """Get LoRA deployment and intelligence history."""
    return await pi_client.send_command_parsed("pet_training_history")


@router.get("/pet/analytics")
async def pet_analytics(days: int = 365):
    """Get pet evolution analytics (stage progression, mood trends)."""
    return await pi_client.send_command_parsed(
        "pet_analytics", {"days": min(days, 365)}
    )


@router.post("/pet/tuck-in")
async def pet_tuck_in():
    """Tuck the pet in — put to sleep and check training readiness.

    Since the Pi can't call back to the Hub (firewall), we report
    dream_ready based on interactions/cooldown only (not hub reachability).
    If ready, the Hub triggers training directly via /training/dream-cycle.
    """
    result = await pi_client.send_command_parsed("tuck_in")
    # Override hub_available since training runs locally on the Hub
    if result and result.get("data"):
        result["data"]["hub_available"] = True
        dream_ready = (
            result["data"].get("interactions_ready", False)
            and result["data"].get("cooldown_ok", False)
        )
        result["data"]["dream_ready"] = dream_ready

        # Auto-trigger dream training when all conditions are met
        if dream_ready:
            from routers.training import start_dream_cycle, DreamCycleRequest
            dream_req = DreamCycleRequest(
                pi_ip=settings.pi_host,
                pi_port=settings.pi_port,
                trigger="tuck_in",
            )
            try:
                dream_result = await start_dream_cycle(dream_req)
                result["data"]["dream_started"] = True
                result["data"]["dream"] = dream_result
            except Exception as e:
                result["data"]["dream_started"] = False
                result["data"]["dream_error"] = str(e)

    return result


@router.post("/pet/wake")
async def pet_wake():
    """Wake the pet from sleep."""
    return await pi_client.send_command_parsed("pet_wake")


@router.post("/pet/force-train")
async def pet_force_train():
    """Force-trigger dream training cycle from the Hub side.

    Instead of asking the Pi to call back to the Hub (blocked by firewall),
    we put the pet to sleep, then start the dream cycle directly on the Hub.
    """
    # Put pet to sleep first
    sleep_result = await pi_client.send_command_parsed("pet_sleep", {"reason": "force_train"})

    # Start dream cycle on the Hub directly
    from routers.training import start_dream_cycle, DreamCycleRequest
    dream_req = DreamCycleRequest(
        pi_ip=settings.pi_host,
        pi_port=settings.pi_port,
        trigger="force_train",
    )
    try:
        dream_result = await start_dream_cycle(dream_req)
        return {"data": {"started": True, "dream": dream_result}}
    except Exception as e:
        return {"data": None, "error": str(e)}


@router.get("/pet/heartbeat-status")
async def pet_heartbeat_status():
    """Get heartbeat system status (enabled, interval, totals)."""
    return await pi_client.send_command_parsed("heartbeat_status")


@router.get("/pet/heartbeat-log")
async def pet_heartbeat_log(limit: int = 50):
    """Get recent heartbeat reflections."""
    return await pi_client.send_command_parsed(
        "heartbeat_log", {"limit": min(limit, 100)}
    )


@router.post("/notes")
async def send_note(req: NoteRequest):
    """Send a note to the Pi."""
    return await pi_client.send_note(
        content=req.content,
        tags=req.tags,
        project=req.project,
        note_type=req.note_type,
    )


@router.get("/notes")
async def get_notes(limit: int = 20):
    """Get notes from the Pi."""
    return await pi_client.query_table("notes", limit=limit)


@router.post("/cmd")
async def send_command(req: CommandRequest):
    """Send any command to the Pi."""
    return await pi_client.send_command(req.command, req.payload)


@router.post("/query")
async def query(req: QueryRequest):
    """Query a table on the Pi."""
    return await pi_client.query_table(
        table=req.table,
        filters=req.filters,
        limit=req.limit,
        order_by=req.order_by,
    )


# ── Pi Firmware Update ────────────────────────────────────────────


def _ssh_run(cmd: str, timeout: int = 15) -> tuple[int, str, str]:
    """Run a command on the Pi via SSH. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["ssh", _PI_SSH_ADDR, cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


@router.get("/update/check")
async def check_update():
    """Check if a cortex-core update is available on the Pi.

    Compares the Pi's current commit with the remote HEAD.
    """
    try:
        # Get current commit on Pi
        rc, current_hash, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse HEAD"
        )
        if rc != 0:
            return {"error": "Could not get current commit", "available": False}

        # Get current branch
        _, current_branch, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse --abbrev-ref HEAD"
        )

        # Fetch latest from remote (no merge)
        _ssh_run(f"cd {_PI_CORE_DIR} && git fetch origin", timeout=30)

        # Get remote HEAD for this branch
        rc, remote_hash, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse origin/{current_branch}"
        )
        if rc != 0:
            return {"error": "Could not get remote commit", "available": False}

        # Get commit log between current and remote
        changelog = ""
        if current_hash != remote_hash:
            _, changelog, _ = _ssh_run(
                f"cd {_PI_CORE_DIR} && git log --oneline "
                f"{current_hash}..origin/{current_branch}"
            )

        # Get current tag if any
        _, current_tag, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git describe --tags --exact-match 2>/dev/null || echo ''"
        )

        # Get current commit message
        _, current_message, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git log -1 --format=%s"
        )

        return {
            "update_available": current_hash != remote_hash,
            "current_commit": current_hash[:8],
            "latest_commit": remote_hash[:8],
            "current_message": current_message or "",
            "current_tag": current_tag or None,
            "branch": current_branch,
            "changelog": changelog,
        }
    except subprocess.TimeoutExpired:
        return {"error": "SSH timed out — is the Pi reachable?", "available": False}
    except Exception as e:
        return {"error": str(e), "available": False}


@router.post("/update/apply")
async def apply_update():
    """Pull latest cortex-core code on the Pi and restart the service.

    Saves the current commit hash for rollback, runs git pull,
    then restarts cortex-core.service.
    """
    import asyncio

    try:
        # Save current commit for rollback
        rc, old_hash, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse HEAD"
        )
        if rc != 0:
            return {"ok": False, "error": "Could not get current commit"}

        # Get current branch
        _, branch, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse --abbrev-ref HEAD"
        )

        # Stash any local changes (deploy.sh uses SCP, bypassing git)
        _ssh_run(f"cd {_PI_CORE_DIR} && git stash", timeout=10)

        # Pull latest code
        rc, pull_out, pull_err = _ssh_run(
            f"cd {_PI_CORE_DIR} && git pull origin {branch}",
            timeout=60,
        )
        if rc != 0:
            return {
                "ok": False,
                "error": f"git pull failed: {pull_err or pull_out}",
                "rollback_hash": old_hash[:8],
            }

        # Drop stash (pulled code supersedes SCP'd files)
        _ssh_run(f"cd {_PI_CORE_DIR} && git stash drop 2>/dev/null || true", timeout=5)

        # Get new commit
        _, new_hash, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse HEAD"
        )

        # Restart cortex-core service
        rc, _, restart_err = _ssh_run(
            "sudo systemctl restart cortex-core",
            timeout=20,
        )

        # Wait for service to come back up
        await asyncio.sleep(3)

        # Verify service is running
        rc2, status, _ = _ssh_run(
            "sudo systemctl is-active cortex-core"
        )
        service_ok = status == "active"

        if not service_ok:
            # Service failed — offer rollback info
            return {
                "success": False,
                "error": "Service failed to start after update",
                "old_commit": old_hash[:8],
                "new_commit": new_hash[:8],
                "pull_output": pull_out or "",
                "service_restarted": False,
                "rollback_hash": old_hash,
            }

        return {
            "success": True,
            "old_commit": old_hash[:8],
            "new_commit": new_hash[:8],
            "pull_output": pull_out or "",
            "service_restarted": True,
            "branch": branch,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "SSH timed out — is the Pi reachable?", "old_commit": "", "new_commit": "", "pull_output": "", "service_restarted": False}
    except Exception as e:
        return {"success": False, "error": str(e), "old_commit": "", "new_commit": "", "pull_output": "", "service_restarted": False}


@router.post("/update/rollback")
async def rollback_update(commit: str = ""):
    """Rollback Pi firmware to a specific commit.

    If no commit is specified, rolls back one commit.
    """
    try:
        if not commit:
            # Roll back one commit
            target = "HEAD~1"
        else:
            # Validate it looks like a commit hash
            if not all(c in "0123456789abcdef" for c in commit.lower()):
                return {"success": False, "error": "Invalid commit hash"}
            target = commit

        # Save current commit before rollback
        _, old_hash, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse HEAD"
        )

        rc, checkout_out, checkout_err = _ssh_run(
            f"cd {_PI_CORE_DIR} && git checkout {target}",
            timeout=15,
        )
        if rc != 0:
            return {"success": False, "error": f"Checkout failed: {checkout_err}"}

        # Restart service
        _ssh_run("sudo systemctl restart cortex-core", timeout=20)

        import asyncio
        await asyncio.sleep(3)

        _, new_hash, _ = _ssh_run(
            f"cd {_PI_CORE_DIR} && git rev-parse HEAD"
        )
        _, status, _ = _ssh_run("sudo systemctl is-active cortex-core")

        return {
            "success": True,
            "previous_commit": old_hash[:8],
            "rolled_back_to": new_hash[:8],
            "service_restarted": status == "active",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "SSH timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}
