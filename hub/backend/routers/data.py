"""Data explorer router — CRUD proxy to the Pi's cortex.db."""

from fastapi import APIRouter
from pydantic import BaseModel

from services import pi_client

router = APIRouter()


class QueryRequest(BaseModel):
    table: str
    filters: dict | None = None
    limit: int = 50
    order_by: str = "created_at DESC"


class UpsertRequest(BaseModel):
    table: str
    data: dict


class DeleteRequest(BaseModel):
    table: str
    id: str | int


@router.get("/tables")
async def list_tables():
    """Return all browsable tables with row counts."""
    result = await pi_client.table_counts()
    return result


@router.post("/query")
async def query_table(req: QueryRequest):
    """Query rows from a Pi database table."""
    filters_str = ""
    if req.filters:
        import json
        filters_str = json.dumps(req.filters)
    raw = await pi_client.query_table(
        table=req.table,
        filters=filters_str,
        limit=req.limit,
        order_by=req.order_by,
    )
    # Parse the RSP:query:[...] response
    resp_str = raw.get("response", "")
    if isinstance(resp_str, str) and resp_str.startswith("RSP:query:"):
        import json
        try:
            rows = json.loads(resp_str[len("RSP:query:"):])
            return {"rows": rows, "count": len(rows)}
        except (ValueError, json.JSONDecodeError):
            pass
    return {"rows": [], "count": 0, "error": raw.get("error", "Unknown error")}


@router.post("/upsert")
async def upsert_record(req: UpsertRequest):
    """Create or update a row in a Pi database table."""
    result = await pi_client.upsert_record(req.table, req.data)
    return result


@router.post("/delete")
async def delete_record(req: DeleteRequest):
    """Delete a row from a Pi database table."""
    result = await pi_client.delete_record(req.table, req.id)
    return result
