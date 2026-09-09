"""memory/graph — SQLite memory with an explicit edge table.

``write`` may carry ``metadata.links`` (a list of existing record ids); each becomes an edge.
``search`` returns keyword matches, then pulls their one-hop neighbours in at a discounted score.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokens(t: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(t)}


class MemoryGraph(Brick):
    name = "memory/graph"
    version = "0.1.0"
    implements = {"memory": ["write", "read", "search", "forget"]}

    async def on_initialize(self) -> bool:
        root = Path(self.config.get("root") or self.workspace_root).resolve()
        if self.config.get("in_memory"):
            self.db = sqlite3.connect(":memory:")
        else:
            d = root / ".veridian"
            d.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(d / "memory-graph.sqlite"))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS node("
            "id TEXT PRIMARY KEY, content TEXT, tags TEXT, metadata TEXT, created_at TEXT)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS edge(src TEXT, dst TEXT)")
        self.db.commit()
        return True

    def _record(self, row) -> dict:
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
        metadata = dict(params.get("metadata", {}))
        links = metadata.get("links", [])
        self.db.execute(
            "INSERT OR REPLACE INTO node VALUES (?,?,?,?,?)",
            (
                rec_id,
                params["content"],
                json.dumps(list(params.get("tags", []))),
                json.dumps(metadata),
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )
        self.db.execute("DELETE FROM edge WHERE src=?", (rec_id,))
        for dst in links:
            self.db.execute("INSERT INTO edge VALUES (?,?)", (rec_id, dst))
        self.db.commit()
        return {"id": rec_id}

    @rpc("memory.read")
    async def read(self, params, ctx):
        row = self.db.execute("SELECT * FROM node WHERE id=?", (params["id"],)).fetchone()
        return {"record": self._record(row) if row else None}

    @rpc("memory.search")
    async def search(self, params, ctx):
        q = _tokens(params["query"])
        want = set(params.get("tags", []))
        k = int(params.get("k", 10))
        rows = self.db.execute("SELECT * FROM node").fetchall()
        by_id = {r[0]: r for r in rows}

        direct: dict[str, tuple[float, tuple]] = {}
        for r in rows:
            if want and not want.issubset(set(json.loads(r[2]))):
                continue
            overlap = len(q & _tokens(r[1]))
            if overlap or not q:
                direct[r[0]] = (overlap / (len(q) or 1), r)

        results = dict(direct)
        for rid in list(direct):
            neigh = [e[1] for e in self.db.execute("SELECT * FROM edge WHERE src=?", (rid,))]
            neigh += [e[0] for e in self.db.execute("SELECT * FROM edge WHERE dst=?", (rid,))]
            for nid in neigh:
                if nid in by_id and nid not in results:
                    results[nid] = (direct[rid][0] * 0.5, by_id[nid])

        ordered = sorted(results.values(), key=lambda s: s[0], reverse=True)[:k]
        return {"results": [{**self._record(r), "score": round(float(s), 4)} for s, r in ordered]}

    @rpc("memory.forget")
    async def forget(self, params, ctx):
        if params.get("id"):
            cur = self.db.execute("DELETE FROM node WHERE id=?", (params["id"],))
            self.db.execute("DELETE FROM edge WHERE src=? OR dst=?", (params["id"], params["id"]))
            self.db.commit()
            return {"forgotten": cur.rowcount}
        tags = set(params.get("tags", []))
        if not tags:
            n = self.db.execute("SELECT COUNT(*) FROM node").fetchone()[0]
            self.db.execute("DELETE FROM node")
            self.db.execute("DELETE FROM edge")
            self.db.commit()
            return {"forgotten": n}
        rows = self.db.execute("SELECT id, tags FROM node").fetchall()
        drop = [rid for rid, t in rows if tags & set(json.loads(t))]
        for rid in drop:
            self.db.execute("DELETE FROM node WHERE id=?", (rid,))
            self.db.execute("DELETE FROM edge WHERE src=? OR dst=?", (rid, rid))
        self.db.commit()
        return {"forgotten": len(drop)}


if __name__ == "__main__":
    run(MemoryGraph())
