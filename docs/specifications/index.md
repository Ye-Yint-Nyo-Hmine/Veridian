# Specifications

- **[`protocol.md`](protocol.md)** — the normative `veridian/1.1` wire protocol: framing,
  lifecycle, host services, `host.contract.call` routing, streaming, cancellation, error codes,
  conformance.
- **JSON Schemas** — [`/schemas/`](../../schemas/). The source of truth for every payload in both
  directions. `schemas/protocol/` has one file per contract plus `envelope`, `common`,
  `lifecycle`, and `host`. `schemas/plugin-manifest.schema.json` and
  `schemas/configuration.schema.json` cover the manifest and stack files.

Where prose and a schema disagree, the schema wins. Language types (`src/veridian/contracts/`,
`sdks/typescript/`) are validated against the schemas and are never authoritative — this is what
keeps a future Rust kernel honest.
