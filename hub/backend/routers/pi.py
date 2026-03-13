"""Pi proxy router — interact with Cortex Pi Zero."""

from fastapi import APIRouter
from pydantic import BaseModel

from services import pi_client

router = APIRouter()


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
