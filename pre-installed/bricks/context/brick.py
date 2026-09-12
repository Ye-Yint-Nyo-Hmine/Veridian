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
from collections import deque
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

# Indexing budget. ``retrieve`` indexes the whole workspace the first time it is called, and the
# kernel gives that call a finite deadline (``call_timeout`` on the binding, 120s by default), so
# an unbounded walk does not degrade — it times the whole run out with an error that names nothing
# useful. A workspace is not always a tidy repository: pointed at a home directory it can be
# hundreds of thousands of files, and on a sync-backed or network filesystem merely *reading* one
# can block. Every budget below is therefore a hard stop, not a hint, and tripping one yields a
# smaller index rather than a failure. ``max_seconds`` is the backstop that holds when the others
# are mis-set for the filesystem in front of them.
_MAX_INDEX_FILES = 2_000
_MAX_INDEX_BYTES = 32 * 1024 * 1024
_MAX_INDEX_DIRS = 4_000
_MAX_INDEX_SECONDS = 20.0
#: A single file larger than this is skipped outright: it is a bundle, a lockfile or a dump, and
#: chunking it costs more than the recall it adds.
_MAX_FILE_BYTES = 1024 * 1024
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
        self.max_files = int(self.config.get("max_files", _MAX_INDEX_FILES))
        self.max_bytes = int(self.config.get("max_bytes", _MAX_INDEX_BYTES))
        self.max_dirs = int(self.config.get("max_dirs", _MAX_INDEX_DIRS))
        self.max_seconds = float(self.config.get("max_seconds", _MAX_INDEX_SECONDS))
        self.max_file_bytes = int(self.config.get("max_file_bytes", _MAX_FILE_BYTES))
        self._chunks: list[dict] = []
        self._by_path: dict[str, list[int]] = {}
        #: Which budget stopped the last full index, or ``None`` if it completed. Reported to the
        #: host as a warning and written into the repository map so the model is told its view of
        #: the workspace is partial rather than silently given one.
        self._limit_hit: str | None = None
        self._indexed_files = 0
        return True

    # -- indexing ------------------------------------------------------------

    def _iter_files(self, paths: list[str] | None):
        """Yield the files to index.

        An explicit ``paths`` list is a caller naming its own files and is yielded in full. A full
        walk is breadth-first and budgeted: breadth-first so that when a budget does trip the files
        that made it in are the shallow ones near the workspace root, which are the project's own,
        rather than whatever a depth-first descent happened to reach first.
        """
        if paths:
            for rel in paths:
                p = (self.root / rel).resolve()
                if p.is_file():
                    yield p
            return

        self._limit_hit = None
        deadline = time.perf_counter() + self.max_seconds
        queue: deque[Path] = deque([self.root])
        dirs = files = total_bytes = 0

        while queue:
            if dirs >= self.max_dirs:
                self._limit_hit = f"{self.max_dirs} directories"
                return
            if time.perf_counter() > deadline:
                self._limit_hit = f"{self.max_seconds:g}s"
                return
            cur = queue.popleft()
            dirs += 1
            try:
                entries = sorted(cur.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name in _IGNORE:
                    continue
                try:
                    if entry.is_dir():
                        queue.append(entry)
                        continue
                    if entry.suffix.lower() not in _TEXT_EXT:
                        continue
                    size = entry.stat().st_size
                except OSError:
                    continue
                if size > self.max_file_bytes:
                    continue
                if files >= self.max_files:
                    self._limit_hit = f"{self.max_files} files"
                    return
                if total_bytes + size > self.max_bytes:
                    self._limit_hit = f"{self.max_bytes // (1024 * 1024)}MB"
                    return
                files += 1
                total_bytes += size
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
            if path.stat().st_size > self.max_file_bytes:
                return 0
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
                if count >= _MAP_MAX_ENTRIES:
                    return          # the map is full; nothing below can still be added
                if entry.name in _IGNORE:
                    continue
                count += 1
                if entry.is_dir():
                    rows.append(f"{prefix}{entry.name}/")
                    walk(entry, prefix + "  ", depth + 1)
                elif entry.suffix.lower() in _TEXT_EXT:
                    # Counting lines reads the whole file. Worth it for source, not for a bundle
                    # or a lockfile, which is also the one place a single entry can stall the map.
                    try:
                        n = 0 if entry.stat().st_size > self.max_file_bytes else sum(
                            1 for _ in entry.open("rb")
                        )
                    except OSError:
                        n = 0
                    rows.append(
                        f"{prefix}{entry.name}" + (f"  ({n} lines)" if n else "")
                    )
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
        n = 0
        files = 0
        for path in self._iter_files(params.get("paths")):
            files += 1
            n += self._index_file(path)
        self._indexed_files = files
        took_ms = (time.perf_counter() - t0) * 1000
        if self._limit_hit:
            # A warning, not an error: a partial index still answers most queries. The host
            # renders warning-level brick logs, so the user is told why recall is thin instead of
            # being left to infer it from poor answers.
            await self.host.log(
                f"indexed {files} file(s) of {self.root} and stopped at the "
                f"{self._limit_hit} budget — retrieval covers part of this workspace. "
                f"Point the agent at a project directory, or raise the context brick's "
                f"max_files / max_bytes / max_seconds config to widen it.",
                level="warning",
            )
        return {"indexed": n, "took_ms": took_ms}

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
        map_text = self._repo_map()
        if self._limit_hit:
            map_text += (
                f"\n\n[partial index: {self._indexed_files} file(s) indexed, stopped at the "
                f"{self._limit_hit} budget. This workspace is larger than the agent indexes by "
                f"default, so treat the map and the retrieved chunks as a sample of it, not a "
                f"complete listing. Search for files by name rather than assuming absence.]"
            )
        chunks.append(
            {
                "path": "<repository map>",
                "span": [1, 1],
                "text": map_text,
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
