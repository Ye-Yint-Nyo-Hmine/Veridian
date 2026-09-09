"""memory/vector — SQLite-persisted memory with embedding search via the model_provider contract."""

from __future__ import annotations

import array
import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokens(t: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(t)}


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryVector(Brick):
    name = "memory/vector"
    version = "0.1.0"
    implements = {"memory": ["write", "read", "search", "forget"]}

    async def on_initialize(self) -> bool:
        self.embed_model = self.config.get("embed_model", "text-embedding-3-small")
        root = Path(self.config.get("root") or self.workspace_root).resolve()
        if self.config.get("in_memory"):
            self.db = sqlite3.connect(":memory:")
        else:
            d = root / ".veridian"
            d.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(d / "memory-vector.sqlite"))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS mem("
            "id TEXT PRIMARY KEY, content TEXT, tags TEXT, metadata TEXT, created_at TEXT, embedding BLOB)"
        )
        self.db.commit()
        return True

    async def _embed(self, texts):
        try:
            res = await self.host.contract_call(
                "model_provider", "embed", {"input": texts, "model": self.embed_model}
            )
            return res["embeddings"]
        except Exception:  # noqa: BLE001
            return None

    def _row_to_record(self, row) -> dict:
        return {
            "id": row[0],
            "content": row[1],
            "tags": json.loads(row[2]),
            "metadata": json.loads(row[3]),
            "created_at": row[4],
        }

    @rpc("memory.write")
    async def write(self, params, ctx):
        rec_id = params.get("key") or uuid.uuid4().hex
        emb = await self._embed([params["content"]])
        blob = array.array("f", emb[0]).tobytes() if emb else None
        self.db.execute(
            "INSERT OR REPLACE INTO mem VALUES (?,?,?,?,?,?)",
            (
                rec_id,
                params["content"],
                json.dumps(list(params.get("tags", []))),
                json.dumps(dict(params.get("metadata", {}))),
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                blob,
            ),
        )
        self.db.commit()
        return {"id": rec_id}

    @rpc("memory.read")
    async def read(self, params, ctx):
        row = self.db.execute("SELECT * FROM mem WHERE id=?", (params["id"],)).fetchone()
        return {"record": self._row_to_record(row) if row else None}

    @rpc("memory.search")
    async def search(self, params, ctx):
        want = set(params.get("tags", []))
        k = int(params.get("k", 10))
        rows = self.db.execute("SELECT * FROM mem").fetchall()
        rows = [r for r in rows if not want or want.issubset(set(json.loads(r[2])))]

        qemb = await self._embed([params["query"]])
        scored: list[tuple[float, tuple]] = []
        if qemb:
            qv = qemb[0]
            for r in rows:
                if r[5] is None:
                    continue
                scored.append((_cosine(qv, list(array.array("f", r[5]))), r))
        if not scored:
            qt = _tokens(params["query"])
            for r in rows:
                overlap = len(qt & _tokens(r[1]))
                if overlap or not qt:
                    scored.append((overlap / (len(qt) or 1), r))
        scored.sort(key=lambda s: s[0], reverse=True)
        return {
            "results": [{**self._row_to_record(r), "score": round(float(s), 4)} for s, r in scored[:k]]
        }

    @rpc("memory.forget")
    async def forget(self, params, ctx):
        if params.get("id"):
            cur = self.db.execute("DELETE FROM mem WHERE id=?", (params["id"],))
            self.db.commit()
            return {"forgotten": cur.rowcount}
        tags = set(params.get("tags", []))
        if not tags:
            n = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
            self.db.execute("DELETE FROM mem")
            self.db.commit()
            return {"forgotten": n}
        rows = self.db.execute("SELECT id, tags FROM mem").fetchall()
        drop = [rid for rid, t in rows if tags & set(json.loads(t))]
        self.db.executemany("DELETE FROM mem WHERE id=?", [(i,) for i in drop])
        self.db.commit()
        return {"forgotten": len(drop)}


if __name__ == "__main__":
    run(MemoryVector())
