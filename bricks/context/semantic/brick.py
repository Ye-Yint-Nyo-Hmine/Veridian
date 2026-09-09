"""context/semantic — cheap keyword recall, then an inference call reranks the shortlist.

If no inference brick is reachable, the keyword ranking is returned unchanged.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_IGNORE = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
_TEXT_EXT = {".py", ".ts", ".js", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".rs", ".go"}
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CHUNK_LINES = 40


def _tokens(t: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(t)}


class ContextSemantic(Brick):
    name = "context/semantic"
    version = "0.1.0"
    implements = {"context": ["index", "retrieve", "invalidate"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        self.rerank_model = self.config.get("rerank_model")
        self.chunk_lines = int(self.config.get("chunk_lines", _CHUNK_LINES))
        self._chunks: list[dict] = []
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

    def _reindex(self, paths):
        if not paths:
            self._chunks.clear()
        for path in self._iter_files(paths):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(self.root).as_posix()
            self._chunks = [c for c in self._chunks if c["path"] != rel]
            for start in range(0, max(1, len(lines)), self.chunk_lines):
                body = "\n".join(lines[start : start + self.chunk_lines])
                if body.strip():
                    self._chunks.append(
                        {"path": rel, "span": [start + 1, min(len(lines), start + self.chunk_lines)],
                         "text": body, "tokens": _tokens(body) | _tokens(rel)}
                    )

    @rpc("context.index")
    async def index(self, params, ctx):
        t0 = time.perf_counter()
        self._reindex(params.get("paths"))
        return {"indexed": len(self._chunks), "took_ms": (time.perf_counter() - t0) * 1000}

    def _keyword_rank(self, query: str) -> list[tuple[float, dict]]:
        q = _tokens(query)
        out = [(len(q & c["tokens"]) / (len(q) or 1), c) for c in self._chunks]
        out = [(s, c) for s, c in out if s > 0]
        out.sort(key=lambda s: s[0], reverse=True)
        return out

    @rpc("context.retrieve")
    async def retrieve(self, params, ctx):
        if not self._chunks:
            self._reindex(None)
        k = int(params.get("k", 6))
        ranked = self._keyword_rank(params["query"])[: max(k * 3, 12)]
        if not ranked:
            return {"chunks": []}

        chosen = ranked[:k]
        try:
            listing = "\n".join(
                f"[{i}] {c['path']}:{c['span'][0]}\n{c['text'][:500]}" for i, (_, c) in enumerate(ranked)
            )
            prompt = (
                f"Query: {params['query']}\n\nCandidate snippets:\n{listing}\n\n"
                f"Return a JSON array of the {k} snippet indices most relevant to the query, "
                f"best first. JSON only."
            )
            gen_params = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 200}
            if self.rerank_model:
                gen_params["model"] = self.rerank_model
            res = await self.host.contract_call("inference", "generate", gen_params)
            text = res["message"]["content"]
            if isinstance(text, list):
                text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
            order = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
            picked = [ranked[i][1] for i in order if isinstance(i, int) and 0 <= i < len(ranked)][:k]
            if picked:
                chosen = [(1.0 - n / len(picked), c) for n, c in enumerate(picked)]
        except Exception as exc:  # noqa: BLE001
            await self.host.log(f"rerank unavailable, using keyword order: {exc}", level="warning")

        return {
            "chunks": [
                {"path": c["path"], "span": c["span"], "text": c["text"], "score": round(float(s), 4)}
                for s, c in chosen
            ]
        }

    @rpc("context.invalidate")
    async def invalidate(self, params, ctx):
        paths = params.get("paths")
        n = len({c["path"] for c in self._chunks}) if not paths else len(paths)
        self._reindex(paths)
        return {"invalidated": n}


if __name__ == "__main__":
    run(ContextSemantic())
