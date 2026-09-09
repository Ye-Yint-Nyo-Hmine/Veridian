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

## Your first run

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # or OPENAI_API_KEY, or a local server (see below)
uv run veridian stack validate stacks/default.toml
uv run veridian run "add a docstring to the top-level function and run the tests" \
  --workspace /path/to/a/scratch/repo
```

`veridian run` streams the orchestrator's steps, tool calls, and messages, then prints the final
status and summary.

## Running fully local

`stacks/local-only.toml` points `inference` and `model_provider` at an OpenAI-compatible server
(Ollama, llama.cpp, vLLM, LM Studio):

```bash
export VERIDIAN_LOCAL_BASE_URL=http://localhost:11434/v1
uv run veridian run "..." --stack stacks/local-only.toml
```

Pull the models the stack names (`llama3.2`, `nomic-embed-text`) or edit the `config` in the stack
file.

## Swapping a subsystem

Copy a stack file and change one binding line:

```toml
[bindings]
context = "bricks/context/graph"     # was bricks/context/default
```

Run `veridian run` again. Nothing else changes — not the kernel, not the other bricks. That is the
whole idea; see [`../../examples/`](../../examples/).
