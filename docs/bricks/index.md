# Reference bricks

Every contract ships with at least one reference brick under `bricks/`. Run
`uv run veridian brick list` for the live table.

| Contract | Bricks | Notes |
|---|---|---|
| `inference` | `inference/openai`, `inference/anthropic`, `inference/local`, `inference/routing` | Real providers only. `local` is the OpenAI-compatible adapter pointed at a local server. `routing` picks a provider per request by rule. |
| `model_provider` | `inference/openai`, `inference/local` | The raw `complete` / `embed` adapter. Anthropic has no embeddings endpoint, so `inference/anthropic` does not implement this. |
| `context` | `context/default` (glob + keyword), `context/graph` (stdlib `ast`), `context/vector` (embeddings → SQLite → cosine), `context/semantic` (keyword recall + inference rerank) | `vector` and `semantic` degrade to keyword ranking when no provider is reachable. |
| `memory` | `memory/ephemeral` (in-process), `memory/vector` (SQLite + embeddings), `memory/graph` (SQLite node + edge tables) | |
| `planner` | `planning/default` (linear), `planning/recursive` (lazy sub-step expansion), `planning/tree-search` (best-scoring branch) | |
| `sandbox` | `sandbox/local` | Working-directory confinement, timeouts, output caps. Not an OS sandbox in Milestone 1. |
| `tools` | `tools/filesystem`, `tools/terminal` (delegates to `sandbox`), `tools/git` (**TypeScript**), `tools/browser` (Playwright optional) | |
| `orchestrator` | `orchestrator/default` | The agent loop. Replace it to replace the agent architecture. |

## Writing your own

See [`../plugin-development/`](../plugin-development/). The short version: subclass `Brick`
(Python) or call `serve({...})` (TypeScript), implement the contract methods, ship a
`veridian.toml`, and pass `uv run veridian brick conformance <dir>`.
