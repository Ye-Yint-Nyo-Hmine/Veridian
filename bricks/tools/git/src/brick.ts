// tools/git — a real brick written in TypeScript. Proof that the protocol is language-neutral:
// a Python kernel drives this Node process with no special-casing.
import { execFile } from "node:child_process";
import { serve, type Params, type RequestContext } from "../../../../sdks/typescript/src/index.ts";

function git(args: string[], cwd: string): Promise<{ code: number; out: string; err: string }> {
  return new Promise((resolve) => {
    execFile("git", args, { cwd, maxBuffer: 4_000_000 }, (error, stdout, stderr) => {
      const code = error && typeof (error as { code?: number }).code === "number"
        ? (error as { code: number }).code
        : error
        ? 1
        : 0;
      resolve({ code, out: stdout ?? "", err: stderr ?? "" });
    });
  });
}

const TOOLS = [
  { name: "git_status", description: "Show `git status --porcelain=v1 -b`.", input_schema: { type: "object", properties: {} } },
  { name: "git_diff", description: "Show `git diff` (add staged:true for --cached).", input_schema: { type: "object", properties: { staged: { type: "boolean" } } } },
  { name: "git_log", description: "Show recent commits.", input_schema: { type: "object", properties: { n: { type: "integer" } } } },
  { name: "git_add", description: "Stage paths.", input_schema: { type: "object", required: ["paths"], properties: { paths: { type: "array", items: { type: "string" } } } } },
  { name: "git_commit", description: "Commit staged changes.", input_schema: { type: "object", required: ["message"], properties: { message: { type: "string" } } } },
];

await serve({
  name: "tools/git",
  version: "0.1.0",
  implements: { tools: ["list", "invoke"] },
  handlers: {
    "tools.list": async () => ({ tools: TOOLS }),

    "tools.invoke": async (params: Params, ctx: RequestContext) => {
      const name = params["name"] as string;
      const input = (params["input"] as Params) ?? {};
      const cwd = ctx.workspaceRoot;
      let args: string[];
      switch (name) {
        case "git_status":
          args = ["status", "--porcelain=v1", "-b"];
          break;
        case "git_diff":
          args = input["staged"] ? ["diff", "--cached"] : ["diff"];
          break;
        case "git_log":
          args = ["log", `-n${(input["n"] as number) ?? 10}`, "--pretty=format:%h %s"];
          break;
        case "git_add":
          args = ["add", "--", ...((input["paths"] as string[]) ?? [])];
          break;
        case "git_commit":
          args = ["commit", "-m", (input["message"] as string) ?? ""];
          break;
        default:
          return { output: `unknown tool ${name}`, is_error: true };
      }
      const r = await git(args, cwd);
      return { output: (r.out + (r.err ? `\n${r.err}` : "")).trim() || "(no output)", is_error: r.code !== 0 };
    },
  },
});
