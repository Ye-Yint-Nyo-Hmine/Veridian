# example/minimal-brick

The smallest brick worth writing: one `tools` contract, one tool, ~25 lines of TypeScript, no
build step.

## Run it in a stack

```toml
[bindings]
tools = "examples/minimal-brick"
```

```bash
uv run veridian brick conformance examples/minimal-brick
```

## What it shows

- A brick is a directory with a `veridian.toml` and a spawn command — nothing more.
- The TypeScript SDK (`@veridian/sdk`) gives you `serve({ name, version, implements, handlers })`.
- `node --experimental-strip-types` runs `.ts` directly; there is no compile step.
