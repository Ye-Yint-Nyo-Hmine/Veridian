# `pre-installed/` — Veridian's default autonomous coding agent

A complete stack plus the bricks it needs to rival a general-purpose autonomous coding agent,
shipped as a self-contained tree. Nothing here modifies the kernel, the schemas, or the built-in
`bricks/`. It works because of two facts already true of Veridian:

1. **A stack may reference a brick by a path relative to the repo root.** So
   `pre-installed/bricks/<name>` binds fine even though `pre-installed/` is not a brick search
   root.
2. **A contract binds exactly one brick.** So the filesystem / terminal / git / skill tools are
   one *toolkit* brick that provides them internally, not three bricks that could never be bound
   at the same time.

```
pre-installed/
├── bricks/
│   ├── toolkit/        tools/toolkit        — fs + terminal + git + skills, one `tools` brick
│   ├── context/        context/repo-map     — repo map, selective retrieval, AGENT.md
│   └── orchestrator/   orchestrator/autonomous — the verifying autonomous loop
└── stacks/
    └── autonomous.toml — binds all of the above (no inference binding; you add one)
```

## Quick start

The stack ships **without an inference binding** — no provider name, no model id, no base URL.
Add one (or let a setup flow do it) before running:

```toml
# append to pre-installed/stacks/autonomous.toml, or use an overlay stack
[bindings.inference]
brick = "bricks/inference/local"   # the built-in adapter for a local chat-completions server
config = { model = "<your-model>" }
# point it at your endpoint with the VERIDIAN_LOCAL_BASE_URL environment variable
```

Then:

```bash
uv run veridian --stack pre-installed/stacks/autonomous.toml
# or one-shot:
uv run veridian run "fix the failing test in calc.py" -s pre-installed/stacks/autonomous.toml
```

`--resume` works out of the box: the stack binds `conversation/sqlite`, and the orchestrator keys
history under the session id the CLI injects, so `veridian --resume <id>` brings back the
conversation.

## The bricks

### `tools/toolkit` — one tools brick, every capability

| tool | what it does |
|---|---|
| `read_file` | read a file (optionally a 1-based line slice), line-numbered |
| `write_file` | create or fully overwrite a file |
| `edit_file` | replace one **unique exact** occurrence of `old_text`; fails (writes nothing) if it is missing or ambiguous |
| `list_dir` | list a directory, optionally recursive; skips VCS/build dirs |
| `search_files` | regex search over file contents → `path:line: text` |
| `run_command` | run a shell command **through the `sandbox` contract** (`host.contract.call("sandbox", "exec")`) — this brick holds no execution logic |
| `git_status` / `git_diff` / `git_log` / `git_add` / `git_commit` | real `git` subprocesses in the workspace root |
| `skill_list` / `skill_create` / `skill_modify` | manage skills |
| `skill.<name>` | one per installed skill — invoking it returns the skill's full instructions |

All filesystem paths are workspace-relative and confined to the workspace root; an attempt to
escape is a hard error, never a silent clamp. Tool descriptions are intentionally exact — a vague
description is the main reason a model misuses a tool.

### `context/repo-map` — repository awareness

- **Repository map**: every `retrieve` returns one `<repository map>` chunk — a depth-limited
  tree with per-file line counts, so the model sees the project shape without a whole-tree dump.
- **Selective retrieval**: files are chunked and ranked by keyword overlap with the query; only
  the top `k` come back.
- **Large-file summarisation**: a file over `summarise_over` lines (default 400) is returned as a
  symbol outline (`def` / `class` / `function` / `export` … with line numbers) plus its head,
  never raw.
- **AGENT.md guidelines**: see [`AGENT.md-format.md`](AGENT.md-format.md). `retrieve` always
  returns the user-level and workspace-level `AGENT.md` as high-priority chunks; the workspace one
  outranks the user one and the chunk text says so, so a conflict resolves in favour of the
  workspace.

### `orchestrator/autonomous` — the verifying loop

Per turn: **inspect → act with tools → observe → recover → verify.**

- **Objective verification.** The goal is split into objectives. An objective is *not* closed
  because the model says so — the loop decides the evidence (`run_command` exits 0 / a diff on
  disk / a successful write) and checks it before advancing. A `step` delta with
  `kind: "verify"` reports the check and its evidence.
- **Git awareness.** The working tree is snapshotted before the run and after every objective;
  the final `summary` reports the files that actually changed plus a diffstat — not an assertion.
- **Failure recovery.** A failed command or tool result is fed back with its real output and the
  model is asked to change approach. Consecutive failures are capped (`max_recovery`, default 3);
  at the cap the loop emits an escalation `message`, returns `status: "failed"`, and stops —
  it does not loop forever.
- **Context management.** The working transcript is compacted automatically (summarise the
  middle, keep head + tail) once it passes ~12k tokens.

Integration guarantees:

- `session_id` is read from the **binding config**, never the wire.
- `compact` is non-destructive: it appends a summary turn tagged
  `metadata.kind = "compaction"`; later runs load only that summary and what follows. The
  verbatim turns stay on disk. `/compact` in the CLI drives this.
- `usage` deltas are emitted **only when the provider reports usage** — never zeros.
- Every long operation is a plain `await` on `host.contract.call`; `$/cancel` cascades through
  the kernel. There is no second cancellation mechanism.

## Skills

Skills live under `$VERIDIAN_HOME/skills/` (i.e. `~/.veridian/skills/`, created on demand). Each
skill is a directory with a `SKILL.md`:

```markdown
---
name: run-migrations
description: Apply pending DB migrations and verify the schema. Use before any data task.
---

# Run migrations

1. …
```

At session start the toolkit loads **only** the `name` and `description` from each skill's
frontmatter and exposes each skill as a `skill.<name>` tool, so the model can pick one cheaply.
The full body is read only when that tool is invoked, and returned as the tool output for the
model to follow. `skill_create` / `skill_modify` / `skill_list` are also tools.

**Follow-up (needs a source change, not done here):** a user-facing `/skills` slash command to
browse and invoke skills interactively. The command registry
(`src/veridian/cli/repl_commands.py`) and the reusable numbered selector
(`src/veridian/cli/ui/selector.py`) it would build on already exist; wiring a new command in is a
`src/` edit, which this tree deliberately does not make.

## What this reuses from the CLI (not rebuilt here)

Session metadata + `--resume` (`session_store.py`), `@`-mention file/folder attachment, the slash
command registry and numbered selector, `/model` / `/context` / `/compact`, streaming and
`$/cancel`. The orchestrator keys history under the CLI's session id so all of that keeps
working.

## Tests

`tests/preinstalled/` — brick conformance, the no-provider-name guarantee, the toolkit tools, the
context brick's map + AGENT.md precedence, and the orchestrator's verification / recovery /
compaction wiring. Live end-to-end and `--resume` round-trip coverage is
`tests/preinstalled/test_autonomous_live.py` (marked `live`).
