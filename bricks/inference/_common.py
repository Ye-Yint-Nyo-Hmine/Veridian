"""Shared translation between the Veridian ``inference`` wire shapes and provider SDKs.

Loaded by the inference bricks via ``sys.path`` (the same trick the echo fixtures use), so the
bricks stay non-importable directories while still sharing this code. Nothing here is imported by
the kernel.
"""

from __future__ import annotations

import json
from typing import Any

# ---------- wire helpers ----------------------------------------------------------


def blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Pull ``system`` role messages out into a single string (Anthropic-style)."""
    system_parts = [blocks_to_text(m["content"]) for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    return "\n\n".join(p for p in system_parts if p), rest


# ---------- OpenAI chat.completions ---------------------------------------------


def to_openai_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = blocks_to_text(content)
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            msg: dict = {"role": "assistant", "content": text or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        elif any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append(
                        {"role": "tool", "tool_call_id": b["tool_use_id"], "content": b.get("content", "")}
                    )
        else:
            out.append({"role": role, "content": blocks_to_text(content)})
    return out


def to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
        }
        for t in tools
    ]


_OPENAI_STOP = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "content_filter": "content_filter",
    "function_call": "tool_use",
}


def from_openai_choice(choice: Any, model: str, usage: Any) -> dict:
    msg = choice.message
    content: list[dict] = []
    if msg.content:
        content.append({"type": "text", "text": msg.content})
    for tc in msg.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {"_raw": tc.function.arguments}
        content.append({"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": args})
    if not content:
        content.append({"type": "text", "text": ""})
    return {
        "message": {"role": "assistant", "content": content},
        "model": model,
        "usage": {
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        },
        "stop_reason": _OPENAI_STOP.get(choice.finish_reason or "stop", "stop"),
    }


# ---------- Anthropic Messages ------------------------------------------------------


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":
            continue
        if role == "tool":
            blocks = content if isinstance(content, list) else [{"type": "tool_result", "content": content}]
            out.append({"role": "user", "content": blocks})
            continue
        if isinstance(content, str):
            out.append({"role": role, "content": content})
        else:
            out.append({"role": role, "content": content})
    return out


def to_anthropic_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools
    ]


_ANTHROPIC_STOP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_use",
    "refusal": "content_filter",
}


def from_anthropic_message(msg: Any) -> dict:
    content: list[dict] = []
    for block in msg.content:
        bt = getattr(block, "type", None)
        if bt == "text":
            content.append({"type": "text", "text": block.text})
        elif bt == "tool_use":
            content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    if not content:
        content.append({"type": "text", "text": ""})
    return {
        "message": {"role": "assistant", "content": content},
        "model": msg.model,
        "usage": {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
        "stop_reason": _ANTHROPIC_STOP.get(msg.stop_reason or "end_turn", "stop"),
    }
