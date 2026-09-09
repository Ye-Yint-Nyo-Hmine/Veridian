# Plugin development

A brick is a subprocess that speaks `veridian/1.0`. You do not import anything from the kernel;
you speak the protocol.

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
protocol = "veridian/1.0"
runtime = "python"

[spawn]
command = ["${python}", "brick.py"]   # ${python}/${node} expand; a relative script is made absolute

[capabilities]
requires = ["workspace:read", "contract:model_provider"]

[[implements]]
contract = "context"
methods = ["index", "retrieve", "invalidate"]
```

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
