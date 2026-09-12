# Reference bricks

Every contract ships with at least one reference brick under `bricks/`. Run
`uv run veridian brick list` for the live table, and `uv run veridian brick which <name>` to see
which copy of a brick would actually load.

| Contract | Bricks | Notes |
|---|---|---|
| `inference` | `inference/openai`, `inference/anthropic`, `inference/local`, `inference/routing` | Real providers only. `openai` is the OpenAI chat-completions adapter, so it also fronts **Gemini, DeepSeek and Moonshot (Kimi)** — set `base_url` and `api_key_env` in the binding. `local` is the same adapter pointed at a local server. `routing` picks a provider per request by rule. |
| `model_provider` | `inference/openai`, `inference/local`, `model-provider/gateway` | The raw `complete` / `embed` adapter. Anthropic has no embeddings endpoint, so `inference/anthropic` does not implement this. `gateway` is the OpenAI-compatible adapter pointed at a Veridian gateway with a bearer token. |
| `context` | `context/default` (glob + keyword), `context/graph` (stdlib `ast`), `context/vector` (embeddings → SQLite → cosine), `context/semantic` (keyword recall + inference rerank) | `vector` and `semantic` degrade to keyword ranking when no provider is reachable. |
| `memory` | `memory/ephemeral` (in-process), `memory/vector` (SQLite + embeddings), `memory/graph` (SQLite node + edge tables) | |
| `conversation` | `conversation/sqlite` | An append-only turn log in one local SQLite file, keyed by session id. Distinct from `memory`, and never leaves the device. |
| `planner` | `planning/default` (linear), `planning/recursive` (lazy sub-step expansion), `planning/tree-search` (best-scoring branch) | |
| `sandbox` | `sandbox/local` | Working-directory confinement, timeouts, output caps. Not an OS sandbox on its own — see the container isolation mode in [`../security/`](../security/). |
| `tools` | `tools/filesystem`, `tools/terminal` (delegates to `sandbox`), `tools/git` (**TypeScript**), `tools/browser` (Playwright optional) | A stack binds one brick per contract, so these are alternatives, not a set you combine. |
| `orchestrator` | `orchestrator/default` | The agent loop. Replace it to replace the agent architecture. |

### Pointing one adapter at another provider

Gemini, DeepSeek and Moonshot all serve the OpenAI chat-completions API, so they need no brick of
their own — only a base URL and the name of the variable holding the key:

```toml
[bindings.inference]
brick = "bricks/inference/openai"
config = { model = "deepseek-chat", base_url = "https://api.deepseek.com/v1", api_key_env = "DEEPSEEK_API_KEY" }
```

`api_key_env` names the one variable the brick reads; nothing falls back to `OPENAI_API_KEY`, so a
key for one provider is never sent to another. The variable must also appear in the brick's
manifest `env_passthrough` or the kernel scrubs it before the brick starts — `inference/openai`
lists the four it supports. The CLI reads the same field to warn about a missing key before the
first goal rather than after, which is why a local binding, declaring none, never warns.

## The pre-installed agent

`pre-installed/` is the shipped product layer: a complete autonomous coding agent assembled only
from bricks, with no kernel involvement. It is deliberately separate from `bricks/`, which exists
to show what each contract means rather than to be a finished product.

| Brick | Contract | What it does |
|---|---|---|
| `tools/toolkit` | `tools` | Filesystem, terminal via the sandbox contract, git, and user skills — all in one brick, because a stack binds one brick per contract. |
| `context/repo-map` | `context` | A compact repository map, keyword-ranked selective retrieval, symbol outlines for large files, and `AGENT.md` loading from both the user and workspace locations. |
| `orchestrator/autonomous` | `orchestrator` | Inspect, plan objectives, edit, run, verify each objective against real evidence, recover from failures, compact context. |

`pre-installed/stacks/autonomous.toml` binds them. It deliberately names no provider, no model id,
and no base URL — add an inference binding before running it. See
[`../../pre-installed/README.md`](../../pre-installed/README.md).

## Writing your own

See [`../plugin-development/`](../plugin-development/). The short version: subclass `Brick`
(Python) or call `serve({...})` (TypeScript), implement the contract methods, ship a
`veridian.toml`, and pass `uv run veridian brick conformance <dir>`.
