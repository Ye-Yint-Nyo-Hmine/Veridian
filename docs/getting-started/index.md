# Getting started

## Install

```bash
curl -LsSf https://raw.githubusercontent.com/Ye-Yint-Nyo-Hmine/Veridian/main/install.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/Ye-Yint-Nyo-Hmine/Veridian/main/install.ps1 | iex
```

Veridian lands under `~/.veridian` with its own Python 3.13 environment, and a `veridian` launcher
goes on your PATH. [uv](https://docs.astral.sh/uv/) is installed first if you don't have it — it is
what provides the interpreter, so you do not need a matching Python yourself.

Options, for either script: `--version <v>` / `-Version <v>` to pin a release, `--no-modify-path` /
`-NoModifyPath` to skip the shell-profile edit, `--force` / `-Force` to reinstall the current
version, and `VERIDIAN_HOME` to install somewhere other than `~/.veridian`. Re-running the installer
is the upgrade path: the new version is built and smoke-tested in full before it becomes active, so
a failed upgrade leaves the working install alone.

To work on Veridian itself, use a checkout instead — `uv sync --extra dev`, then `uv run veridian`.
A checkout always takes precedence over an installed copy, so the two coexist.

## Check your environment

```bash
veridian doctor
```

Everything except "an inference provider" should be `ok`. The kernel and the whole hermetic test
suite run with no provider configured. `doctor` also prints whether you are running an installed
copy or a checkout, which root it resolved, and whether the launcher on your PATH is the one this
install wrote — the usual explanation for "my upgrade did nothing".

## Your first session

```bash
cd /path/to/a/scratch/repo
veridian
```

**The directory you start it in becomes the workspace** — where context is retrieved from, where
tools read and write, and where bricks are confined. Use `--workspace` to point somewhere else.

Start it in a project, not in your home directory. A workspace is indexed on the first retrieval
of every session, and the context brick stops at a budget rather than walking a whole machine, so
a home directory gets you an agent that silently sees a fraction of your files. Veridian warns at
startup when the workspace resolves to your home directory or a filesystem root; the budget and
the warning are both described under [`../bricks/`](../bricks/).

The first run asks which model provider to use and which agent to run:

| Provider | Brick | Key |
|---|---|---|
| Anthropic (Claude) | `inference/anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI (GPT) | `inference/openai` | `OPENAI_API_KEY` |
| Google (Gemini) | `inference/openai` | `GEMINI_API_KEY` |
| DeepSeek | `inference/openai` | `DEEPSEEK_API_KEY` |
| Moonshot (Kimi) | `inference/openai` | `MOONSHOT_API_KEY` |
| Ollama | `inference/local` | none |
| Other OpenAI-compatible server | `inference/local` | none |

Gemini, DeepSeek and Moonshot serve the OpenAI chat-completions API, so they are the same adapter
behind a different base URL — see [`../bricks/`](../bricks/).

**Ollama is detected, not asked for.** Veridian looks for a server on Ollama's port (honouring
`OLLAMA_HOST`), then lists the models you have actually pulled so you can pick one. You never type
a URL or a model name. If nothing is running it falls back to the default endpoint and says so, so
starting Ollama later is all it takes. Pick "Other OpenAI-compatible server" for llama.cpp, vLLM or
LM Studio on a different port — that one does ask for the URL, then lists that server's models.

The answers are written to `~/.veridian/` and it never asks again: a generated stack in
`~/.veridian/stacks/veridian.toml`, the choice of default in `~/.veridian/config.toml`, and — only
if you ask it to save one — your API key in `~/.veridian/env`. That last file is plaintext; the
same variable set in your shell overrides it and is the better option if you already manage secrets
elsewhere. Run `/setup` in a session to answer the questions again — switching provider is two
answers rather than a TOML edit, and the running session is rebound in place. The questions are
skipped entirely when stdin is not a terminal, so scripts and CI are unaffected.

It then prints the active stack, the inference binding, and the workspace, and waits at a `›`
prompt. Type a goal and it streams the
orchestrator's plan, active steps, tool calls, and model output, then a final status line. `/help`
lists the session commands (it is rendered from the command registry, so it can't drift);
Ctrl-C interrupts a running goal; Ctrl-D exits.

Session commands: `/setup` (re-run the provider and agent questions, then rebind the session — a
cancelled answer changes nothing), `/stack`, `/workspace`, `/mode` (cycle plan ↔ auto — plan enforces
`contract:sandbox` so no command execution; the `workspace:write` withholding is advisory this
milestone), `/model` (list the models the inference brick reports and switch live — the brick is
rebound and restarted, and a failed switch keeps the previous model), `/context` (what is loaded
and the token usage the footer shows), and `/compact` (ask the orchestrator to compress its
working context; degrades cleanly if the bound orchestrator doesn't implement it).

Put `@path` in a goal to attach a file's contents or a directory listing to that turn, resolved
against the workspace before the turn starts — an unresolved or out-of-workspace path is an error,
a truncated file or a listed directory is stated plainly. There is no tab completion after `@`.

Every session is written to `~/.veridian/sessions/<id>/` as it runs. `veridian --resume <id>`
restores it — including the conversation history, since the id on screen is the one the
conversation brick keys history under. `veridian --resume` with no id lists resumable sessions to
pick from.

For a single goal without the prompt — in a script, or a CI step — use the one-shot form:

```bash
veridian run "add a docstring to the top-level function and run the tests" \
  --workspace /path/to/a/scratch/repo
```

It exits `0` when the run completes, non-zero otherwise. Unlike the interactive session, it refuses
to start without a reachable provider, since there is no prompt to correct it at.

Every command below is written as `veridian`; from a checkout, prefix it with `uv run`.

## Running fully local

`stacks/local-only.toml` points `inference` and `model_provider` at an OpenAI-compatible server
(Ollama, llama.cpp, vLLM, LM Studio):

```bash
export VERIDIAN_LOCAL_BASE_URL=http://localhost:11434/v1
veridian --stack local-only
```

Pull the models the stack names (`llama3.2`, `nomic-embed-text`) or edit the `config` in the stack
file. That stack binds vector context and vector memory, so it needs an embedding model as well as
a chat model; `stacks/local-ollama.toml` uses keyword context and ephemeral memory instead and
needs only a chat model.

`/model` lists the models the bound inference brick reports and switches between them live, so you
only need to edit a stack file to change *provider*, not to change model.

## The pre-installed agent

`pre-installed/` holds a complete autonomous coding agent built entirely from bricks: a verifying
loop that checks each objective against real evidence, git awareness, failure recovery, repo-map
context, `AGENT.md` guidelines, and user skills.

Choosing it at the first run is the easy path — that writes a copy with your inference binding
already filled in. Otherwise its stack deliberately names no provider, so add one yourself:

```bash
cp pre-installed/stacks/autonomous.toml ~/.veridian/stacks/autonomous-local.toml
```

```toml
[bindings.inference]
brick = "bricks/inference/local"
config = { model = "your-local-model" }
```

```bash
veridian --stack autonomous-local --workspace /path/to/a/scratch/repo
```

Use a scratch repository. This agent edits files and runs commands.

## Installing bricks and stacks

Bricks you did not write install by name and bind like any built-in:

```bash
uv run veridian brick add https://github.com/someone/their-brick
uv run veridian brick list                       # every discoverable brick and its env status
uv run veridian brick which context/default      # which copy loads, and from which root
uv run veridian stack add ./team-stack.toml
uv run veridian --stack team-stack
```

Installed bricks and stacks live under `VERIDIAN_HOME` (`~/.veridian` by default). Installing a
brick grants it nothing — capabilities come only from the stack policy. See
[`../plugin-development/`](../plugin-development/).

## Swapping a subsystem

Copy a stack file and change one binding line:

```toml
[bindings]
context = "bricks/context/graph"     # was bricks/context/default
```

Start `uv run veridian` again. Nothing else changes — not the kernel, not the other bricks. That
is the whole idea; see [`../../examples/`](../../examples/).
