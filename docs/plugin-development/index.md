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
