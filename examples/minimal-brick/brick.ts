// The smallest useful Veridian brick: one tool, in TypeScript, runnable with
//   node --experimental-strip-types brick.ts
// No build, no dependencies beyond the SDK.
import { serve, type Params } from "../../sdks/typescript/src/index.ts";

await serve({
  name: "example/minimal-brick",
  version: "0.1.0",
  implements: { tools: ["list", "invoke"] },
  handlers: {
    "tools.list": async () => ({
      tools: [
        {
          name: "reverse",
          description: "Reverse a string.",
          input_schema: { type: "object", required: ["text"], properties: { text: { type: "string" } } },
        },
      ],
    }),
    "tools.invoke": async (params: Params) => {
      if (params["name"] !== "reverse") return { output: "unknown tool", is_error: true };
      const text = String((params["input"] as Params)["text"] ?? "");
      return { output: [...text].reverse().join("") };
    },
  },
});
