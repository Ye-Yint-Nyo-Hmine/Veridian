# Security

The authoritative statement is [`/SECURITY.md`](../../SECURITY.md). This page summarises the model.

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

## What Milestone 1 does NOT do

A subprocess is not a sandbox. The kernel does not enforce network isolation, OS-level filesystem
isolation, or CPU/memory limits, and cannot stop a brick spawning its own children.
`veridian brick inspect` reports a brick's trust level (`MINIMAL` / `STANDARD` / `ELEVATED`) as an
advisory, not a boundary.

**Run only bricks you trust**, the same standard you apply to any dependency. OS-level confinement
is the job of the sandbox brick and the container / WASM isolation modes on the
[roadmap](../../ROADMAP.md).
