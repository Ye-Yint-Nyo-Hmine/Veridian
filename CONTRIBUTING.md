# Contributing to Veridian

Thanks for helping build a runtime that stays out of your way.

## Ground rules

1. **The kernel stays model-agnostic.** No change to `src/veridian/kernel/` may introduce an
   assumption about a specific model, provider, inference strategy, or agent shape. If your change
   needs the kernel to know about one of those, the design is wrong — put it in a brick.
2. **Schemas are the source of truth.** `schemas/` defines the wire format. Python and TypeScript
   types are validated against the schemas; they never define the protocol. A protocol change lands
   in `schemas/` and `docs/specifications/protocol.md` first.
3. **No mock inference, ever.** No brick implements the `inference` contract with fabricated output.
   Tests use fixture bricks (a throwaway `echo` contract) and deliberately broken bricks. Inference
   is covered by `live`-marked tests gated on a real provider.
4. **Windows is a first-class host.** No POSIX signals, no `fork`. Process paths go through
   `asyncio.create_subprocess_exec` with `terminate()` and a hard-kill timeout. Bricks are spawned
   detached from the parent's signal group, so a console Ctrl-C reaches the kernel alone;
   cancellation travels the protocol as `$/cancel`, never as a signal.
5. **Claims match enforcement.** Do not describe a guarantee the runtime does not deliver. Plan
   mode enforces `contract:sandbox` but its `workspace:write` withholding is advisory, and the docs
   say so. Privacy Version 0 buys unlinkability, not confidentiality, and the docs say that too.

## Development

```bash
uv sync --extra dev
uv run pytest -m "not live"           # hermetic suite, must pass with no provider configured
uv run pytest -m live                 # live suite, needs a key or a reachable local server
uv run pytest -m install              # dependency-isolation suite, needs network and uv
uv run pytest -m container            # isolation suite, needs a container engine on PATH
uv run python scripts/milestone1.py   # the eight Milestone 1 criteria
uv run python scripts/milestone2.py   # the ten Milestone 2 criteria
```

The marked suites are deselected by default because each needs something the default environment
may not have. They are not optional: `install` and `container` carry the only end-to-end proof that
dependency isolation and the capability boundary actually work.

## Adding a brick

A brick is a directory under `bricks/<contract>/<name>/` with:

- `veridian.toml` — the manifest (`schemas/plugin-manifest.schema.json`).
- a spawn command that reads JSON-RPC from stdin and writes it to stdout.

**If your brick shells out, pass `stdin=DEVNULL`.** A child process otherwise inherits the brick's
stdin, which is the JSON-RPC channel, and anything it reads corrupts the protocol.

Run `uv run veridian brick validate bricks/<contract>/<name>` and
`uv run veridian brick conformance bricks/<contract>/<name>` before opening a PR. The conformance
harness calls every method of every contract the brick claims and validates both directions
against the schemas.

## Commit and PR

- One logical change per PR.
- Touch `src/veridian/kernel/` only when the PR is about the kernel itself. Adding a capability to
  the runtime legitimately changes it; adding agent behaviour never should. If a feature needs the
  kernel to learn about a model, a provider, or an agent shape, the design is wrong.
- Prefer a brick. Most features belong in `bricks/` or `pre-installed/`, and the whole point of the
  architecture is that they can.
- CI runs the hermetic suite on Windows and Linux, plus a dedicated `install` job. Live tests run
  in a separate, manually triggered job using repository secrets.
