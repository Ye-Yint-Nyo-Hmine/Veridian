# Architecture

The full picture lives in [`/ARCHITECTURE.md`](../../ARCHITECTURE.md). This page is the quick map.

- **Kernel** (`src/veridian/kernel/`) — lifecycle, registry, policy, events, config. No agent
  logic. No knowledge of any model or agent shape.
- **Plugin runtime** (`src/veridian/plugin_runtime/`) — transport, JSON-RPC codec, process
  supervision, manifest parsing, discovery, the binding table.
- **Security** (`src/veridian/security/`) — capability strings, operator policy, runtime
  permission checks, advisory trust assessment.
- **Contracts** (`src/veridian/contracts/`) — Pydantic models and a method registry, all
  validated against `schemas/`.
- **SDKs** (`src/veridian/sdk/`, `sdks/typescript/`) — thin brick-author libraries.
- **Bricks** (`bricks/`) — a directory + manifest + spawn command per subsystem implementation.

Data flow for one `veridian run`:

1. CLI loads a stack, starts the `Kernel`, calls `orchestrator.run` (streaming).
2. The orchestrator brick calls `context.retrieve`, `planner.plan`, `inference.generate`,
   `tools.invoke`, `sandbox.exec` — all via `host.contract.call`.
3. The kernel checks the caller's effective capabilities, routes each call to the bound brick,
   and returns the result.
4. Deltas stream back to the CLI as `orchestrator.delta` notifications.

Key invariant: the kernel routes by contract name only. Swap the brick behind a name and the same
kernel code path runs.
