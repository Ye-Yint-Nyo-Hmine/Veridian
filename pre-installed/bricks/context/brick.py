"""context/repo-map — repository awareness for the autonomous loop.

What it does beyond a flat keyword index:

* **Repository map.** ``retrieve`` always returns one ``<repository map>`` chunk: a depth-limited
  directory tree with per-file line counts, so the model sees the shape of the project without a
  whole-tree dump.
* **Selective retrieval.** Files are chunked by line window and ranked by keyword overlap with the
  query; only the top ``k`` chunks come back, never the whole tree.
* **Large-file summarisation.** A file over ``summarise_over`` lines (default 400) is not returned
  raw — it is reduced to a symbol outline (``def`` / ``class`` / ``function`` / ``export`` … lines
  with their line numbers) plus its first few lines.
* **AGENT.md guidelines.** ``$VERIDIAN_HOME/AGENT.md`` (user-level) and ``<workspace>/AGENT.md``
  (workspace-level) are loaded and returned as high-priority chunks on every ``retrieve``. The
  workspace guideline outranks the user one, and the chunk text says so, so the model resolves a
  conflict in favour of the workspace.

No model is required; ``retrieve`` params/results are the ordinary ``context`` contract.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from veridian.plugin_runtime.home import veridian_home
from veridian.sdk import Brick, rpc, run

_IGNORE = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
           "dist", "build", ".ruff_cache", ".tox", ".idea", ".vscode"}
_TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".txt", ".toml", ".json", ".yaml", ".yml",
             ".cfg", ".ini", ".rs", ".go", ".java", ".c", ".h", ".hpp", ".cpp", ".sh", ".rb",
             ".php", ".sql", ".html", ".css", ".scss"}
_CHUNK_LINES = 40
_SUMMARISE_OVER = 400
_MAP_MAX_ENTRIES = 400
_MAP_MAX_DEPTH = 4
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_SYMBOL = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def |class |function |func |fn |interface |type |struct |"
    r"impl |public |private |protected |module |const \w+\s*=\s*(?:async\s*)?\()"
)

_AGENT_FILENAME = "AGENT.md"


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text)}


class ContextRepoMap(Brick):
    name = "context/repo-map"
    version = "0.1.0"
    implements = {"context": ["index", "retrieve", "invalidate"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        self.chunk_lines = int(self.config.get("chunk_lines", _CHUNK_LINES))
        self.summarise_over = int(self.config.get("summarise_over", _SUMMARISE_OVER))
        self.user_agent_path = Path(
            self.config.get("user_agent_md") or (veridian_home() / _AGENT_FILENAME)
        ).expanduser()
        self._chunks: list[dict] = []
        self._by_path: dict[str, list[int]] = {}
        return True

    # -- indexing ------------------------------------------------------------

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
            try:
                entries = sorted(cur.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name in _IGNORE:
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.suffix.lower() in _TEXT_EXT:
                    yield entry

    def _symbol_outline(self, lines: list[str]) -> str:
        outline = [
            f"{i:>6}\t{ln.rstrip()}"
            for i, ln in enumerate(lines, 1)
            if _SYMBOL.match(ln)
        ]
        head = "\n".join(f"{i:>6}\t{ln}" for i, ln in enumerate(lines[:15], 1))
        body = "\n".join(outline[:120]) or "(no top-level symbols detected)"
        return (
            f"[large file: {len(lines)} lines — outline only]\n"
            f"--- first 15 lines ---\n{head}\n--- symbol outline ---\n{body}"
        )

    def _index_file(self, path: Path) -> int:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            return 0
        rel = path.relative_to(self.root).as_posix()
        self._drop(rel)
        idxs: list[int] = []

        if len(lines) > self.summarise_over:
            text = self._symbol_outline(lines)
            idxs.append(len(self._chunks))
            self._chunks.append(
                {"path": rel, "span": [1, len(lines)], "text": text,
                 "tokens": _tokens(text) | _tokens(rel), "kind": "outline"}
            )
            self._by_path[rel] = idxs
            return 1

        for start in range(0, max(1, len(lines)), self.chunk_lines):
            body = "\n".join(lines[start : start + self.chunk_lines])
            if not body.strip():
                continue
            idxs.append(len(self._chunks))
            self._chunks.append(
                {
                    "path": rel,
                    "span": [start + 1, min(len(lines), start + self.chunk_lines)],
                    "text": body,
                    "tokens": _tokens(body) | _tokens(rel),
                    "kind": "chunk",
                }
            )
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

    # -- repository map ----------------------------------------------------

    def _repo_map(self) -> str:
        rows: list[str] = []
        count = 0

        def walk(d: Path, prefix: str, depth: int) -> None:
            nonlocal count
            if depth > _MAP_MAX_DEPTH or count >= _MAP_MAX_ENTRIES:
                return
            try:
                entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError:
                return
            for entry in entries:
                if entry.name in _IGNORE or count >= _MAP_MAX_ENTRIES:
                    continue
                count += 1
                if entry.is_dir():
                    rows.append(f"{prefix}{entry.name}/")
                    walk(entry, prefix + "  ", depth + 1)
                elif entry.suffix.lower() in _TEXT_EXT:
                    try:
                        n = sum(1 for _ in entry.open("rb"))
                    except OSError:
                        n = 0
                    rows.append(f"{prefix}{entry.name}  ({n} lines)")
                else:
                    rows.append(f"{prefix}{entry.name}")

        walk(self.root, "", 0)
        tail = "\n… (map truncated)" if count >= _MAP_MAX_ENTRIES else ""
        return f"{self.root.name}/\n" + "\n".join(rows) + tail

    # -- AGENT.md guidelines --------------------------------------------

    def _agent_chunks(self) -> list[dict]:
        out: list[dict] = []
        user = self._read_text(self.user_agent_path)
        if user:
            out.append(
                {
                    "path": f"{_AGENT_FILENAME} (user guideline)",
                    "span": [1, user.count("\n") + 1],
                    "text": (
                        "User-level agent guideline from "
                        f"{self.user_agent_path}. Applies to every workspace; the workspace "
                        "AGENT.md overrides it on any conflict.\n\n" + user
                    ),
                    "score": 0.98,
                }
            )
        ws = self._read_text(self.root / _AGENT_FILENAME)
        if ws:
            out.append(
                {
                    "path": f"{_AGENT_FILENAME} (workspace guideline)",
                    "span": [1, ws.count("\n") + 1],
                    "text": (
                        "Workspace agent guideline from <workspace>/AGENT.md. Authoritative: "
                        "where it conflicts with the user-level guideline, follow this one.\n\n"
                        + ws
                    ),
                    "score": 0.99,
                }
            )
        return out

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            data = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
        return data if data.strip() else ""

    # -- contract methods ----------------------------------------------

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

        scored: list[tuple[float, dict]] = []
        for c in self._chunks:
            overlap = len(q & c["tokens"])
            if overlap:
                base = overlap / (len(q) or 1)
                if c.get("kind") == "outline":
                    base *= 0.9
                scored.append((round(base, 4), c))
        scored.sort(key=lambda s: s[0], reverse=True)

        chunks: list[dict] = list(self._agent_chunks())
        chunks.append(
            {
                "path": "<repository map>",
                "span": [1, 1],
                "text": self._repo_map(),
                "score": 0.97,
            }
        )
        for score, c in scored[:k]:
            chunks.append(
                {"path": c["path"], "span": c["span"], "text": c["text"], "score": score}
            )
        return {"chunks": chunks}

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
    run(ContextRepoMap())
