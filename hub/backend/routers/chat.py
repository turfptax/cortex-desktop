"""Chat router — SSE proxy to LM Studio."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import lmstudio

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1024


@router.post("")
async def chat(req: ChatRequest):
    """Stream chat completions from LM Studio."""
    messages = [m.model_dump() for m in req.messages]

    try:
        return StreamingResponse(
            lmstudio.stream_chat(
                messages=messages,
                model=req.model,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LM Studio error: {e}")


@router.get("/models")
async def list_models():
    """List available models from LM Studio."""
    models = await lmstudio.list_models()
    return {"models": models}


@router.get("/health")
async def chat_health():
    """Check LM Studio connectivity."""
    online = await lmstudio.check_health()
    return {"lmstudio_online": online}
