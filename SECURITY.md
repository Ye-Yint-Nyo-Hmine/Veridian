# Security

## Reporting a vulnerability

Email the maintainer with a description of the issue, a minimal reproduction, and the affected
version or commit. Please do not open a public issue for an unpatched vulnerability. Expect an
acknowledgement within a few days.

## The security model, stated honestly

Veridian's kernel enforces a capability model at two boundaries:

- **The host-service boundary.** When a brick calls `host.contract.call`, `host.permission.request`,
  `host.event.emit`, or `host.log`, the kernel checks the calling brick's *effective capabilities* —
  the intersection of what the brick's manifest declares it needs and what the active policy grants.
  A call outside that intersection is denied with a Veridian `permission_denied` error.
- **Process spawn.** Bricks are started with a scrubbed environment (only an explicit allowlist plus
  kernel-injected protocol variables) and a confined working directory (the workspace root).

### Process mode: what it does NOT do

A brick spawned in the default `process` mode is a plain OS subprocess, which is not a sandbox. In
that mode the kernel **does not**:

- enforce network isolation — a brick can open sockets;
- enforce filesystem isolation at the OS level — working-directory confinement is a convention the
  kernel sets up, not a jail the OS enforces;
- limit CPU, memory, or file descriptors;
- prevent a brick from spawning its own child processes.

For a process-mode brick: **only run bricks you trust**, the same standard you apply to any package
you install.

### Container mode: an OS-level boundary

A brick whose manifest carries `[isolation]` with `mode = "container"` is run by the kernel inside
a Docker or Podman container it controls (`docker run` / `podman run`), with the workspace mounted
read-write at `/workspace` and the brick directory read-only at `/brick`. In this mode:

- **`network = false`** (the default) is `docker run --network none` — the brick has no network
  interface at all and cannot open a socket to anything.
- **`network = true` with `allow_hosts`** puts the brick on an `--internal` network with no route
  of its own. Its only exit is a sidecar egress proxy (`veridian.security.egress_proxy`) that
  refuses every `CONNECT` whose host is not on `allow_hosts`. `HTTPS_PROXY` is injected so a normal
  HTTP client uses it without code changes. This is how an inference brick reaches exactly its
  provider and nothing else.
- **`network = true` with no `allow_hosts`** is unrestricted networking, and is refused for any
  brick that also implements a conversation-content contract (`inference`, `context`, `memory`,
  `orchestrator`, …).

`veridian stack show` prints the isolation mode and permitted destinations for every brick in a
stack, so a stack's whole network surface is auditable from the manifests alone.

Container mode still depends on the host's container engine for its guarantees, and a bricks-you-
trust posture remains the right default for process mode. `veridian brick inspect` and the
conformance harness help you decide what to trust; they are not a security boundary.

## Environment and secrets

Provider bricks read API keys from their environment. The kernel passes through only the variables
a brick's manifest lists in `env_passthrough`. Keys are never written to the event bus; brick
`stderr` is forwarded to the bus, so bricks must not print secrets.
