# Roadmap

Milestone 1 (this release) ships the kernel, the `veridian/1.0` protocol, the plugin runtime,
Python and TypeScript SDKs, a reference brick for every contract, the conformance harness, and the
CLI. Everything below is explicitly **out of scope for Milestone 1** and noted here so the
boundaries it affects already have the right shape.

## Transport

- **Unix domain socket / Windows named pipe transport.** `Transport` is an ABC; `StdioTransport`
  is the only M1 implementation. A new transport drops in without touching `ipc.py` or callers.
- **Remote transport** (a brick running on another host). The negotiated protocol version names
  the framing, so a length-prefixed or TLS framing can ship under a new version string without
  changing any method contract.

## Isolation

- **Per-brick dependency isolation** — *landed in Milestone 2.* A brick with a `[dependencies]`
  manifest table is resolved into its own venv / `node_modules` on `veridian brick install`
  instead of sharing the kernel's interpreter.
- **Container isolation** (Docker / Podman) as a brick-spawn mode — *landed in Milestone 2.*
  A manifest `[isolation]` table with `mode = "container"` makes the kernel spawn the brick inside
  a container it drives (`docker run` / `podman run`), with the workspace mounted read-write and
  the brick read-only. `network = false` is `--network none`; `network = true` with `allow_hosts`
  is an egress allowlist enforced at the container boundary.
- **WASM isolation** for bricks that can be compiled to WASI.
- **Firecracker microVMs** for untrusted bricks.

Until then the capability model is enforced at the host-service boundary and at spawn only; a
plain subprocess is not a jail. See [`SECURITY.md`](SECURITY.md).

## Privacy (Version 0)

- **Local conversation history** — *landed in Milestone 2.* The `conversation` contract
  (`append` / `load` / `list_sessions` / `delete`) plus `bricks/conversation/sqlite`: an
  append-only turn log in one local SQLite file, distinct from `memory`. Additive — no wire
  version bump.
- **Identity stripping in provider adapters** — *landed in Milestone 2.* A conformance check
  drives every inference brick against a capture server and asserts no stable client identity
  reaches the provider; a planted leaky adapter proves the check bites.
- **Gateway client** — *landed in Milestone 2.* `bricks/model-provider/gateway` is the
  OpenAI-compatible adapter with a gateway base URL and a bearer token. The gateway **service**
  (`veridian-gateway`) stays in its own repository, like the marketplace.
- **Data-flow documentation** — *landed in Milestone 2.* `docs/security/privacy.md`: the
  persistent/ephemeral boundary table and the three structural limits the design does not claim
  past.
- **Client-side encryption (V1)**, **TEE inference (V2)**, **remote attestation + E2EE (V3)** —
  future milestones. See `docs/security/privacy.md` for why each only helps once the trust set
  actually changes.

## Kernel

- **Rust kernel port.** The protocol is specified as JSON Schema plus the language-neutral
  conformance suite in `tests/conformance/` precisely so a second kernel implementation can be
  validated against the same bar. No Python type is allowed to leak into the wire format.
- **Rust brick SDK.**

## Ecosystem

- **The marketplace** — brick discovery, publishing, versioning, signing. Stays out of this
  repository by design.
- **Tauri desktop app.**

## Protocol extensions (candidates, not commitments)

- Streaming through `host.contract.call` (currently unary; a brick that wants streamed output from
  another brick calls the non-streaming method).
- Capability negotiation for optional method groups.
- ~~A `cancel` control message for in-flight requests.~~ — landed as `$/cancel` in `veridian/1.1`.
