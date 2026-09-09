"""tools/filesystem — file tools confined to the workspace root."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from veridian.sdk import Brick, BrickError, rpc, run

_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file relative to the workspace root.",
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file relative to the workspace root.",
        "input_schema": {
            "type": "object",
            "required": ["path", "content"],
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        },
    },
    {
        "name": "list_dir",
        "description": "List a directory relative to the workspace root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}},
        },
    },
    {
        "name": "search",
        "description": "Regex-search files under the workspace root; returns matching lines.",
        "input_schema": {
            "type": "object",
            "required": ["pattern"],
            "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
        },
    },
]

_IGNORE = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}


class FilesystemTools(Brick):
    name = "tools/filesystem"
    version = "0.1.0"
    implements = {"tools": ["list", "invoke"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        return True

    def _confine(self, rel: str) -> Path:
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise BrickError(f"path {rel!r} escapes the workspace root", code=-32602)
        return target

    def _walk(self, base: Path, recursive: bool):
        for entry in sorted(base.iterdir()):
            if entry.name in _IGNORE:
                continue
            yield entry
            if recursive and entry.is_dir():
                yield from self._walk(entry, True)

    @rpc("tools.list")
    async def list_(self, params, ctx):
        return {"tools": _TOOLS}

    @rpc("tools.invoke")
    async def invoke(self, params, ctx):
        name, inp = params["name"], params.get("input", {})
        try:
            if name == "read_file":
                return {"output": self._confine(inp["path"]).read_text(encoding="utf-8")}
            if name == "write_file":
                target = self._confine(inp["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(inp["content"], encoding="utf-8")
                return {"output": f"wrote {len(inp['content'])} chars to {inp['path']}"}
            if name == "list_dir":
                base = self._confine(inp.get("path", "."))
                entries = [
                    f"{'d' if e.is_dir() else 'f'} {e.relative_to(self.root).as_posix()}"
                    for e in self._walk(base, bool(inp.get("recursive")))
                ]
                return {"output": "\n".join(entries) or "(empty)"}
            if name == "search":
                rx = re.compile(inp["pattern"])
                glob = inp.get("glob", "*")
                hits: list[str] = []
                for path in self._walk(self.root, True):
                    if not path.is_file() or not fnmatch.fnmatch(path.name, glob):
                        continue
                    try:
                        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                            if rx.search(line):
                                hits.append(f"{path.relative_to(self.root).as_posix()}:{i}: {line.strip()}")
                    except (UnicodeDecodeError, OSError):
                        continue
                    if len(hits) >= 200:
                        break
                return {"output": "\n".join(hits) or "(no matches)"}
        except BrickError:
            raise
        except (FileNotFoundError, NotADirectoryError, OSError, re.error) as exc:
            return {"output": f"{type(exc).__name__}: {exc}", "is_error": True}
        raise BrickError(f"unknown tool {name!r}", code=-32602)


if __name__ == "__main__":
    run(FilesystemTools())
