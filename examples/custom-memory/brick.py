"""example/jsonl-memory — memory as a single append-mostly JSONL file under .veridian/.

Not clever: linear scan, substring search. It exists to show that a stack can bind something as
plain as "a text file" to the ``memory`` contract without the kernel or the orchestrator noticing.

    [bindings]
    memory = "examples/custom-memory"
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


class JsonlMemory(Brick):
    name = "example/jsonl-memory"
    version = "0.1.0"
    implements = {"memory": ["write", "read", "search", "forget"]}

    async def on_initialize(self) -> bool:
        d = Path(self.workspace_root).resolve() / ".veridian"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "memory.jsonl"
        self.path.touch(exist_ok=True)
        return True

    def _all(self) -> list[dict]:
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def _rewrite(self, records: list[dict]) -> None:
        self.path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""), encoding="utf-8")

    @rpc("memory.write")
    async def write(self, params, ctx):
        rec = {
            "id": params.get("key") or uuid.uuid4().hex,
            "content": params["content"],
            "tags": list(params.get("tags", [])),
            "metadata": dict(params.get("metadata", {})),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        records = [r for r in self._all() if r["id"] != rec["id"]]
        records.append(rec)
        self._rewrite(records)
        return {"id": rec["id"]}

    @rpc("memory.read")
    async def read(self, params, ctx):
        return {"record": next((r for r in self._all() if r["id"] == params["id"]), None)}

    @rpc("memory.search")
    async def search(self, params, ctx):
        q = {m.group(0).lower() for m in _WORD.finditer(params["query"])}
        want = set(params.get("tags", []))
        k = int(params.get("k", 10))
        scored = []
        for r in self._all():
            if want and not want.issubset(set(r["tags"])):
                continue
            toks = {m.group(0).lower() for m in _WORD.finditer(r["content"])}
            overlap = len(q & toks)
            if overlap or not q:
                scored.append((overlap / (len(q) or 1), r))
        scored.sort(key=lambda s: s[0], reverse=True)
        return {"results": [{**r, "score": round(float(s), 4)} for s, r in scored[:k]]}

    @rpc("memory.forget")
    async def forget(self, params, ctx):
        records = self._all()
        if params.get("id"):
            kept = [r for r in records if r["id"] != params["id"]]
            self._rewrite(kept)
            return {"forgotten": len(records) - len(kept)}
        tags = set(params.get("tags", []))
        if not tags:
            self._rewrite([])
            return {"forgotten": len(records)}
        kept = [r for r in records if not tags & set(r["tags"])]
        self._rewrite(kept)
        return {"forgotten": len(records) - len(kept)}


if __name__ == "__main__":
    run(JsonlMemory())
