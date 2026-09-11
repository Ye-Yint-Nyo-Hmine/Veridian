# Roadmap

## Shipped

### Milestone 1 — the architecture works

The kernel, the `veridian/1.0` protocol, the plugin runtime, Python and TypeScript SDKs, a
reference brick for every contract, the conformance harness, and the CLI. Its eight criteria are
checked by `scripts/milestone1.py`, and the claim they prove is that swapping inference, context,
or the sandbox requires no kernel change.

### Milestone 2 — the boundary is enforced, not declared

Before Milestone 2 a manifest saying `network = false` was a note. Now it is a boundary. Checked by
`scripts/milestone2.py`.

- **Per-brick dependency isolation.** A `[dependencies]` manifest table resolves into a private
  venv or `node_modules` on `veridian brick install`, instead of sharing the kernel's interpreter.
  A brick that declares dependencies with no valid environment is refused at bind time rather than
  silently borrowing the kernel's packages.
- **Container spawn mode.** `[isolation]` with `mode = "container"` makes the kernel spawn the
  brick inside a container it drives, workspace mounted read-write and the brick read-only.
- **Egress control.** `network = false` is `--network none`. `network = true` with `allow_hosts`
  routes the brick through a sidecar allow-listing proxy as its only exit. `network = true` with no
  allowlist is refused for any brick that handles conversation content.
- **Cooperative cancellation.** `$/cancel` in `veridian/1.1`, negotiated on major version so
  `veridian/1.0` bricks still bind and degrade to timeout. A cancel cascades through
  `host.contract.call`, so interrupting a run stops the downstream model call too.
- **Local conversation history.** The `conversation` contract plus `bricks/conversation/sqlite`, an
  append-only turn log in one local SQLite file, distinct from `memory`.
- **Identity stripping.** A conformance check drives every inference brick against a capture server
  and asserts no stable client identity reaches the provider. A planted leaky adapter proves the
  check bites.
- **Gateway client.** `bricks/model-provider/gateway`. The gateway *service* stays in its own
  repository, like the marketplace.
- **Privacy documentation.** [`docs/security/privacy.md`](docs/security/privacy.md) — the
  persistent/ephemeral boundary and the three structural limits the design does not claim past.

### Since Milestone 2

- **A real plugin system.** `VERIDIAN_HOME`, ordered search roots, `brick add` / `stack add` from a
  local path, a git URL, or an archive, versioning with `name@version`, a `[requires].veridian`
  compatibility check at stack-resolve time, provenance records, and a consent prompt before any
  dependency install.
- **The interactive harness.** A boot screen and structured running UI, a slash-command registry,
  a reusable numbered selector, `/model` and `/stack` with live switching, `/context` and
  `/compact`, `@file` mentions, plan and auto modes, and session persistence with `--resume`.
- **`pre-installed/`** — a complete autonomous coding agent (verifying loop, git awareness, failure
  recovery, repo-map context, AGENT.md, skills) built entirely from bricks, touching no kernel code.

## Next

### Milestone 3 — privacy Version 1

Client-side encryption. Note the prerequisite: encryption only changes the trust model if the
gateway and the inference worker are operated by different parties. Running both yourself means
encrypting to yourself, which proves nothing. The gateway service has to exist first.

### Later

- **TEE inference (V2)** and **remote attestation with end-to-end encryption (V3)**. See
  `docs/security/privacy.md` for why each only helps once the trust set actually changes, and why
  a TEE cannot protect a prompt from the provider you forward it to.
- **Unix domain socket / Windows named pipe transport.** `Transport` is an ABC and `StdioTransport`
  is the only implementation; a new one drops in without touching `ipc.py` or its callers.
- **Remote transport** — a brick running on another host. The negotiated protocol version names the
  framing, so a length-prefixed or TLS framing ships under a new version string without changing
  any method contract.
- **WASM isolation** for bricks that compile to WASI, and **Firecracker microVMs** for untrusted
  bricks.
- **Rust kernel port** and a **Rust brick SDK**. The protocol is specified as JSON Schema plus the
  language-neutral conformance suite in `tests/conformance/` precisely so a second kernel can be
  validated against the same bar. No Python type is allowed to leak into the wire format.
- **The marketplace** — brick discovery, publishing, signing. Stays out of this repository by
  design.
- **Tauri desktop app.**

## Protocol extensions

Candidates, not commitments.

- Streaming through `host.contract.call`. It is unary today, so a brick wanting streamed output
  from another brick calls the non-streaming method.
- Capability negotiation for optional method groups.
- Multiple bricks bound to one contract. A stack binds exactly one brick per contract today, which
  is why `pre-installed/bricks/toolkit` provides filesystem, terminal, and git tools in a single
  brick rather than three.
