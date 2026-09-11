# @veridian/sdk

The TypeScript brick SDK for the [Veridian](../../README.md) runtime.

A brick is a subprocess that speaks `veridian/1.1` — JSON-RPC 2.0, one message per NDJSON line,
protocol on stdout, logs on stderr. This SDK handles framing, lifecycle, dispatch, streaming
deltas, host-service calls, and error mapping so a brick author writes only handlers. It
negotiates by major version, so it also talks to a `veridian/1.0` kernel; it does not interrupt a
running handler on `$/cancel`.

```ts
import { serve } from "@veridian/sdk";

await serve({
  name: "tools/git",
  version: "0.1.0",
  implements: { tools: ["list", "invoke"] },
  handlers: {
    "tools.list": async () => ({ tools: [/* ... */] }),
    "tools.invoke": async (params, ctx) => {
      ctx.host.log(`invoking ${params.name}`);
      return { output: "…" };
    },
  },
});
```

## Running a brick

Bricks run under Node's type stripping — no build step:

```
node --experimental-strip-types brick.ts
```

Node ≥ 22.6 (flagged) or ≥ 23.6 (unflagged). Type stripping erases types but does not transform,
so avoid `enum`, `namespace`, and parameter properties. Run `npm run typecheck` for real checking.

## What the SDK gives a handler

Each handler receives `(params, ctx)` where `ctx` has:

- `config` — this brick's config table from the stack file
- `workspaceRoot` — absolute path the kernel confined the brick to
- `host` — `log`, `emit`, `requestPermission`, `contractCall` back into the kernel
- `emitDelta(delta)` — for a streaming method, send a `<contract>.delta` notification keyed to the
  current request

The wire format is defined by the JSON Schemas in `/schemas`, not by this package.
