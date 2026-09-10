# Veridian

An open-source, plugin-native runtime for coding agents.

Every major subsystem — inference, context, memory, planning, sandbox, tools, orchestration —
is a replaceable **brick**: a subprocess that speaks JSON-RPC 2.0 over stdio against a stable,
schema-defined protocol. The kernel owns process lifecycle, the protocol, permissions, config,
and events. It owns no agent logic.

The claim that matters: **a developer replaces a fundamental subsystem without forking the kernel.**
The kernel contains no assumption about a specific model, provider, inference strategy, or agent
shape. Even the orchestrator — the agent loop itself — is a brick.

```
             veridian run "fix the failing test"
                          │
                    ┌─────▼─────┐
                    │  KERNEL   │  lifecycle · registry · policy · events · config
                    └─────┬─────┘
                          │  JSON-RPC 2.0 / NDJSON / stdio
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   orchestrator       inference          context
      brick             brick             brick
        │
        └── host.contract.call ──► kernel ──► planner / memory / tools / sandbox
```

## Status

Milestone 1. The protocol, plugin runtime, kernel, Python and TypeScript SDKs, a full set of
reference bricks, the conformance harness, and the CLI are in place. Not yet in scope: the
marketplace, container/WASM isolation, remote transport, the desktop app, and the Rust kernel port.
See [`ROADMAP.md`](ROADMAP.md).

## Quick start

```bash
uv sync --extra dev
uv run veridian doctor
uv run veridian stack validate stacks/default.toml
uv run veridian                       # interactive session — type a goal, Ctrl-D to exit
```

`uv run veridian` starts the interactive session: it prints the active stack and workspace, takes
a goal at the prompt, and streams the orchestrator's plan, steps, tool calls, and output. For a
one-shot / scriptable run, `uv run veridian run "add a docstring and run the tests"` does the same
for a single goal and exits with a status code.

Either form needs at least one inference provider. Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`,
or point `stacks/local-only.toml` at a reachable OpenAI-compatible server (Ollama, llama.cpp,
vLLM, LM Studio). With every provider variable unset, the kernel and the full hermetic test suite
still pass — model-agnosticism is structural.

## Verifying Milestone 1

```bash
uv run pytest -m "not live"              # hermetic suite
uv run python scripts/milestone1.py      # walks the eight Milestone 1 criteria, pass/fail each
```

The eight criteria: swap inference / context / sandbox without touching the kernel; cross-language
bricks (a TypeScript brick beside Python ones); crash isolation; manifest validation; protocol-only
communication (`veridian brick conformance --all`); and the kernel running with no provider
configured.

## Layout

| Path | What lives there |
|---|---|
| `schemas/` | JSON Schema — the source of truth for the wire format. |
| `docs/specifications/protocol.md` | The protocol every brick obeys. |
| `src/veridian/contracts/` | Pydantic models validated against the schemas. |
| `src/veridian/plugin_runtime/` | Transport, IPC codec, process supervision, manifest, loader, registry. |
| `src/veridian/kernel/` | Runtime, lifecycle, events, permissions, config. Small on purpose. |
| `src/veridian/sdk/` | Python brick SDK. |
| `sdks/typescript/` | TypeScript brick SDK. |
| `bricks/` | Reference bricks. Directories with a manifest and a spawn command, not importable modules. |
| `stacks/` | Stack files that bind contracts to bricks. |
| `tests/conformance/` | The harness that validates any brick against the schemas. |

## License

Apache 2.0. See [`LICENSE`](LICENSE). The patent grant is deliberate: an open plugin ecosystem
wants it.
