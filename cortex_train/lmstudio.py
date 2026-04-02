"""LM Studio teacher model client.

Provides synchronous API calls for training data synthesis.
Supports single-server and multi-server (work-stealing pool) modes.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional


def check_server(url: str, timeout: float = 5.0) -> Optional[List[str]]:
    """Check LM Studio connectivity. Returns list of model IDs or None."""
    url = url.rstrip("/")
    try:
        req = urllib.request.Request(f"{url}/models", method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return [m.get("id", "unknown") for m in data.get("data", [])]
    except Exception:
        return None


def call_teacher(
    url: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.8,
    max_tokens: int = 2048,
    timeout: float = 600.0,
) -> Optional[str]:
    """Synchronous non-streaming chat completion via LM Studio.

    Returns the assistant message content string, or None on error.
    Uses stdlib urllib to avoid requiring 'requests' as a dependency.
    """
    url = url.rstrip("/")
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{url}/chat/completions",
        data=payload,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except urllib.error.URLError as e:
        return None
    except Exception:
        return None


def build_teacher_messages(
    system_prompt: str,
    user_prompt: str,
    assistant_prefix: str = "[",
) -> List[Dict[str, str]]:
    """Build messages for teacher model with optional assistant prefix trick.

    The assistant prefix trick seeds the response with "[" to force JSON array output.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if assistant_prefix:
        messages.append({"role": "assistant", "content": assistant_prefix})
    return messages
