"""Which inference provider can a ``live``-marked test actually reach right now?

Milestone 2 / A5. The live tests used to gate purely on cloud API keys, so a contributor with a
local model server running (Ollama, llama.cpp, LM Studio, vLLM — all OpenAI-compatible) executed
*zero* real inference. This module probes for a usable provider, **preferring a local server**,
and hands each live test a ready-made ``[bindings]`` line so the same test body runs against
whatever is available.

Nothing here is imported by the kernel; it lives under ``tests/`` on purpose.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

import pytest

_LOCAL_BASE = (
    os.environ.get("VERIDIAN_LOCAL_BASE_URL")
    or os.environ.get("OPENAI_BASE_URL")
    or "http://localhost:11434/v1"
).rstrip("/")


def _get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _local_model() -> str | None:
    """The model id a live test should use against a reachable local server, or ``None``.

    An explicit ``VERIDIAN_LOCAL_MODEL`` always wins. Otherwise: Ollama's native ``/api/tags``
    carries an on-disk ``size`` per model, so the **smallest** model is chosen — a live test wants
    the fastest thing that answers, not whatever happens to sort first (that could be a 30B). A
    generic OpenAI-compatible server exposes only ``/v1/models``, so there we take the first id.
    """
    override = os.environ.get("VERIDIAN_LOCAL_MODEL")
    if override:
        return override

    root = _LOCAL_BASE[:-3] if _LOCAL_BASE.endswith("/v1") else _LOCAL_BASE
    tags = _get_json(root + "/api/tags")
    if tags and isinstance(tags.get("models"), list) and tags["models"]:
        sized = [m for m in tags["models"] if m.get("name") and isinstance(m.get("size"), int)]
        if sized:
            return min(sized, key=lambda m: m["size"])["name"]
        return tags["models"][0].get("name")

    data = _get_json(_LOCAL_BASE + "/models")
    if not data:
        return None
    ids = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    return ids[0] if ids else None


@dataclass(frozen=True)
class LiveProvider:
    name: str  # "local" | "anthropic" | "openai"
    binding: str  # a full `inference = ...` line for a stack's [bindings] table
    model: str  # "" when the brick's own default is fine


def _detect() -> LiveProvider | None:
    model = _local_model()
    if model:
        return LiveProvider(
            name="local",
            binding=(
                'inference = { brick = "bricks/inference/local", '
                f'config = {{ model = "{model}", max_tokens = 512 }} }}'
            ),
            model=model,
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        return LiveProvider(
            name="anthropic",
            binding='inference = "bricks/inference/anthropic"',
            model="",
        )
    if os.environ.get("OPENAI_API_KEY"):
        return LiveProvider(
            name="openai",
            binding='inference = "bricks/inference/openai"',
            model="",
        )
    return None


PROVIDER: LiveProvider | None = _detect()
LOCAL_MODEL: str | None = _local_model()

requires_live_provider = pytest.mark.skipif(
    PROVIDER is None,
    reason="no live inference provider reachable (no local server, no ANTHROPIC_API_KEY, no OPENAI_API_KEY)",
)
requires_local_server = pytest.mark.skipif(
    LOCAL_MODEL is None,
    reason=f"no OpenAI-compatible local server reachable at {_LOCAL_BASE}",
)
