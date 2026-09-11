# Architecture

The full picture lives in [`/ARCHITECTURE.md`](../../ARCHITECTURE.md). This page is the quick map.

- **Kernel** (`src/veridian/kernel/`) — lifecycle, registry, policy, events, config. No agent
  logic. No knowledge of any model or agent shape.
- **Plugin runtime** (`src/veridian/plugin_runtime/`) — transport, JSON-RPC codec, process
  supervision and spawn strategies, manifest parsing, discovery, acquisition, versioning, the
  binding table.
- **Security** (`src/veridian/security/`) — capability strings, operator policy, runtime
  permission checks, the egress proxy, advisory trust assessment.
- **Contracts** (`src/veridian/contracts/`) — Pydantic models and a method registry, all
  validated against `schemas/`.
- **CLI** (`src/veridian/cli/`) — commands, the interactive REPL, and the terminal UI. Renders
  from protocol events only; it imports nothing from `bricks/`.
- **SDKs** (`src/veridian/sdk/`, `sdks/typescript/`) — thin brick-author libraries.
- **Bricks** (`bricks/`) — a directory + manifest + spawn command per subsystem implementation.
- **Pre-installed** (`pre-installed/`) — the shipped agent, assembled only from bricks.

Data flow for one goal (interactive `uv run veridian`, or one-shot `veridian run`):

1. CLI resolves a stack, starts the `Kernel`, calls `orchestrator.run` (streaming).
2. The orchestrator brick calls `context.retrieve`, `planner.plan`, `inference.generate`,
   `tools.invoke`, `sandbox.exec`, and `conversation.append` — all via `host.contract.call`.
3. The kernel checks the caller's effective capabilities, routes each call to the bound brick,
   and returns the result.
4. Deltas stream back to the CLI as `orchestrator.delta` notifications, which drive the running UI
   and the context-usage meter.
5. Ctrl-C sends `$/cancel`, which cascades through `host.contract.call` to whatever downstream call
   is in flight.

Key invariant: the kernel routes by contract name only. Swap the brick behind a name and the same
kernel code path runs.

Session identity is one id, not three: the id shown on the boot screen is the key the conversation
brick stores history under and the key `veridian --resume` takes.
