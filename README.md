# Veridian

An open-source, plugin-native runtime for coding agents.

Every major subsystem — inference, context, memory, planning, sandbox, tools, conversation,
orchestration — is a replaceable **brick**: a subprocess that speaks JSON-RPC 2.0 over stdio
against a stable, schema-defined protocol. The kernel owns process lifecycle, the protocol,
permissions, config, and events. It owns no agent logic.

The claim that matters: **a developer replaces a fundamental subsystem without forking the kernel.**
The kernel contains no assumption about a specific model, provider, inference strategy, or agent
shape. Even the orchestrator — the agent loop itself — is a brick.

```
                    uv run veridian
                          │
                    ┌─────▼─────┐
                    │  KERNEL   │  lifecycle · registry · policy · events · config
                    └─────┬─────┘
                          │  JSON-RPC 2.0 / NDJSON / stdio   (veridian/1.1)
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   orchestrator       inference          context
      brick             brick             brick
        │
        └── host.contract.call ──► kernel ──► planner / memory / tools / sandbox / conversation
```

## Status

**Milestone 1 and Milestone 2 have shipped.** Milestone 1 proved the architecture: the protocol,
plugin runtime, kernel, Python and TypeScript SDKs, a reference brick for every contract, the
conformance harness, and the CLI.

Milestone 2 turned the capability model from a declaration into an enforcement boundary, and added
the privacy Version 0 guarantees:

- **Per-brick dependency isolation** — a brick declares `[dependencies]` and gets its own venv or
  `node_modules`, so two third-party bricks with conflicting requirements coexist.
- **Container spawn mode** — `isolation.mode = "container"` runs a brick inside a container the
  kernel drives, where `network = false` is genuinely `--network none`.
- **Egress control** — a network-capable brick declares an allowlist and reaches nothing else.
- **Cooperative cancellation** — `$/cancel` cascades through `host.contract.call`, so interrupting
  a run actually stops the in-flight model call.
- **Local conversation history** and a check that no inference brick forwards a client identity to
  a provider.

Since then: a full plugin system (install bricks and stacks by name from a path, git URL, or
archive), an interactive terminal UI, and `pre-installed/` — a complete autonomous coding agent
built entirely out of bricks.

Not yet in scope: the marketplace, WASM and Firecracker isolation, remote transport, the desktop
app, and the Rust kernel port. See [`ROADMAP.md`](ROADMAP.md).

## Quick start

```bash
uv sync --extra dev
uv run veridian doctor
uv run veridian                       # interactive session — type a goal, Ctrl-D to exit
```

`uv run veridian` starts the interactive session. It prints the active stack, model, and workspace,
takes a goal at the `›` prompt, and streams the orchestrator's plan, tool calls, and output. `/help`
lists the session commands. Ctrl-C interrupts a running goal without killing the session; Ctrl-D
exits.

For a single goal in a script or a CI step, `uv run veridian run "add a docstring and run the
tests"` does the same thing once and exits with a status code.

Either form needs an inference provider. Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, or point a
stack at a reachable OpenAI-compatible server (Ollama, llama.cpp, vLLM, LM Studio). With every
provider variable unset, the kernel and the full hermetic test suite still pass — model-agnosticism
is structural, not a configuration option.

See [`docs/getting-started/`](docs/getting-started/) for the full first run, including sessions,
`@file` mentions, and `--resume`.

## Installing bricks and stacks

A brick you did not write installs by name and binds like any built-in:

```bash
uv run veridian brick add https://github.com/someone/their-brick
uv run veridian brick which context/default        # which copy would load, and from where
uv run veridian stack add ./team-stack.toml
uv run veridian --stack team-stack
```

Installed bricks and stacks live under `VERIDIAN_HOME` (`~/.veridian` by default). Installing
grants a brick nothing — capabilities come only from the stack policy and its `[isolation]` table.
See [`docs/plugin-development/`](docs/plugin-development/).

## Verifying the milestones

```bash
uv run pytest -m "not live"              # hermetic suite, passes with no provider configured
uv run python scripts/milestone1.py      # the eight Milestone 1 criteria, pass/fail each
uv run python scripts/milestone2.py      # the ten Milestone 2 criteria
```

Milestone 1's criteria: swap inference, context, and sandbox without touching the kernel;
cross-language bricks (a TypeScript brick beside Python ones); crash isolation; manifest
validation; protocol-only communication; and the kernel running with no provider configured.

Milestone 2's criteria need a container engine on `PATH` for the two isolation ones; they report
as skipped otherwise.

## Layout

| Path | What lives there |
|---|---|
| `schemas/` | JSON Schema — the source of truth for the wire format. |
| `docs/specifications/protocol.md` | The protocol every brick obeys. |
| `src/veridian/contracts/` | Pydantic models validated against the schemas. |
| `src/veridian/plugin_runtime/` | Transport, IPC codec, process supervision, manifest, discovery, acquisition, the binding table. |
| `src/veridian/kernel/` | Runtime, lifecycle, events, permissions, config. Small on purpose. |
| `src/veridian/security/` | Capability resolution, policy, the egress proxy. |
| `src/veridian/cli/` | The CLI, the interactive REPL, and the terminal UI. |
| `src/veridian/sdk/` | Python brick SDK. |
| `sdks/typescript/` | TypeScript brick SDK. |
| `bricks/` | Reference bricks — one per contract, showing what each contract means. |
| `pre-installed/` | The shipped product layer: a complete autonomous coding agent, built only from bricks. |
| `stacks/` | Stack files that bind contracts to bricks. |
| `examples/` | Minimal bricks that each replace one subsystem, including a whole alternative agent architecture. |
| `tests/conformance/` | The harness that validates any brick against the schemas. |

## License

Apache 2.0. See [`LICENSE`](LICENSE). The patent grant is deliberate: an open plugin ecosystem
wants it.
