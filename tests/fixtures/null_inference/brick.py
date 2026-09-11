"""Planted adapter for the Bug 1 regression.

A small local model — a reasoning model that spent its entire token budget on hidden thinking,
say — returns an assistant turn whose ``content`` is ``null`` on the wire, or a text block whose
``text`` is ``null``. The orchestrator used to build its reply text with
``"".join(b.get("text", "") ...)``, which yields that ``None`` (the key is *present*, so the
default never applies) and the run dies with a ``TypeError``.

``mode`` (from binding config) picks which malformed shape to send:

* ``"null_content"``  — ``{"role": "assistant", "content": null}``
* ``"null_text"``     — ``{"role": "assistant", "content": [{"type": "text", "text": null}]}``
* ``"then_ok"``       — the null shape for the first N calls, then a normal reply, so the close-out
                        path runs too
"""

from __future__ import annotations

from veridian.sdk import Brick, rpc, run


class NullInference(Brick):
    name = "fixtures/null-inference"
    version = "0.0.1"
    implements = {"inference": ["generate", "models"]}

    async def on_initialize(self) -> bool:
        self.mode = self.config.get("mode", "null_content")
        self.ok_after = int(self.config.get("ok_after", 1))
        self._calls = 0
        return True

    def _malformed(self) -> dict:
        if self.mode == "null_text":
            return {"role": "assistant", "content": [{"type": "text", "text": None}]}
        return {"role": "assistant", "content": None}

    @rpc("inference.generate")
    async def generate(self, params, ctx):
        self._calls += 1
        if self.mode == "then_ok" and self._calls > self.ok_after:
            message = {"role": "assistant", "content": [{"type": "text", "text": "All done."}]}
            stop = "stop"
        else:
            message = self._malformed()
            stop = "length"
        return {
            "message": message,
            "model": "null-model",
            "usage": {"input_tokens": 5, "output_tokens": 0},
            "stop_reason": stop,
        }

    @rpc("inference.models")
    async def models(self, params, ctx):
        return {"models": [{"id": "null-model"}]}


if __name__ == "__main__":
    run(NullInference())
