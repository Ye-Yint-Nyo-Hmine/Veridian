"""inference/local — the same OpenAI-compatible adapter as inference/openai, pointed at a local
server. Ollama, llama.cpp, vLLM, and LM Studio all speak the OpenAI chat-completions API; only the
base URL differs.

This is deliberately thin: it loads the openai brick's implementation and changes the defaults.
"""

from __future__ import annotations

import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))          # bricks/inference  (for _common)
sys.path.insert(0, str(_HERE.parent.parent / "openai"))  # reuse the openai implementation

import brick as openai_brick  # noqa: E402  (bricks/inference/openai/brick.py)

from veridian.sdk import run  # noqa: E402

_DEFAULT_BASE_URL = "http://localhost:11434/v1"  # Ollama's default
_DEFAULT_MODEL = "llama3.2"


class LocalInference(openai_brick.OpenAIInference):
    name = "inference/local"
    version = "0.1.0"

    async def on_initialize(self) -> bool:
        await super().on_initialize()
        self.base_url = (
            self.config.get("base_url")
            or os.environ.get("VERIDIAN_LOCAL_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or _DEFAULT_BASE_URL
        )
        self.model = self.config.get("model", _DEFAULT_MODEL)
        self.embed_model = self.config.get("embed_model", "nomic-embed-text")
        return True


if __name__ == "__main__":
    run(LocalInference())
