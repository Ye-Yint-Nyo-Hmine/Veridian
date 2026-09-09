"""context/vector — cosine retrieval over embeddings from the model_provider contract.

Embeddings come from whatever brick is bound to ``model_provider`` (``host.contract.call``). Vectors
are stored in SQLite. If no provider is reachable, indexing still records the chunks and retrieval
falls back to keyword overlap — an honest degradation, not a fake model.
"""

from __future__ import annotations

import array
import math
import re
import sqlite3
import time
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_IGNORE = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
_TEXT_EXT = {".py", ".ts", ".js", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".rs", ".go"}
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CHUNK_LINES = 40


def _tokens(t: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(t)}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class ContextVector(Brick):
    name = "context/vector"
    version = "0.1.0"
    implements = {"context": ["index", "retrieve", "invalidate"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        self.embed_model = self.config.get("embed_model", "text-embedding-3-small")
        self.chunk_lines = int(self.config.get("chunk_lines", _CHUNK_LINES))
        if self.config.get("in_memory"):
            self.db = sqlite3.connect(":memory:")
        else:
            dbdir = self.root / ".veridian"
            dbdir.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(dbdir / "context-vector.sqlite"))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS chunks("
            "path TEXT, span_start INT, span_end INT, text TEXT, embedding BLOB)"
        )
        self.db.commit()
        return True

    def _iter_files(self, paths):
        if paths:
            for rel in paths:
                p = (self.root / rel).resolve()
                if p.is_file():
                    yield p
            return
        stack = [self.root]
        while stack:
            cur = stack.pop()
            for e in sorted(cur.iterdir()):
                if e.name in _IGNORE:
                    continue
                if e.is_dir():
                    stack.append(e)
                elif e.suffix.lower() in _TEXT_EXT:
                    yield e

    async def _embed(self, texts: list[str]) -> list[list[float]] | None:
        try:
            res = await self.host.contract_call(
                "model_provider", "embed", {"input": texts, "model": self.embed_model}
            )
            return res["embeddings"]
        except Exception as exc:  # noqa: BLE001
            await self.host.log(f"embed unavailable, degrading to keyword: {exc}", level="warning")
            return None

    @rpc("context.index")
    async def index(self, params, ctx):
        t0 = time.perf_counter()
        rows: list[tuple] = []
        pending_texts: list[str] = []
        pending_meta: list[tuple] = []
        for path in self._iter_files(params.get("paths")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(self.root).as_posix()
            self.db.execute("DELETE FROM chunks WHERE path=?", (rel,))
            for start in range(0, max(1, len(lines)), self.chunk_lines):
                body = "\n".join(lines[start : start + self.chunk_lines])
                if not body.strip():
                    continue
                pending_texts.append(body)
                pending_meta.append((rel, start + 1, min(len(lines), start + self.chunk_lines), body))

        embeddings = await self._embed(pending_texts) if pending_texts else []
        for i, meta in enumerate(pending_meta):
            emb = None
            if embeddings and i < len(embeddings):
                emb = array.array("f", embeddings[i]).tobytes()
            rows.append((*meta, emb))
        self.db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?)", rows)
        self.db.commit()
        return {"indexed": len(rows), "took_ms": (time.perf_counter() - t0) * 1000}

    @rpc("context.retrieve")
    async def retrieve(self, params, ctx):
        if self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0:
            await self.index({}, ctx)
        k = int(params.get("k", 6))
        query = params["query"]
        all_rows = self.db.execute("SELECT path, span_start, span_end, text, embedding FROM chunks").fetchall()

        qemb = await self._embed([query])
        scored: list[tuple[float, tuple]] = []
        if qemb:
            qv = qemb[0]
            for row in all_rows:
                if row[4] is None:
                    continue
                v = list(array.array("f", row[4]))
                scored.append((_cosine(qv, v), row))
        if not scored:  # keyword fallback
            qt = _tokens(query)
            for row in all_rows:
                overlap = len(qt & _tokens(row[3]))
                if overlap:
                    scored.append((overlap / (len(qt) or 1), row))

        scored.sort(key=lambda s: s[0], reverse=True)
        return {
            "chunks": [
                {"path": r[0], "span": [r[1], r[2]], "text": r[3], "score": round(float(s), 4)}
                for s, r in scored[:k]
            ]
        }

    @rpc("context.invalidate")
    async def invalidate(self, params, ctx):
        paths = params.get("paths")
        if not paths:
            n = self.db.execute("SELECT COUNT(DISTINCT path) FROM chunks").fetchone()[0]
            self.db.execute("DELETE FROM chunks")
            self.db.commit()
            return {"invalidated": n}
        for rel in paths:
            self.db.execute("DELETE FROM chunks WHERE path=?", (Path(rel).as_posix(),))
        self.db.commit()
        await self.index({"paths": paths}, ctx)
        return {"invalidated": len(paths)}


if __name__ == "__main__":
    run(ContextVector())
