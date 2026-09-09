# example/retry-inference

An inference ENGINE (not a provider): composes the model_provider contract and retries once on failure.

Conformance:

```bash
uv run veridian brick conformance examples/custom-inference
```

See the module docstring in `brick.py` for the one-line stack binding that swaps it in.
