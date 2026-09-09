"""example/headline-context — a deliberately tiny context strategy.

It indexes only "headline" lines (function/class defs, markdown headings, assignments) and ranks
by substring match. Nothing like production retrieval — the point is that swapping it in is one
line in a stack file and the kernel does not change.

    [bindings]
    context = "examples/custom-context"
"""

from __future__ import annotations

import time
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_IGNORE = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
_EXT = {".py", ".ts", ".js", ".md", ".rs", ".go", ".java"}


def _is_headline(line: str) -> bool:
    s = line.strip()
    return s.startswith(("def ", "class ", "async def ", "export ", "function ", "#", "//")) or (
        "=" in s and not s.startswith((" ", "\t")) and len(s) < 100
    )


class HeadlineContext(Brick):
    name = "example/headline-context"
    version = "0.1.0"
    implements = {"context": ["index", "retrieve", "invalidate"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.workspace_root).resolve()
        self._lines: list[dict] = []
        return True

    def _scan(self):
        self._lines.clear()
        stack = [self.root]
        while stack:
            cur = stack.pop()
            for e in sorted(cur.iterdir()):
                if e.name in _IGNORE:
                    continue
                if e.is_dir():
                    stack.append(e)
                elif e.suffix in _EXT:
                    try:
                        for n, line in enumerate(e.read_text(encoding="utf-8").splitlines(), 1):
                            if _is_headline(line):
                                self._lines.append(
                                    {"path": e.relative_to(self.root).as_posix(), "n": n, "text": line.strip()}
                                )
                    except (UnicodeDecodeError, OSError):
                        continue

    @rpc("context.index")
    async def index(self, params, ctx):
        t0 = time.perf_counter()
        self._scan()
        return {"indexed": len(self._lines), "took_ms": (time.perf_counter() - t0) * 1000}

    @rpc("context.retrieve")
    async def retrieve(self, params, ctx):
        if not self._lines:
            self._scan()
        q = params["query"].lower()
        terms = [t for t in q.replace("_", " ").split() if t]
        k = int(params.get("k", 6))
        scored = []
        for item in self._lines:
            hay = (item["text"] + " " + item["path"]).lower()
            score = sum(1 for t in terms if t in hay)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda s: s[0], reverse=True)
        return {
            "chunks": [
                {"path": it["path"], "span": [it["n"], it["n"]], "text": it["text"], "score": float(s)}
                for s, it in scored[:k]
            ]
        }

    @rpc("context.invalidate")
    async def invalidate(self, params, ctx):
        n = len({x["path"] for x in self._lines})
        self._scan()
        return {"invalidated": n}


if __name__ == "__main__":
    run(HeadlineContext())
