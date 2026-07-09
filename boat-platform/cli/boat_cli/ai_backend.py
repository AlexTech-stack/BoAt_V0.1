"""Thin stdlib-only client for any OpenAI-compatible chat completions endpoint.

Works with Ollama, LM Studio, llama.cpp server, vLLM, LocalAI, and OpenAI/cloud
endpoints — anything that speaks POST /v1/chat/completions.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class Message:
    role: str   # "system" | "user" | "assistant"
    content: str


class AiBackendError(Exception):
    pass


def complete(
    endpoint: str,
    model: str,
    messages: list[Message],
    timeout: int = 120,
    api_key: str = "",
    temperature: float = 0.15,
) -> str:
    """Send a chat completion request and return the assistant's reply text.

    Args:
        endpoint:    Base URL, e.g. ``http://localhost:11434/v1``.
        model:       Model identifier, e.g. ``qwen2.5-coder:7b``.
        messages:    Ordered list of system/user/assistant messages.
        timeout:     HTTP timeout in seconds.
        api_key:     Bearer token — leave empty for local servers that need none.
        temperature: Sampling temperature (low = more deterministic).

    Returns:
        The assistant reply string (stripped of leading/trailing whitespace).

    Raises:
        AiBackendError: on HTTP errors or unexpected response shape.
    """
    url = endpoint.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "temperature": temperature,
        "stream": False,
    }
    payload = json.dumps(body).encode()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise AiBackendError(
            f"HTTP {exc.code} from {url}: {body_text[:400]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AiBackendError(
            f"Cannot reach {url}: {exc.reason}\n"
            "Is your local LLM server running? "
            "(Ollama: `ollama serve`, LM Studio: enable local server)"
        ) from exc

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise AiBackendError(
            f"Unexpected response shape from {url}: {json.dumps(data)[:400]}"
        ) from exc


def extract_code(text: str) -> str:
    """Strip markdown code fences if the model wrapped its output in them."""
    lines = text.splitlines()
    # Find first ``` fence
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            start = i + 1
            break
    else:
        return text.strip()
    # Find closing fence
    end = len(lines)
    for i in range(len(lines) - 1, start - 1, -1):
        if lines[i].strip().startswith("```"):
            end = i
            break
    return "\n".join(lines[start:end]).strip()
