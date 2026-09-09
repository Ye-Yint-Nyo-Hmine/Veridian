# Examples

Each directory is a working brick you can bind into a stack. They exist to make one point
concrete: **you replace a subsystem by changing one line in a stack file, and the kernel does not
change.**

| Example | Contract | Swaps in for | Shows |
|---|---|---|---|
| `minimal-brick/` | `tools` | any tools brick | the smallest possible brick (TypeScript, no build) |
| `custom-inference/` | `inference` | `bricks/inference/*` | an inference *engine* composing `model_provider` — the split that makes model-agnosticism structural |
| `custom-context/` | `context` | `bricks/context/*` | a throwaway retrieval heuristic dropped in with one line |
| `custom-sandbox/` | `sandbox` | `bricks/sandbox/local` | a second sandbox implementation the kernel can't tell apart |
| `custom-memory/` | `memory` | `bricks/memory/*` | memory as a plain JSONL file |
| `custom-agent-runtime/` | `orchestrator` | `bricks/orchestrator/default` | **a different agent architecture** — plan-once-then-execute — bound in one line |

Validate any of them:

```bash
uv run veridian brick conformance examples/custom-memory
```

Then bind it, e.g. in a copy of `stacks/default.toml`:

```toml
[bindings]
memory = "examples/custom-memory"
```

and run the same `veridian run` — nothing else changes.
