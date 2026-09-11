"""tools/toolkit — a single ``tools`` brick that carries everything an autonomous coding agent
needs to touch the machine:

* **filesystem** — ``read_file``, ``write_file``, ``edit_file`` (exact search/replace),
  ``list_dir``, ``search_files`` (regex). All paths are workspace-relative and confined to the
  workspace root; an attempt to escape is a hard error, not a silent clamp.
* **terminal** — ``run_command`` executes through the bound ``sandbox`` contract
  (``host.contract.call("sandbox", "exec")``), so whichever sandbox the stack binds is what
  actually runs the process. This brick holds no execution logic of its own.
* **git** — ``git_status``, ``git_diff``, ``git_log``, ``git_add``, ``git_commit``, run as real
  ``git`` subprocesses in the workspace root (needs ``process:spawn``).
* **skills** — every directory under ``$VERIDIAN_HOME/skills`` with a ``SKILL.md`` is loaded at
  start (name + description from the frontmatter only) and exposed as its own tool
  ``skill.<name>``. Invoking it returns the full ``SKILL.md`` body so the model can follow it.
  ``skill_list`` / ``skill_create`` / ``skill_modify`` manage the set.

One brick, because a contract binds exactly one brick: three separate tool bricks could never be
bound at once.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path

from veridian.plugin_runtime.home import veridian_home
from veridian.sdk import Brick, BrickError, rpc, run

_INVALID_PARAMS = -32602

_IGNORE = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
           "dist", "build", ".ruff_cache", ".tox"}
_MAX_READ_BYTES = 256 * 1024
_MAX_SEARCH_HITS = 200
_MAX_OUTPUT = 16000

# ---------------------------------------------------------------------------
# static tool catalogue — descriptions are deliberately exact. A vague tool
# description is the main reason a model misuses a tool.
# ---------------------------------------------------------------------------

_FS_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file inside the workspace and return its contents. `path` is "
            "relative to the workspace root (a leading slash or `..` that escapes the root is an "
            "error). Optionally pass 1-based inclusive `start_line` and `end_line` to read only a "
            "slice; omit both to read the whole file. Output is prefixed with line numbers. "
            "Files larger than 256 KiB are truncated and the truncation is stated."
        ),
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "description": "workspace-relative file path"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or completely overwrite an existing one with `content` (UTF-8 "
            "text). `path` is workspace-relative; parent directories are created as needed. Use "
            "this for new files or a full rewrite. To change part of an existing file prefer "
            "`edit_file`, which will not clobber the rest of the file."
        ),
        "input_schema": {
            "type": "object",
            "required": ["path", "content"],
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace one exact occurrence of `old_text` with `new_text` in the file at `path`. "
            "`old_text` must match the file byte-for-byte including indentation and must be unique "
            "in the file; if it is missing or appears more than once the call fails and nothing "
            "is written. Include enough surrounding context in `old_text` to make it unique. To "
            "delete code, pass an empty `new_text`."
        ),
        "input_schema": {
            "type": "object",
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
        },
    },
    {
        "name": "list_dir",
        "description": (
            "List the entries of a directory inside the workspace. `path` defaults to the "
            "workspace root. Set `recursive` true to walk the whole subtree. Each line is "
            "`d <path>` for a directory or `f <path>` for a file, paths workspace-relative. "
            "Build and VCS directories (.git, node_modules, __pycache__, …) are skipped."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search file contents under the workspace with a Python regular expression and return "
            "matching lines as `path:line: text`. `pattern` is the regex. Optional `glob` filters "
            "by file name (e.g. `*.py`, default all). Optional `path` limits the search to one "
            "subtree. Binary and build directories are skipped; at most 200 matches are returned."
        ),
        "input_schema": {
            "type": "object",
            "required": ["pattern"],
            "additionalProperties": False,
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
]

_TERMINAL_TOOL = {
    "name": "run_command",
    "description": (
        "Run a shell command in the workspace through the sandbox and return its exit code, "
        "stdout and stderr. Use this to run tests, build tools, linters, or any executable. "
        "`command` is a single shell command line. Optional `cwd` is workspace-relative "
        "(default: workspace root). Optional `timeout_ms` caps the run (default 30000). A "
        "non-zero exit code is reported as an error but the full output is still returned so you "
        "can diagnose it."
    ),
    "input_schema": {
        "type": "object",
        "required": ["command"],
        "additionalProperties": False,
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout_ms": {"type": "integer", "minimum": 1},
        },
    },
}

_GIT_TOOLS = [
    {
        "name": "git_status",
        "description": "Show `git status --porcelain=v1 -b` for the workspace: the branch line "
                       "plus one line per changed, staged, or untracked path.",
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "git_diff",
        "description": "Show a unified diff of the workspace. By default this is the unstaged "
                       "working-tree diff; set `staged` true for the diff of what is staged "
                       "(`--cached`). Optional `path` limits the diff to one file or directory.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"staged": {"type": "boolean"}, "path": {"type": "string"}},
        },
    },
    {
        "name": "git_log",
        "description": "Show the last `n` commits (default 10) as `<short-hash> <subject>`.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"n": {"type": "integer", "minimum": 1}},
        },
    },
    {
        "name": "git_add",
        "description": "Stage the given workspace-relative `paths` (`git add -- <paths>`). Pass "
                       "`[\".\"]` to stage everything.",
        "input_schema": {
            "type": "object",
            "required": ["paths"],
            "additionalProperties": False,
            "properties": {"paths": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
        },
    },
    {
        "name": "git_commit",
        "description": "Commit the currently staged changes with `message`. Fails if nothing is "
                       "staged. Does not add or push.",
        "input_schema": {
            "type": "object",
            "required": ["message"],
            "additionalProperties": False,
            "properties": {"message": {"type": "string"}},
        },
    },
]

_SKILL_MGMT_TOOLS = [
    {
        "name": "skill_list",
        "description": "List every installed skill as `<name>: <description>`. Skills are reusable "
                       "instruction sets under the user's skills directory. Call a specific skill "
                       "with its own `skill.<name>` tool to load its full instructions.",
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "skill_create",
        "description": "Create a new skill. `name` is a short kebab-case identifier, `description` "
                       "is a one-line summary used for selection, `body` is the Markdown "
                       "instructions. Writes `<skills-dir>/<name>/SKILL.md` with YAML frontmatter.",
        "input_schema": {
            "type": "object",
            "required": ["name", "description", "body"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "body": {"type": "string"},
            },
        },
    },
    {
        "name": "skill_modify",
        "description": "Update an existing skill's `description` and/or `body`. Omitted fields are "
                       "left unchanged.",
        "input_schema": {
            "type": "object",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "body": {"type": "string"},
            },
        },
    },
]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def _parse_skill(md: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md into ``(frontmatter, body)``. Frontmatter is a tiny ``key: value`` YAML
    subset — enough for ``name`` and ``description`` without a yaml dependency."""
    m = _FRONTMATTER_RE.match(md)
    if not m:
        return {}, md
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, val = line.partition(":")
            fm[key.strip().lower()] = val.strip().strip('"').strip("'")
    return fm, m.group(2)


def _skill_tool_name(name: str) -> str:
    return "skill." + re.sub(r"[^a-z0-9_.-]+", "-", name.strip().lower())


class Toolkit(Brick):
    name = "tools/toolkit"
    version = "0.1.0"
    implements = {"tools": ["list", "invoke"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        self.skills_dir = Path(
            self.config.get("skills_dir") or (veridian_home() / "skills")
        ).expanduser()
        self._skills: dict[str, dict] = {}  # tool name -> {"dir", "name", "description"}
        self._load_skills()
        return True

    # -- skills -----------------------------------------------------------------

    def _load_skills(self) -> None:
        self._skills.clear()
        if not self.skills_dir.is_dir():
            return
        for entry in sorted(self.skills_dir.iterdir()):
            md = entry / "SKILL.md"
            if not entry.is_dir() or not md.is_file():
                continue
            try:
                fm, _ = _parse_skill(md.read_text(encoding="utf-8"))
            except OSError:
                continue
            skill_name = fm.get("name") or entry.name
            desc = fm.get("description") or f"the {skill_name} skill"
            self._skills[_skill_tool_name(skill_name)] = {
                "dir": entry,
                "name": skill_name,
                "description": desc,
            }

    def _skill_tools(self) -> list[dict]:
        return [
            {
                "name": tool_name,
                "description": (
                    f"Skill: {s['description']} "
                    "Invoke to load this skill's full step-by-step instructions, then follow them."
                ),
                "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
            }
            for tool_name, s in sorted(self._skills.items())
        ]

    # -- path confinement -----------------------------------------------------

    def _confine(self, rel: str | None) -> Path:
        target = (self.root / (rel or ".")).resolve()
        if target != self.root and self.root not in target.parents:
            raise BrickError(f"path {rel!r} escapes the workspace root", code=_INVALID_PARAMS)
        return target

    def _walk(self, base: Path, recursive: bool):
        try:
            entries = sorted(base.iterdir())
        except (NotADirectoryError, FileNotFoundError, OSError) as exc:
            raise BrickError(f"{type(exc).__name__}: {exc}", code=_INVALID_PARAMS) from exc
        for entry in entries:
            if entry.name in _IGNORE:
                continue
            yield entry
            if recursive and entry.is_dir():
                yield from self._walk(entry, True)

    # -- git ----------------------------------------------------------------

    def _git(self, args: list[str]) -> tuple[int, str]:
        try:
            p = subprocess.run(
                ["git", *args],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,  # never inherit the brick's JSON-RPC stdin pipe
            )
        except FileNotFoundError:
            return 127, "git is not installed or not on PATH"
        except subprocess.TimeoutExpired:
            return 124, "git command timed out"
        out = (p.stdout + (("\n" + p.stderr) if p.stderr else "")).strip()
        return p.returncode, out or "(no output)"

    # -- contract methods --------------------------------------------------

    @rpc("tools.list")
    async def list_(self, params, ctx):
        # reload skills so a skill_create in this session is visible on the next list
        self._load_skills()
        tools = [
            *_FS_TOOLS,
            _TERMINAL_TOOL,
            *_GIT_TOOLS,
            *_SKILL_MGMT_TOOLS,
            *self._skill_tools(),
        ]
        return {"tools": tools}

    @rpc("tools.invoke")
    async def invoke(self, params, ctx):
        name = params["name"]
        inp = params.get("input") or {}

        if name in self._skills or name.startswith("skill."):
            return self._invoke_skill(name)
        if name in {"skill_list", "skill_create", "skill_modify"}:
            return self._invoke_skill_mgmt(name, inp)
        if name == "run_command":
            return await self._run_command(inp)
        if name.startswith("git_"):
            return self._invoke_git(name, inp)
        return self._invoke_fs(name, inp)

    # -- filesystem impl -------------------------------------------------

    def _invoke_fs(self, name: str, inp: dict) -> dict:
        try:
            if name == "read_file":
                return self._read_file(inp)
            if name == "write_file":
                target = self._confine(inp["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(inp["content"], encoding="utf-8")
                return {"output": f"wrote {len(inp['content'])} chars to {inp['path']}"}
            if name == "edit_file":
                return self._edit_file(inp)
            if name == "list_dir":
                base = self._confine(inp.get("path", "."))
                lines = [
                    f"{'d' if e.is_dir() else 'f'} {e.relative_to(self.root).as_posix()}"
                    for e in self._walk(base, bool(inp.get("recursive")))
                ]
                return {"output": "\n".join(lines) or "(empty)"}
            if name == "search_files":
                return self._search(inp)
        except BrickError:
            raise
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError, re.error) as exc:
            return {"output": f"{type(exc).__name__}: {exc}", "is_error": True}
        raise BrickError(f"unknown tool {name!r}", code=_INVALID_PARAMS)

    def _read_file(self, inp: dict) -> dict:
        target = self._confine(inp["path"])
        data = target.read_bytes()
        truncated = len(data) > _MAX_READ_BYTES
        text = data[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = inp.get("start_line")
        end = inp.get("end_line")
        if start is not None or end is not None:
            s = max(1, int(start or 1))
            e = min(len(lines), int(end or len(lines)))
            sliced = lines[s - 1 : e]
            body = "\n".join(f"{s + i:>6}\t{ln}" for i, ln in enumerate(sliced))
            head = f"{inp['path']} lines {s}-{e} of {len(lines)}"
        else:
            body = "\n".join(f"{i:>6}\t{ln}" for i, ln in enumerate(lines, 1))
            head = f"{inp['path']} ({len(lines)} lines)"
        if truncated:
            head += f" — truncated to {_MAX_READ_BYTES // 1024} KiB"
        return {"output": f"# {head}\n{body}"}

    def _edit_file(self, inp: dict) -> dict:
        target = self._confine(inp["path"])
        original = target.read_text(encoding="utf-8")
        old = inp["old_text"]
        count = original.count(old)
        if count == 0:
            return {"output": f"old_text not found in {inp['path']}; nothing written", "is_error": True}
        if count > 1:
            return {
                "output": f"old_text appears {count} times in {inp['path']}; make it unique by "
                          f"including more surrounding context. Nothing written.",
                "is_error": True,
            }
        target.write_text(original.replace(old, inp["new_text"], 1), encoding="utf-8")
        delta = len(inp["new_text"]) - len(old)
        return {"output": f"edited {inp['path']} ({delta:+d} chars)"}

    def _search(self, inp: dict) -> dict:
        rx = re.compile(inp["pattern"])
        glob = inp.get("glob", "*")
        base = self._confine(inp.get("path", "."))
        roots = [base] if base.is_dir() else [base.parent]
        hits: list[str] = []
        for root in roots:
            for path in self._walk(root, True):
                if not path.is_file() or not fnmatch.fnmatch(path.name, glob):
                    continue
                try:
                    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if rx.search(line):
                            hits.append(
                                f"{path.relative_to(self.root).as_posix()}:{i}: {line.strip()[:400]}"
                            )
                            if len(hits) >= _MAX_SEARCH_HITS:
                                return {"output": "\n".join(hits) + "\n… (truncated at 200 matches)"}
                except (UnicodeDecodeError, OSError):
                    continue
        return {"output": "\n".join(hits) or "(no matches)"}

    # -- terminal impl (through the sandbox contract) --------------------

    async def _run_command(self, inp: dict) -> dict:
        call: dict = {"command": inp["command"]}
        if inp.get("cwd"):
            call["cwd"] = inp["cwd"]
        call["timeout_ms"] = int(inp.get("timeout_ms", 30000))
        try:
            res = await self.host.contract_call("sandbox", "exec", call)
        except Exception as exc:  # noqa: BLE001 - report, never crash the agent loop
            return {"output": f"sandbox exec failed: {exc}", "is_error": True}
        body = (
            f"$ {inp['command']}\n"
            f"exit={res['exit_code']} timed_out={res.get('timed_out', False)}\n"
            f"--- stdout ---\n{res['stdout']}\n--- stderr ---\n{res['stderr']}"
        )
        return {"output": body[:_MAX_OUTPUT], "is_error": res["exit_code"] != 0}

    # -- git impl ---------------------------------------------------------

    def _invoke_git(self, name: str, inp: dict) -> dict:
        if name == "git_status":
            code, out = self._git(["status", "--porcelain=v1", "-b"])
        elif name == "git_diff":
            args = ["diff"] + (["--cached"] if inp.get("staged") else [])
            if inp.get("path"):
                args += ["--", inp["path"]]
            code, out = self._git(args)
        elif name == "git_log":
            code, out = self._git(["log", f"-n{int(inp.get('n', 10))}", "--pretty=format:%h %s"])
        elif name == "git_add":
            code, out = self._git(["add", "--", *inp["paths"]])
            if code == 0 and out == "(no output)":
                out = f"staged {', '.join(inp['paths'])}"
        elif name == "git_commit":
            code, out = self._git(["commit", "-m", inp["message"]])
        else:
            raise BrickError(f"unknown tool {name!r}", code=_INVALID_PARAMS)
        return {"output": out[:_MAX_OUTPUT], "is_error": code != 0}

    # -- skills impl ----------------------------------------------------

    def _invoke_skill(self, tool_name: str) -> dict:
        s = self._skills.get(tool_name)
        if s is None:
            self._load_skills()
            s = self._skills.get(tool_name)
        if s is None:
            return {"output": f"no skill bound to {tool_name!r}", "is_error": True}
        try:
            md = (s["dir"] / "SKILL.md").read_text(encoding="utf-8")
        except OSError as exc:
            return {"output": f"could not read skill {s['name']}: {exc}", "is_error": True}
        _, body = _parse_skill(md)
        extra = sorted(
            p.name for p in s["dir"].iterdir() if p.is_file() and p.name != "SKILL.md"
        )
        note = f"\n\n(other files in this skill: {', '.join(extra)})" if extra else ""
        return {"output": f"# skill: {s['name']}\n\n{body.strip()}{note}"}

    def _invoke_skill_mgmt(self, name: str, inp: dict) -> dict:
        if name == "skill_list":
            self._load_skills()
            if not self._skills:
                return {"output": f"(no skills installed under {self.skills_dir})"}
            return {
                "output": "\n".join(
                    f"{s['name']}: {s['description']}  [{tool}]"
                    for tool, s in sorted(self._skills.items())
                )
            }
        skill_name = re.sub(r"[^a-z0-9_.-]+", "-", inp["name"].strip().lower())
        if not skill_name:
            return {"output": "skill name is empty after normalisation", "is_error": True}
        sdir = self.skills_dir / skill_name
        md_path = sdir / "SKILL.md"
        if name == "skill_create":
            if md_path.exists():
                return {"output": f"skill {skill_name!r} already exists; use skill_modify",
                        "is_error": True}
            sdir.mkdir(parents=True, exist_ok=True)
            md_path.write_text(
                f"---\nname: {skill_name}\ndescription: {inp['description'].strip()}\n---\n\n"
                f"{inp['body'].rstrip()}\n",
                encoding="utf-8",
            )
            self._load_skills()
            return {"output": f"created skill {skill_name!r} at {md_path}"}
        # skill_modify
        if not md_path.is_file():
            return {"output": f"no skill {skill_name!r} to modify", "is_error": True}
        fm, body = _parse_skill(md_path.read_text(encoding="utf-8"))
        desc = inp.get("description", fm.get("description", ""))
        new_body = inp.get("body", body).rstrip()
        md_path.write_text(
            f"---\nname: {skill_name}\ndescription: {desc.strip()}\n---\n\n{new_body}\n",
            encoding="utf-8",
        )
        self._load_skills()
        changed = ", ".join(k for k in ("description", "body") if k in inp) or "nothing"
        return {"output": f"updated skill {skill_name!r} ({changed})"}


if __name__ == "__main__":
    run(Toolkit())
