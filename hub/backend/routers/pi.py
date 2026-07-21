"""Core proxy router. Talks to the cloud core through the gateway /core proxy (or a bare host in legacy form) via pi_client."""

from fastapi import APIRouter
from pydantic import BaseModel

from services import pi_client

router = APIRouter()


class NoteRequest(BaseModel):
    content: str
    tags: str = ""
    project: str = ""
    note_type: str = "note"


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


