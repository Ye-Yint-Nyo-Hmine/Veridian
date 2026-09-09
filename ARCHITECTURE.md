# Architecture

Veridian is a kernel plus a set of subprocesses ("bricks") that speak one schema-defined
protocol. The kernel owns process lifecycle, the protocol, permissions, config, and events. It
owns no agent logic and contains no assumption about a model, provider, inference strategy, or
agent shape.

```
             veridian run "fix the failing test"
                          │
                    ┌─────▼─────┐
                    │  KERNEL   │  lifecycle · registry · policy · events · config
                    └─────┬─────┘
                          │  JSON-RPC 2.0 / NDJSON / stdio   (veridian/1.0)
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   orchestrator       inference          context
      brick             brick             brick
        │
        └── host.contract.call ──► kernel ──► planner / memory / tools / sandbox
```

## The kernel (`src/veridian/kernel/`)

| Module | Responsibility |
|---|---|
| `runtime.py` | The `Kernel` object. Starts bricks, binds contracts, routes `host.contract.call`, enforces capabilities at the host-service boundary. Deliberately small. |
| `lifecycle.py` | `BrickSupervisor` — spawn → `plugin.initialize` → `plugin.capabilities` → cross-check; health via `plugin.ping`; crash detection; restart with exponential backoff and a windowed budget. |
| `events.py` | Async pub/sub bus with exact and `prefix.*` subscriptions; bounded history. |
| `config.py` | Loads one stack TOML, schema-validates it, resolves every binding to a real manifest, builds the `Policy`. |
| `errors.py` | Kernel-level exceptions (wire errors live in `contracts/errors.py`). |

The kernel knows contract *names* as opaque strings. It never imports a brick; a test enforces
this.

## The plugin runtime (`src/veridian/plugin_runtime/`)

| Module | Responsibility |
|---|---|
| `transport.py` | Framing only. `Transport` ABC + `StdioTransport` (NDJSON). Future socket/pipe/remote transports subclass it. |
| `ipc.py` | The JSON-RPC 2.0 codec: request/response correlation, per-call timeouts, `call_stream` with request-id-keyed delta routing, background inbound dispatch, malformed-line quarantine with a crash threshold. Peer-symmetric — the SDK uses the same `Endpoint`. |
| `process.py` | `BrickProcess` over `asyncio.create_subprocess_exec`. `terminate()` then hard `kill()` after a timeout — no POSIX signals. `stderr` drained line-by-line to a sink, never parsed. Environment scrubbed to an allowlist. |
| `manifest.py` | Parse + schema-validate `veridian.toml` → frozen `Manifest`. |
| `loader.py` | Discover bricks (`discover` / `discover_with_errors`); resolve a stack reference by path or manifest name. |
| `registry.py` | The contract → brick binding table + `cross_check_capabilities`: a brick whose manifest claims a contract method its running code doesn't report is refused. |

## Security (`src/veridian/security/`)

Effective capabilities = **what the manifest declares ∩ what policy grants − what policy denies**.
Enforced at two boundaries: the host-service boundary (`host.contract.call` and friends) and
process spawn (env scrubbing, working-directory confinement). Milestone 1 does **not** provide
OS-level network or filesystem isolation — see [`SECURITY.md`](SECURITY.md). That is what the
sandbox brick and the roadmap's container/WASM modes are for.

## Bricks (`bricks/`)

A brick is a directory with a `veridian.toml` and a spawn command. It is **not** an importable
module. It implements one or more contracts and may call other bricks only through
`host.contract.call`. Reference bricks ship for every contract; `bricks/tools/git` is written in
TypeScript to prove the protocol is language-neutral.

## The protocol

Normative spec: [`docs/specifications/protocol.md`](docs/specifications/protocol.md). Source of
truth for every payload: the JSON Schemas in [`schemas/`](schemas/). The Python types in
`src/veridian/contracts/` and the TypeScript types in `sdks/typescript/` are validated against the
schemas; they are never authoritative. A future Rust kernel would be validated against
`tests/conformance/`.

## Why the orchestrator is a brick

The agent loop — retrieve context, plan, generate with tools, dispatch, observe, repeat — lives in
`bricks/orchestrator/default`. It holds no reference to any other brick; it calls `context`,
`planner`, `inference`, `tools`, and `sandbox` through `host.contract.call`. Because the binding is
one line in a stack file, replacing the orchestrator replaces the agent architecture. That is the
claim Veridian exists to make true, and `examples/custom-agent-runtime` is the proof.
