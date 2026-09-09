"""Pydantic models for the shapes that get built and passed around most often.

These are a convenience for brick and SDK authors. They are *validated against*
``schemas/protocol/common.schema.json`` by the contract test suite; they do not define the wire
format. When in doubt, the schema wins.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextBlock(_Strict):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(_Strict):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict


class ToolResultBlock(_Strict):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool | None = None


ContentBlock = Annotated[
    Union[TextBlock, ToolUseBlock, ToolResultBlock],
    Field(discriminator="type"),
]

Role = Literal["system", "user", "assistant", "tool"]


class Message(_Strict):
    role: Role
    content: str | list[ContentBlock]

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


class Usage(_Strict):
    model_config = ConfigDict(extra="allow")
    input_tokens: int = 0
    output_tokens: int = 0


class ToolSchema(_Strict):
    name: str
    description: str
    input_schema: dict


StopReason = Literal["stop", "length", "tool_use", "content_filter", "error"]


def to_wire(model: BaseModel) -> dict:
    """Dump a model to a JSON-safe dict with defaults and Nones dropped where the schema allows."""
    return model.model_dump(mode="json", exclude_none=True)
