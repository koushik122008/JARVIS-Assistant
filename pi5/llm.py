"""Tiny Ollama client for the Pi 5 unit.

Mirrors the important parts of ``core/llm_client.py``:
  - streams the reply and splits it into complete sentences (for low-latency TTS)
  - reports tool calls so the assistant can execute them and loop back
"""
from __future__ import annotations

import json
import re
from collections.abc import Generator

import requests

# Sentence boundary: [.!?] + whitespace, or a blank line. Won't split on
# decimals like "3.5" because those have no whitespace after the dot.
_SENT_END = re.compile(r"(?<=[.!?])\s+|(?<=\n)\s*\n")


def chat_stream(
    messages: list,
    tools: list | None = None,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:3b",
    timeout: int = 120,
) -> Generator[dict, None, None]:
    """Stream an Ollama ``/api/chat`` request.

    Yields:
        {"type": "sentence", "text": str}       — each complete sentence as it arrives
        {"type": "done", "content": str, "tool_calls": list}  — final event
    """
    url = base_url.rstrip("/") + "/api/chat"
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": -1,
        "options": {"num_predict": 200},  # ~100 words — plenty for a spoken reply
    }
    if tools:
        payload["tools"] = tools

    with requests.post(url, json=payload, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        buf = ""
        full = ""
        tool_calls: list = []

        for raw in resp.iter_lines():
            if not raw:
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg = chunk.get("message", {})
            delta = msg.get("content") or ""
            full += delta
            buf += delta

            # Emit complete sentences as soon as they are ready
            while True:
                m = _SENT_END.search(buf)
                if not m:
                    break
                sentence = buf[: m.start() + 1].strip()
                buf = buf[m.end():]
                if sentence:
                    yield {"type": "sentence", "text": sentence}

            if msg.get("tool_calls"):
                tool_calls.extend(msg["tool_calls"])

            if chunk.get("done"):
                if buf.strip():
                    yield {"type": "sentence", "text": buf.strip()}
                yield {
                    "type": "done",
                    "content": full.strip(),
                    "tool_calls": tool_calls,
                }
                return
