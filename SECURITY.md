# Security

## Reporting a vulnerability

Email the maintainer with a description of the issue, a minimal reproduction, and the affected
version or commit. Please do not open a public issue for an unpatched vulnerability. Expect an
acknowledgement within a few days.

## The Milestone 1 security model, stated honestly

Veridian's kernel enforces a capability model at two boundaries:

- **The host-service boundary.** When a brick calls `host.contract.call`, `host.permission.request`,
  `host.event.emit`, or `host.log`, the kernel checks the calling brick's *effective capabilities* —
  the intersection of what the brick's manifest declares it needs and what the active policy grants.
  A call outside that intersection is denied with a Veridian `permission_denied` error.
- **Process spawn.** Bricks are started with a scrubbed environment (only an explicit allowlist plus
  kernel-injected protocol variables) and a confined working directory (the workspace root).

### What Milestone 1 does NOT do

A plain OS subprocess is not a sandbox. The kernel **does not**:

- enforce network isolation — a brick can open sockets;
- enforce filesystem isolation at the OS level — working-directory confinement is a convention the
  kernel sets up, not a jail the OS enforces;
- limit CPU, memory, or file descriptors;
- prevent a brick from spawning its own child processes.

OS-level confinement is the job of the **sandbox brick** and of the container / WASM isolation modes
on the roadmap. Until then: **only run bricks you trust**, the same standard you apply to any
package you install. `veridian brick inspect` and the conformance harness help you decide, but they
are not a security boundary.

## Environment and secrets

Provider bricks read API keys from their environment. The kernel passes through only the variables
a brick's manifest lists in `env_passthrough`. Keys are never written to the event bus; brick
`stderr` is forwarded to the bus, so bricks must not print secrets.
