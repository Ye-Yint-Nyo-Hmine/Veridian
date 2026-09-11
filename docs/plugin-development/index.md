# Plugin development

A brick is a subprocess that speaks `veridian/1.1` (or `veridian/1.0` — the kernel negotiates by
major version). You do not import anything from the kernel; you speak the protocol.

## Anatomy

```
bricks/<contract>/<name>/
├── veridian.toml     # manifest — schemas/plugin-manifest.schema.json
└── brick.py|.ts|...  # anything that reads JSON-RPC from stdin and writes it to stdout
```

`veridian.toml`:

```toml
name = "context/my-retriever"
version = "0.1.0"
protocol = "veridian/1.1"
runtime = "python"

[spawn]
command = ["${python}", "brick.py"]   # ${python}/${node} expand; a relative script is made absolute

[capabilities]
requires = ["workspace:read", "contract:model_provider"]

[[implements]]
contract = "context"
methods = ["index", "retrieve", "invalidate"]

# Optional: third-party packages. A brick that declares these is resolved into its own
# environment on install; without this table the brick shares the kernel's interpreter.
[dependencies]
python = ["httpx>=0.27", "tenacity>=8"]
# python_version = ">=3.12,<3.14"   # optional interpreter constraint (uv venv --python)
# node = { "@octokit/rest" = "^21" } # for runtime = "node": installed into a local node_modules
```

## Dependency isolation

Bricks launch under `${python}` / `${node}`. By default that is the kernel's own interpreter, so
every brick shares one dependency set — fine for bricks you wrote, a problem for two third-party
bricks with conflicting requirements.

Declare a `[dependencies]` table and run `veridian brick install <ref>` (or `--all`). A Python
brick gets a private venv under `.veridian/venv` resolved with `uv`; a Node brick gets a local
`node_modules`. The resolved interpreter is recorded in `.veridian/environment.json`, stamped with
a fingerprint of the dependency table — edit the table and the stale environment is ignored until
you reinstall.

`veridian brick list` shows each brick's environment as `n/a` / `missing` / `stale` / `ok`, and
warns for every `missing` / `stale` one. A brick that declares `[dependencies]` but whose
environment is `missing` or `stale` is **refused before it runs**: `${python}` resolution raises
rather than returning the kernel interpreter, and the binding table rejects it with
`invalid_manifest`, so the stack fails to start instead of running the brick against whatever
packages the kernel happens to have. Bricks with no `[dependencies]` table are unaffected and
keep launching under the kernel's interpreter.

## Installing bricks and stacks

A brick you did not write can be installed by name and used, without ever putting an explicit
filesystem path in a stack file.

### `VERIDIAN_HOME`

User-installed bricks and stacks live under `VERIDIAN_HOME` — `~/.veridian` by default, or wherever
the `VERIDIAN_HOME` environment variable points. Layout:

```
$VERIDIAN_HOME/
├── bricks/<name>/<version>/…      # one dir per installed version
└── stacks/<name>.toml
```

The directory is created the first time you install something; nothing touches it at import time.

### Search order

A brick name in a stack file is resolved against three roots, in order — **first match wins**:

| # | root | what it is |
|---|---|---|
| 1 | `project` | `./bricks` next to where you run `veridian` (a repo vendoring its own bricks) |
| 2 | `user` | `$VERIDIAN_HOME/bricks` (what `veridian brick add` installs) |
| 3 | `builtin` | the `bricks/` Veridian ships |

`veridian brick which <name>` prints this list and marks the copy that would load — run it when a
brick isn't the one you expected:

```bash
uv run veridian brick which context/default
uv run veridian brick which context/default@0.2.0     # a specific version
```

### Acquiring a brick

```bash
uv run veridian brick add ../some/local/brick          # a local directory
uv run veridian brick add https://github.com/you/brick # a git repo (shallow-cloned)
uv run veridian brick add https://example.com/b.tar.gz # a .zip / .tar.gz archive
```

The brick is **copied** (never symlinked) into `$VERIDIAN_HOME/bricks/<name>/<version>/`. If it
declares a `[dependencies]` table, `brick add` then resolves its environment through the same
`veridian brick install` path — which runs `uv pip install` / `npm install`, executing setup code
from those packages. You are asked to confirm first; pass `--yes` for scripted use.

`veridian brick remove <name>` removes every installed version; `veridian brick remove <name>@1.2.0`
removes one. Built-in bricks are never touched.

### Versioning

Several versions of a brick coexist on disk. A stack pins with `brick = "name@1.2.0"`; an unpinned
reference resolves to the highest installed version. A brick may also declare which Veridian
versions it supports:

```toml
[requires]
veridian = ">=0.1,<0.2"
```

This is checked when the stack resolves the binding — before anything spawns — and a mismatch fails
stack load naming both the requirement and the running version.

### Provenance

Each install records, beside the brick in `.veridian/install.json`: the source URL, the resolved
git commit (for git sources), the install timestamp, and a content hash. `veridian brick inspect
<name>` prints it, and warns if the on-disk content no longer matches the recorded hash. This is
**not** a signature — it is so you can see where a brick came from.

### Installing a stack

```bash
uv run veridian stack add ./team-stack.toml
uv run veridian stack add https://github.com/you/stacks   # --path points at the .toml inside
uv run veridian --stack team-stack                        # resolved by name, not path
uv run veridian stack list                                # built-in + installed, with source
```

### What installing does *not* do

Installing a brick grants it nothing. Capabilities come only from the stack policy and the
`[isolation]` table. A third-party brick that asks for `network` in its manifest, bound in a stack
whose policy grants none, gets none — and in `isolation.mode = "container"` that is enforced as
`--network none`.

## Python

```python
from veridian.sdk import Brick, rpc, run

class MyRetriever(Brick):
    name = "context/my-retriever"
    version = "0.1.0"
    implements = {"context": ["index", "retrieve", "invalidate"]}

    async def on_initialize(self) -> bool:
        self.root = self.workspace_root
        return True

    @rpc("context.retrieve")
    async def retrieve(self, params, ctx):
        hits = await self.host.contract_call("model_provider", "embed", {"input": [params["query"]], "model": "..."})
        ...
        return {"chunks": [...]}

if __name__ == "__main__":
    run(MyRetriever())
```

The SDK handles framing, lifecycle, dispatch, streaming (`ctx.emit_delta({...})`), host-service
calls (`self.host.*`), and error mapping. Raise `veridian.sdk.BrickError(msg, code)` for a specific
JSON-RPC error.

## TypeScript

```ts
import { serve } from "@veridian/sdk";

await serve({
  name: "tools/my-tools",
  version: "0.1.0",
  implements: { tools: ["list", "invoke"] },
  handlers: {
    "tools.list": async () => ({ tools: [...] }),
    "tools.invoke": async (params, ctx) => ({ output: "..." }),
  },
});
```

Run with `node --experimental-strip-types brick.ts` — no build step in Milestone 1. Use type-only
TS syntax (no `enum`, no parameter properties) so stripping is enough.

## Rules

1. **Schemas are the source of truth.** Validate against `schemas/`, not against a language type.
2. **Call other bricks only through `host.contract.call`.** Never assume which brick is bound.
3. **`stdout` is protocol only.** Log to `stderr` (the kernel forwards it to the event bus).
4. **Windows-safe.** No POSIX signals; expect `terminate()` then a hard kill on shutdown.

## Before you ship

```bash
uv run veridian brick validate bricks/context/my-retriever
uv run veridian brick conformance bricks/context/my-retriever
```

Conformance calls every method of every contract you claim and validates both directions.
