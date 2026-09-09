# example/linear-agent

A different agent architecture: plan once, then run every step to completion with no re-planning. Bind it to `orchestrator` and the whole agent changes.

Conformance:

```bash
uv run veridian brick conformance examples/custom-agent-runtime
```

See the module docstring in `brick.py` for the one-line stack binding that swaps it in.
