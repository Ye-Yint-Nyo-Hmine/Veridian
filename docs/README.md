# Veridian documentation

- `getting-started/` — install, first run, sessions, and writing your first stack.
- `architecture/` — how the kernel, plugin runtime, CLI, and bricks fit together.
- `bricks/` — reference for each shipped brick and its contract, plus the pre-installed agent.
- `plugin-development/` — how to build, install, and distribute a brick, in Python or TypeScript.
- `security/` — the capability model, the two isolation modes and their limits, and the Version 0
  privacy boundary (`privacy.md`).
- `specifications/` — `protocol.md`, the normative `veridian/1.1` wire-format spec. Schemas live in
  `/schemas`, and where prose and a schema disagree, the schema wins.

Outside `docs/`:

- [`/ARCHITECTURE.md`](../ARCHITECTURE.md) — the full architecture, module by module.
- [`/ROADMAP.md`](../ROADMAP.md) — what has shipped and what is next.
- [`/SECURITY.md`](../SECURITY.md) — the authoritative security statement.
- [`/CONTRIBUTING.md`](../CONTRIBUTING.md) — ground rules, test suites, and adding a brick.
- [`/pre-installed/README.md`](../pre-installed/README.md) — the shipped autonomous agent.
- [`/examples/README.md`](../examples/README.md) — one minimal brick per replaceable subsystem.
