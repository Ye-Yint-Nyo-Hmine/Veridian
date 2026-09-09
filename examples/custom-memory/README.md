# example/jsonl-memory

A custom memory brick backed by a single JSONL file. Human-readable, git-diffable.

Conformance:

```bash
uv run veridian brick conformance examples/custom-memory
```

See the module docstring in `brick.py` for the one-line stack binding that swaps it in.
