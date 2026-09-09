"""context/graph — an import + definition graph over Python sources via the stdlib ``ast`` module.

Retrieval ranks a definition by how well its name matches the query, then pulls in files that
import the file a match lives in, so a query about a symbol also surfaces its call sites.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

from veridian.sdk import Brick, rpc, run

_IGNORE = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}


class ContextGraph(Brick):
    name = "context/graph"
    version = "0.1.0"
    implements = {"context": ["index", "retrieve", "invalidate"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        self._defs: dict[str, list[dict]] = {}          # rel path -> [{name, kind, lineno, text}]
        self._imports: dict[str, set[str]] = {}         # rel path -> {imported module dotted}
        self._importers: dict[str, set[str]] = {}       # module stem -> {rel paths that import it}
        return True

    def _iter_py(self, paths: list[str] | None):
        if paths:
            for rel in paths:
                p = (self.root / rel).resolve()
                if p.is_file() and p.suffix == ".py":
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
                elif e.suffix == ".py":
                    yield e

    def _index_file(self, path: Path) -> int:
        rel = path.relative_to(self.root).as_posix()
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError, OSError):
            return 0
        lines = src.splitlines()
        defs: list[dict] = []
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                snippet = "\n".join(lines[node.lineno - 1 : min(end, node.lineno + 20)])
                defs.append(
                    {
                        "name": node.name,
                        "kind": type(node).__name__.replace("Def", "").lower(),
                        "lineno": node.lineno,
                        "endno": end,
                        "text": snippet,
                    }
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        self._defs[rel] = defs
        self._imports[rel] = imports
        for mod in imports:
            self._importers.setdefault(mod.split(".")[-1], set()).add(rel)
        return len(defs)

    @rpc("context.index")
    async def index(self, params, ctx):
        t0 = time.perf_counter()
        if not params.get("paths"):
            self._defs.clear()
            self._imports.clear()
            self._importers.clear()
        n = sum(self._index_file(p) for p in self._iter_py(params.get("paths")))
        return {"indexed": n, "took_ms": (time.perf_counter() - t0) * 1000}

    @rpc("context.retrieve")
    async def retrieve(self, params, ctx):
        if not self._defs:
            await self.index({}, ctx)
        query = params["query"].lower()
        terms = [t for t in query.replace("_", " ").split() if t]
        k = int(params.get("k", 6))

        scored: list[tuple[float, dict]] = []
        for rel, defs in self._defs.items():
            for d in defs:
                name = d["name"].lower()
                score = 0.0
                if name in terms:
                    score += 3.0
                score += sum(1.0 for t in terms if t in name)
                if score:
                    stem = rel.rsplit("/", 1)[-1][:-3]
                    score += 0.25 * len(self._importers.get(stem, ()))
                    scored.append(
                        (score, {"path": rel, "span": [d["lineno"], d["endno"]], "text": d["text"], "score": score})
                    )
        scored.sort(key=lambda s: s[0], reverse=True)
        seen: set[tuple[str, int]] = set()
        out = []
        for _, chunk in scored:
            key = (chunk["path"], chunk["span"][0])
            if key in seen:
                continue
            seen.add(key)
            chunk["score"] = round(chunk["score"], 4)
            out.append(chunk)
            if len(out) >= k:
                break
        return {"chunks": out}

    @rpc("context.invalidate")
    async def invalidate(self, params, ctx):
        paths = params.get("paths") or list(self._defs)
        for rel in paths:
            rel = Path(rel).as_posix()
            self._defs.pop(rel, None)
            self._imports.pop(rel, None)
        for p in self._iter_py(params.get("paths")):
            self._index_file(p)
        return {"invalidated": len(paths)}


if __name__ == "__main__":
    run(ContextGraph())
