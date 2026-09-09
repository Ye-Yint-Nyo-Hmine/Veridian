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
   `asyncio.create_subprocess_exec` with `terminate()` and a hard-kill timeout.

## Development

```bash
uv sync --extra dev
uv run pytest -m "not live"     # hermetic suite, must pass with no provider configured
uv run pytest -m live           # live suite, needs a key or a local server
```

## Adding a brick

A brick is a directory under `bricks/<contract>/<name>/` with:

- `veridian.toml` — the manifest (`schemas/plugin-manifest.schema.json`).
- a spawn command that reads JSON-RPC from stdin and writes it to stdout.

Run `uv run veridian brick validate bricks/<contract>/<name>` and
`uv run veridian brick conformance bricks/<contract>/<name>` before opening a PR. The conformance
harness calls every method of every contract the brick claims and validates both directions
against the schemas.

## Commit and PR

- One logical change per PR. Keep the kernel diff empty unless the PR is explicitly about the kernel.
- CI runs the hermetic suite on Windows and Linux. Live tests run in a separate, manually triggered
  job using repository secrets.
