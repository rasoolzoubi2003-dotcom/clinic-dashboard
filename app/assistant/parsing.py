"""Recover tool calls that a small model wrote as plain text instead of a real tool call.

Local models sometimes reply with something like
    {"name": "find_available_slots", "arguments": {"doctor": "Cardiology", "date": "2026-11-08"}}
or wrap it in <tool_call> tags or a ```json block. We detect those and run them as tool calls.
"""

import json
import re
from collections.abc import Collection

from app.assistant.providers.base import ToolCall

_WRAPPERS = re.compile(r"</?tool_call>|```(?:json)?", re.IGNORECASE)


def _as_call(obj: object, known: Collection[str]) -> tuple[str, object] | None:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict):  # OpenAI-style {"function": {"name": ..., "arguments": ...}}
        obj = obj["function"]
    name = obj.get("name")
    if name not in known:
        return None
    for key in ("arguments", "parameters", "args", "input"):
        if key in obj:
            return name, obj[key]
    return name, {}


def extract_tool_calls(text: str, known: Collection[str]) -> tuple[list[ToolCall], str]:
    """Return (tool calls found in the text, the text with those JSON snippets removed)."""
    if not text or "{" not in text:
        return [], text

    decoder = json.JSONDecoder()
    calls: list[ToolCall] = []
    kept: list[str] = []
    position = 0
    while (start := text.find("{", position)) != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            kept.append(text[position : start + 1])
            position = start + 1
            continue
        found = _as_call(obj, known)
        kept.append(text[position:start])
        if found:
            name, arguments = found
            calls.append(ToolCall(id=f"text_call_{len(calls) + 1}", name=name, arguments=arguments, from_text=True))
        else:
            kept.append(text[start:end])
        position = end
    kept.append(text[position:])

    if not calls:
        return [], text
    remaining = _WRAPPERS.sub("", "".join(kept)).strip()
    return calls, remaining
