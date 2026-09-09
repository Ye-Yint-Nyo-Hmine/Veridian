"""context/default — glob + keyword-overlap retrieval, no model required."""

from __future__ import annotations

import re
import time
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_IGNORE = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
_TEXT_EXT = {".py", ".ts", ".js", ".tsx", ".jsx", ".md", ".txt", ".toml", ".json", ".yaml", ".yml",
             ".cfg", ".ini", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".sh"}
_CHUNK_LINES = 40
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text)}


class ContextDefault(Brick):
    name = "context/default"
    version = "0.1.0"
    implements = {"context": ["index", "retrieve", "invalidate"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        self.chunk_lines = int(self.config.get("chunk_lines", _CHUNK_LINES))
        self._chunks: list[dict] = []
        self._by_path: dict[str, list[int]] = {}
        return True

    def _iter_files(self, paths: list[str] | None):
        if paths:
            for rel in paths:
                p = (self.root / rel).resolve()
                if p.is_file():
                    yield p
            return
        stack = [self.root]
        while stack:
            cur = stack.pop()
            for entry in sorted(cur.iterdir()):
                if entry.name in _IGNORE:
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.suffix.lower() in _TEXT_EXT:
                    yield entry

    def _index_file(self, path: Path) -> int:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            return 0
        rel = path.relative_to(self.root).as_posix()
        self._drop(rel)
        idxs: list[int] = []
        for start in range(0, max(1, len(lines)), self.chunk_lines):
            body = "\n".join(lines[start : start + self.chunk_lines])
            if not body.strip():
                continue
            chunk = {
                "path": rel,
                "span": [start + 1, min(len(lines), start + self.chunk_lines)],
                "text": body,
                "tokens": _tokens(body) | _tokens(rel),
            }
            idxs.append(len(self._chunks))
            self._chunks.append(chunk)
        self._by_path[rel] = idxs
        return len(idxs)

    def _drop(self, rel: str) -> None:
        old = set(self._by_path.pop(rel, []))
        if not old:
            return
        self._chunks = [c for i, c in enumerate(self._chunks) if i not in old]
        self._by_path = {}
        for i, c in enumerate(self._chunks):
            self._by_path.setdefault(c["path"], []).append(i)

    @rpc("context.index")
    async def index(self, params, ctx):
        t0 = time.perf_counter()
        n = sum(self._index_file(p) for p in self._iter_files(params.get("paths")))
        return {"indexed": n, "took_ms": (time.perf_counter() - t0) * 1000}

    @rpc("context.retrieve")
    async def retrieve(self, params, ctx):
        if not self._chunks:
            await self.index({}, ctx)
        q = _tokens(params["query"])
        k = int(params.get("k", 6))
        scored = []
        for c in self._chunks:
            overlap = len(q & c["tokens"])
            if overlap:
                scored.append((overlap / (len(q) or 1), c))
        scored.sort(key=lambda s: s[0], reverse=True)
        return {
            "chunks": [
                {"path": c["path"], "span": c["span"], "text": c["text"], "score": round(score, 4)}
                for score, c in scored[:k]
            ]
        }

    @rpc("context.invalidate")
    async def invalidate(self, params, ctx):
        paths = params.get("paths")
        if not paths:
            n = len(self._by_path)
            self._chunks.clear()
            self._by_path.clear()
            return {"invalidated": n}
        for rel in paths:
            self._drop(Path(rel).as_posix())
        for p in self._iter_files(paths):
            self._index_file(p)
        return {"invalidated": len(paths)}


if __name__ == "__main__":
    run(ContextDefault())
