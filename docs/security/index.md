# Security

The authoritative statement is [`/SECURITY.md`](../../SECURITY.md). This page summarises the model.
For the Version 0 privacy boundary — what stays on the device, what reaches a provider, and the
three limits the design does not claim past — see [`privacy.md`](privacy.md).

## Capability model

A capability is a colon-delimited string: `contract:memory`, `contract:memory:search`,
`workspace:read`, `workspace:write`, `process:spawn`, `network`, `env:VAR`. `*` is a single-segment
wildcard.

**Effective capabilities = manifest `requires` ∩ policy `grant` − policy `deny`.** Computed per
brick at start, sent to the brick in `plugin.initialize`, and enforced by the kernel:

- at the host-service boundary — a `host.contract.call` outside the caller's effective set is
  denied with `permission_denied` (-32001);
- at process spawn — scrubbed environment (allowlist only) and working directory set to the
  workspace root.

Policy lives in the stack file:

```toml
[policy]
grant = ["network", "contract:inference", "workspace:read"]
deny  = ["process:spawn"]

[bindings.tools]
brick = "bricks/tools/git"
grant = ["process:spawn"]     # per-brick grant, still subject to the global deny
```

## Two isolation modes

**`process` (the default).** A plain OS subprocess, which is not a sandbox. In this mode the kernel
does not enforce network isolation, does not enforce OS-level filesystem isolation, does not limit
CPU or memory, and cannot stop a brick spawning children. Working-directory confinement is a
convention the kernel sets up, not a jail the OS enforces. **Run only bricks you trust**, the same
standard you apply to any dependency.

**`container`.** A brick whose manifest carries `[isolation]` with `mode = "container"` runs inside
a Docker or Podman container the kernel drives, and there the capability model has OS teeth:

```toml
[isolation]
mode = "container"
image = "python:3.13-slim"
network = true
allow_hosts = ["api.example.com"]
```

- `network = false` is `--network none`. The brick has no interface and cannot open a socket.
- `network = true` with `allow_hosts` puts the brick on an internal network whose only exit is a
  sidecar proxy that refuses every `CONNECT` to an unlisted host.
- `network = true` with no allowlist is refused outright for any brick that handles conversation
  content.

`veridian stack show` prints the isolation mode and permitted destinations for every brick, so a
stack's entire network surface is auditable from the manifests alone.

Container mode still depends on the host's container engine for its guarantees.
`veridian brick inspect` reports a trust level (`MINIMAL` / `STANDARD` / `ELEVATED`) and a
provenance record as advisories, not boundaries. WASM and Firecracker isolation remain on the
[roadmap](../../ROADMAP.md).

## Installing a brick grants it nothing

`veridian brick add` copies a brick onto disk and resolves its dependencies. It confers no
capabilities. A third-party brick that asks for `network` in its manifest, bound in a stack whose
policy grants none, gets none — and in container mode that refusal is `--network none`.

Note that `brick add` runs `uv pip install` or `npm install` for a brick declaring dependencies,
which executes setup code from those packages. You are asked to confirm before that happens.
