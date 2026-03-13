"""LM Studio OpenAI-compatible API client."""

import json
from typing import AsyncGenerator

import httpx

from config import settings


async def list_models() -> list[dict]:
    """List available models from LM Studio."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.lmstudio_url}/models")
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
    except Exception:
        return []


async def stream_chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> AsyncGenerator[str, None]:
    """Stream chat completions from LM Studio.

    Yields SSE-formatted lines: 'data: {...}\\n\\n'
    """
    model = model or settings.lmstudio_default_model

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
        async with client.stream(
            "POST",
            f"{settings.lmstudio_url}/chat/completions",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break
                    yield f"data: {chunk}\n\n"


async def check_health() -> bool:
    """Check if LM Studio is reachable."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.lmstudio_url}/models")
            return resp.status_code == 200
    except Exception:
        return False
