# Veridian Protocol Specification

**Status:** normative for Milestone 1. **Protocol version:** `veridian/1.1`.

> **1.0 → 1.1.** Adds optional cancellation: the `$/cancel` notification (§5.1) and the
> `request_cancelled` error (`-32009`). Method contracts and framing are unchanged. A `veridian/1.0`
> peer is fully compatible — it never sends `$/cancel` and ignores one it receives, so a cancelled
> call against it simply runs to its timeout.

This document defines how the Veridian kernel and a brick communicate. The JSON Schema files under
`schemas/` are the source of truth for every payload shape. Where prose here and a schema disagree,
the schema wins. Language-specific types (Python, TypeScript) are conveniences validated against the
schemas; they are never authoritative.

---

## 1. Transport and framing

A brick is a subprocess. The kernel spawns it and speaks to it over its standard streams.

- **`stdin` / `stdout`** carry the protocol and nothing else.
- **`stdout`** is **newline-delimited JSON (NDJSON)**: UTF-8, exactly one JSON-RPC 2.0 message per
  line, terminated by `\n`. JSON escapes literal newlines inside strings, so a raw `\n` byte always
  ends a message. A line that is empty or whitespace-only is ignored.
- **`stderr`** is the brick's log stream. It is never parsed as protocol. The kernel captures it
  line by line and republishes each line on the event bus as a `brick.stderr` event. A brick may
  therefore `print()` to `stderr` freely; writing non-protocol bytes to `stdout` is a
  protocol violation and the kernel MAY quarantine the brick.

### 1.1 Framing is versioned

The negotiated protocol version (`veridian/<major>.<minor>`) names *both* the method contracts
*and* the framing (`NDJSON over stdio`). A future major version may define length-prefixed frames
or a socket transport without changing any method contract.

Peers negotiate by **major version only**. An implementation MUST reject a peer whose major
version differs (`protocol_version_mismatch`, code `-32008`) and MUST accept one whose major
version matches, whatever its minor. A minor version only *adds* optional messages; a peer that
does not implement an addition MUST ignore it rather than fault. `plugin.initialize` still
exchanges the full `veridian/<major>.<minor>` string in both directions so each side can detect
which optional features the other supports.

### 1.2 Message quarantine

If the kernel reads a line from `stdout` that is not valid JSON, or is valid JSON but not a
well-formed JSON-RPC message, it MUST NOT crash. It emits a `brick.malformed_line` event carrying
the offending bytes (truncated) and continues reading. A brick that emits a configurable threshold
of malformed lines in a row is treated as crashed and handed to the restart policy.

---

## 2. Message types

All messages are JSON-RPC 2.0. The envelope schema is `schemas/protocol/envelope.schema.json`.

### 2.1 Request

```json
{ "jsonrpc": "2.0", "id": 42, "method": "inference.generate", "params": { ... } }
```

`id` is a JSON integer or string, unique for the lifetime of the connection on the sender's side.
`params` is always an object (Veridian does not use positional params).

### 2.2 Success response

```json
{ "jsonrpc": "2.0", "id": 42, "result": { ... } }
```

### 2.3 Error response

```json
{ "jsonrpc": "2.0", "id": 42, "error": { "code": -32001, "message": "permission denied", "data": { ... } } }
```

### 2.4 Notification

A message with no `id`. Used for streaming (§5), cancellation (§5.1), and for fire-and-forget
host calls (`host.log`). A notification never receives a response. Method names beginning `$/` are
framing-level control messages reserved by the protocol; they carry no contract payload.

```json
{ "jsonrpc": "2.0", "method": "inference.delta", "params": { "request_id": 42, "delta": { ... } } }
```

---

## 3. Direction of calls

- **Kernel → brick:** every method in §4 (lifecycle) and every contract method the brick
  implements (§6).
- **Brick → kernel:** the `host.*` services in §4.2, sent by the brick on its `stdout` as ordinary
  JSON-RPC requests (or, for `host.log`, a notification). The kernel replies on the brick's
  `stdin`.

Both peers may have in-flight requests at the same time. Correlation is by `id` and by
originating side; a brick's request `id` space and the kernel's request `id` space are independent.

---

## 4. Lifecycle and host services

### 4.1 Lifecycle methods (kernel → brick)

Schema: `schemas/protocol/lifecycle.schema.json`. Every brick MUST implement all four.

| Method | Params | Result |
|---|---|---|
| `plugin.initialize` | `{ protocol_version, capabilities, workspace_root, config }` | `{ protocol_version, brick: { name, version }, ready }` |
| `plugin.capabilities` | `{}` | `{ contracts: [ { name, version, methods } ] }` |
| `plugin.ping` | `{ nonce? }` | `{ nonce? }` (echoes the nonce if given) |
| `plugin.shutdown` | `{}` | `{}` — the brick MUST exit its process promptly after responding |

`plugin.initialize` is always the first message the kernel sends. `capabilities` in its params is
the set of capability strings the kernel has **granted** this brick (the intersection of the
manifest's request and policy — see `SECURITY.md`). `config` is the brick's config table from the
active stack file. A brick that cannot operate under the given protocol version or capabilities
MUST respond with `ready: false` and MAY include a reason in an error instead.

### 4.2 Host services (brick → kernel)

Schema: `schemas/protocol/host.schema.json`.

| Method | Params | Result | Notes |
|---|---|---|---|
| `host.log` | `{ level, message, fields? }` | — | Sent as a **notification**. `level` ∈ `debug info warning error`. |
| `host.event.emit` | `{ type, payload? }` | `{}` | Publishes a custom event on the bus. `type` is namespaced by the brick. |
| `host.permission.request` | `{ capability, reason? }` | `{ granted }` | Asks policy for a capability not in the initial grant. Milestone 1 policy answers from config; no interactive prompt. |
| `host.contract.call` | `{ contract, method, params }` | `{ result }` \| error | **The core routing primitive** — see §7. |

A brick MUST NOT call a host service before it has received `plugin.initialize`.

---

## 5. Streaming

Streaming responses use **notifications keyed by the originating request id**.

When a brick handles a streaming method (e.g. `inference.generate_stream`, request `id: 42`) it:

1. emits zero or more notifications whose `method` is `<contract>.delta` and whose `params` contain
   `request_id: 42` plus a `delta` payload defined by that contract's schema;
2. finally sends the ordinary success response for `id: 42` carrying the aggregated final result.

The receiver correlates deltas to the call by `params.request_id`. Deltas that arrive after the
final response, or for an unknown `request_id`, are dropped with a `brick.orphan_delta` event.

`orchestrator.run` uses the same mechanism with method `orchestrator.delta` and an `event` tag on
each delta (`step`, `tool`, `message`, `log`).

### 5.1 Cancellation (`veridian/1.1`)

Either peer MAY abandon a request it originated by sending a **`$/cancel` notification** naming
that request's id:

```json
{ "jsonrpc": "2.0", "method": "$/cancel", "params": { "id": 42 } }
```

- `$/cancel` is fire-and-forget; it never receives a response. It is valid only for a request the
  **sender** originated and that has not yet been answered. A `$/cancel` for an unknown or
  already-settled id is a no-op.
- On receipt, the peer SHOULD stop work on request `42` promptly and SHOULD answer it with an
  error response of code `-32009` (`request_cancelled`). It MAY still answer with a normal result
  if the work had already completed. The originator, having sent `$/cancel`, MUST tolerate either
  a `request_cancelled` error, a late success, or no response at all.
- **Cascade.** When the cancelled request is one the kernel is serving via `host.contract.call`
  (§7), the kernel MUST send `$/cancel` for the downstream request it issued on the caller's
  behalf, and so on down the chain. Cancelling `orchestrator.run` therefore also cancels the
  `inference` call the orchestrator was blocked on, and any `model_provider` call under that.
- A `veridian/1.0` peer never sends `$/cancel` and ignores one it receives. Against such a peer a
  cancelled call stops on the *originator's* side but runs on the peer until the call's timeout
  (`-32006`) — the pre-1.1 behaviour.

---

## 6. Contracts

One schema file per contract under `schemas/protocol/`, one module per contract under
`src/veridian/contracts/`. Shared shapes (`Message`, `ContentBlock`, `Usage`, `ToolSchema`) live in
`schemas/protocol/common.schema.json`.

| Contract | Methods | Streaming method |
|---|---|---|
| `inference` | `generate`, `generate_stream`, `models` | `inference.delta` |
| `model_provider` | `complete`, `embed` | — |
| `context` | `index`, `retrieve`, `invalidate` | — |
| `memory` | `write`, `read`, `search`, `forget` | — |
| `conversation` | `append`, `load`, `list_sessions`, `delete` | — |
| `planner` | `plan`, `next`, `is_complete` | — |
| `sandbox` | `exec`, `spawn`, `write`, `kill`, `reset` | — |
| `tools` | `list`, `invoke` | — |
| `workspace` | `read`, `write`, `list`, `stat` | — |
| `orchestrator` | `run` | `orchestrator.delta` |

> **Additive in Milestone 2.** `conversation` was added after `veridian/1.1` shipped. A new
> contract adds only a schema file and a binding name; it changes no framing and no existing
> method, so it needs no version bump. A kernel or brick that does not know the contract simply
> never binds it (`contract_not_bound`, `-32002`).

`inference` and `model_provider` are deliberately distinct. A `model_provider` adapts exactly one
vendor API (`complete`, `embed`). An `inference` engine is a *strategy* — it may route, ensemble,
or tree-search across several `model_provider` bricks by calling them through
`host.contract.call`. This split is why model-agnosticism is structural and not a config switch:
the kernel knows only "something implements `inference`".

A brick MAY implement more than one contract. It MUST report every contract and method it
implements from `plugin.capabilities`, and the registry cross-checks that list against the
manifest's `implements` before binding (§8).

Calling a method a brick does not implement returns `unsupported_method` (`-32003`).

---

## 7. `host.contract.call` — routing

This is the single most important method in the protocol. A brick never holds a reference to
another brick.

1. Brick A sends `host.contract.call` with `{ contract: "memory", method: "search", params: {...} }`.
2. The kernel checks that A's effective capabilities include `contract:memory` (or
   `contract:memory:search` for method-scoped grants). If not → `permission_denied` (`-32001`).
3. The kernel looks up the brick bound to `memory` in the active stack. If nothing is bound →
   `contract_not_bound` (`-32002`).
4. The kernel forwards `memory.search` to brick B, awaits the response (subject to the call
   timeout), and returns `{ result: <B's result> }` to A. A `-32003` from B is passed through.
5. If B is mid-restart or has exited → `brick_unavailable` (`-32005`); if the call exceeds its
   timeout → `timeout` (`-32006`).

Because routing is a kernel service and the binding is just a line in a stack file, the
orchestrator — which is only a brick that calls `context`, `planner`, `inference`, `tools`, and
`sandbox` through this method — is itself replaceable. Replacing it replaces the agent
architecture.

---

## 8. Error codes

Standard JSON-RPC:

| Code | Meaning |
|---|---|
| `-32700` | Parse error (invalid JSON on the wire) |
| `-32600` | Invalid request (not a well-formed JSON-RPC message) |
| `-32601` | Method not found (unknown method name) |
| `-32602` | Invalid params (failed schema validation) |
| `-32603` | Internal error |

Veridian reserved range (`-32000` … `-32099`, permitted by JSON-RPC for implementation-defined
server errors):

| Code | Symbol | Meaning |
|---|---|---|
| `-32001` | `permission_denied` | Caller lacks the required effective capability |
| `-32002` | `contract_not_bound` | No brick is bound to the requested contract in the active stack |
| `-32003` | `unsupported_method` | Target brick does not implement this optional method |
| `-32004` | `brick_internal_error` | Target brick raised while handling the call |
| `-32005` | `brick_unavailable` | Target brick is not running (crashed, restarting, or shut down) |
| `-32006` | `timeout` | Call exceeded its deadline |
| `-32007` | `invalid_manifest` | Manifest failed schema validation or lied about `implements` |
| `-32008` | `protocol_version_mismatch` | Peer's protocol major version is incompatible |
| `-32009` | `request_cancelled` | Request was abandoned by its originator via `$/cancel` (§5.1) before it completed |

`error.data`, when present, is an object. For validation errors it carries `{ schema, path,
detail }`.

---

## 9. Conformance

A brick conforms to `veridian/1.1` when, for every contract it reports from
`plugin.capabilities`:

- every request the kernel sends validates against that contract's params schema, and
- every response and delta the brick sends validates against the corresponding result / delta
  schema, and
- lifecycle and error semantics in §4, §5, and §8 hold.

Honouring `$/cancel` (§5.1) is **not** required for conformance — a conformant brick MAY ignore it
and be driven to its timeout instead. A brick that does act on `$/cancel` MUST still end the
cancelled request with a valid message (a `request_cancelled` error or a late valid result).

`tests/conformance/` is the executable form of this section and any future kernel (including a
Rust port) is validated against it.
