"""memory/ephemeral — in-process memory, gone when the brick stops."""

from __future__ import annotations

import re
import time
import uuid

from veridian.sdk import Brick, rpc, run

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokens(t: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(t)}


class MemoryEphemeral(Brick):
    name = "memory/ephemeral"
    version = "0.1.0"
    implements = {"memory": ["write", "read", "search", "forget"]}

    async def on_initialize(self) -> bool:
        self._store: dict[str, dict] = {}
        return True

    @rpc("memory.write")
    async def write(self, params, ctx):
        rec_id = params.get("key") or uuid.uuid4().hex
        self._store[rec_id] = {
            "id": rec_id,
            "content": params["content"],
            "tags": list(params.get("tags", [])),
            "metadata": dict(params.get("metadata", {})),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return {"id": rec_id}

    @rpc("memory.read")
    async def read(self, params, ctx):
        return {"record": self._store.get(params["id"])}

    @rpc("memory.search")
    async def search(self, params, ctx):
        q = _tokens(params["query"])
        want = set(params.get("tags", []))
        k = int(params.get("k", 10))
        scored = []
        for rec in self._store.values():
            if want and not want.issubset(set(rec["tags"])):
                continue
            overlap = len(q & _tokens(rec["content"]))
            if overlap or not q:
                scored.append((overlap / (len(q) or 1), rec))
        scored.sort(key=lambda s: s[0], reverse=True)
        return {
            "results": [
                {**rec, "score": round(float(s), 4)} for s, rec in scored[:k]
            ]
        }

    @rpc("memory.forget")
    async def forget(self, params, ctx):
        if params.get("id"):
            return {"forgotten": 1 if self._store.pop(params["id"], None) else 0}
        tags = set(params.get("tags", []))
        if not tags:
            n = len(self._store)
            self._store.clear()
            return {"forgotten": n}
        drop = [i for i, r in self._store.items() if tags & set(r["tags"])]
        for i in drop:
            del self._store[i]
        return {"forgotten": len(drop)}


if __name__ == "__main__":
    run(MemoryEphemeral())
