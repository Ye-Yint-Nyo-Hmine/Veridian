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

- **Container isolation** (Docker / Podman) as a sandbox brick mode and as a brick-spawn mode.
- **WASM isolation** for bricks that can be compiled to WASI.
- **Firecracker microVMs** for untrusted bricks.

Until then the capability model is enforced at the host-service boundary and at spawn only; a
plain subprocess is not a jail. See [`SECURITY.md`](SECURITY.md).

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
- A `cancel` control message for in-flight requests.
