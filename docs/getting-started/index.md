# Getting started

## Install

```bash
uv sync --extra dev
# for the inference bricks and their live tests:
uv sync --extra dev --extra providers
```

## Check your environment

```bash
uv run veridian doctor
```

Everything except "an inference provider" should be `ok`. The kernel and the whole hermetic test
suite run with no provider configured.

## Your first session

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # or OPENAI_API_KEY, or a local server (see below)
uv run veridian stack validate stacks/default.toml
uv run veridian --workspace /path/to/a/scratch/repo
```

`uv run veridian` starts the interactive session. It prints the active stack, the inference
binding, and the workspace, then waits at a `›` prompt. Type a goal and it streams the
orchestrator's plan, active steps, tool calls, and model output, then a final status line. `/help`
lists the session commands (it is rendered from the command registry, so it can't drift);
Ctrl-C interrupts a running goal; Ctrl-D exits.

Session commands: `/stack`, `/workspace`, `/mode` (cycle plan ↔ auto — plan enforces
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
uv run veridian run "add a docstring to the top-level function and run the tests" \
  --workspace /path/to/a/scratch/repo
```

It exits `0` when the run completes, non-zero otherwise.

## Running fully local

`stacks/local-only.toml` points `inference` and `model_provider` at an OpenAI-compatible server
(Ollama, llama.cpp, vLLM, LM Studio):

```bash
export VERIDIAN_LOCAL_BASE_URL=http://localhost:11434/v1
uv run veridian --stack stacks/local-only.toml
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

Its stack deliberately names no provider, so add an inference binding before running it:

```bash
cp pre-installed/stacks/autonomous.toml stacks/autonomous-local.toml
```

```toml
[bindings.inference]
brick = "bricks/inference/local"
config = { model = "your-local-model" }
```

```bash
uv run veridian --stack stacks/autonomous-local.toml --workspace /path/to/a/scratch/repo
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
