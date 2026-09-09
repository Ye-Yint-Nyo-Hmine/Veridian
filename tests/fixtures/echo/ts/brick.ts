// Echo fixture implemented with the TypeScript SDK — proves the wire contract is language-neutral.
import { serve, type Params, type RequestContext } from "../../../../sdks/typescript/src/index.ts";

await serve({
  name: "echo/ts",
  version: "1.0.0",
  implements: { echo: ["say", "stream", "roundtrip"] },
  handlers: {
    "echo.say": async (params: Params) => ({ text: (params["text"] as string) ?? "" }),

    "echo.stream": async (params: Params, ctx: RequestContext) => {
      const text = (params["text"] as string) ?? "";
      const chunks = (params["chunks"] as number) ?? 3;
      for (let i = 0; i < chunks; i++) ctx.emitDelta({ index: i, text });
      return { text, chunks };
    },

    "echo.roundtrip": async (params: Params, ctx: RequestContext) => {
      ctx.host.log("calling echo.say through the host proxy");
      const out = (await ctx.host.contractCall("echo", "say", {
        text: (params["text"] as string) ?? "",
      })) as { text: string };
      return { text: out.text, via: "ts-host" };
    },
  },
});
