# Privacy — Version 0

Version 0 is about **where conversation state lives**, not about hiding prompts from the model
that answers them. It removes the server-side database from the picture: no centralised
conversation history, no persistent provider profile keyed to your identity. That is a real,
checkable property. It is also a narrow one, and this page is careful about the edges.

> **The one-line claim.** *No centralised conversation history, and no persistent provider
> profile.* Not "the provider cannot see your conversation" — it can; you send it the prompt.

---

## What already holds by construction

Most of Version 0 is not new code. It falls out of how the kernel and the bricks are already
built. The gap Milestone 2 closes is **enforcement and demonstration**, not architecture.

- **Memory bricks are local.** `memory/ephemeral` lives in process and is gone when the brick
  exits. `memory/vector` and `memory/graph` are local SQLite files under the workspace
  (`.veridian/`). None of them declares `network`; none writes conversation state to a server.
- **Context bricks read the local workspace only.** `context/default`, `context/graph`,
  `context/semantic`, and `context/vector` each require `workspace:read` and nothing that would
  let them reach off the machine for their own purposes. Retrieval is over your files.
- **The kernel is the only component that holds assembled context, and it runs on your
  machine.** System prompt, memory, retrieved context, and the message are stitched together in
  the kernel process on the user's host. The assembled prompt exists there and is handed to an
  `inference` brick for exactly one request.
- **The `inference` / `model_provider` split is the privacy layer.** `model_provider` adapts one
  vendor API. `inference` is a strategy that may call several. The kernel knows only "something
  implements `inference`". Because no privacy-relevant code depends on which provider is behind
  that boundary, swapping the provider — or pointing it at a local model — changes what the
  prompt is exposed to without touching anything else. That indirection **is** what makes the
  privacy story provider-agnostic.

New in Milestone 2, making the above demonstrable rather than merely true:

- **The `conversation` contract** (`bricks/conversation/sqlite`) makes chat history a first-class
  *local* object — an append-only turn log in one SQLite file under `.veridian/`, with
  `append` / `load` / `list_sessions` / `delete`. The brick has no `network` capability and could
  not open a socket if it tried. Before this, the orchestrator held messages in loop state and
  dropped them when a run ended.
- **Identity stripping is conformance-checked.** An inference brick must not forward a stable
  client identity to a provider. `tests/security/test_identity_stripping.py` drives every
  inference brick against a capture server and inspects the bytes that left the process; a
  deliberately planted leaky adapter (`tests/fixtures/leaky_inference`) is there to prove the
  check still bites.

---

## Persistent vs. ephemeral — the data-flow boundary

| Data | Where it lives | Lifetime | Leaves the device? |
|---|---|---|---|
| Conversation history | `conversation` brick — local SQLite, `.veridian/conversation.sqlite` | on disk until you `delete` it | **No** |
| Agent memory | `memory/ephemeral` in process; `memory/vector` & `memory/graph` local SQLite | process life, or on disk locally | **No** |
| Retrieved context | `context` brick, from the local workspace | rebuilt per request | Only as part of the prompt below |
| System prompt | assembled in the kernel, on device | per request | Only as part of the prompt below |
| **Assembled prompt** (system + memory + context + messages) | kernel process, on device | per request | **Yes — sent to the provider for that one request** |
| Model completion | returned to the kernel, appended to local history | per request | Originates at the provider |
| Provider API key | brick environment (`env_passthrough` allowlist) | brick process life | Sent to the provider it authenticates, nothing else |
| Prompt + completion, at the provider | the provider's infrastructure | the provider's retention policy — outside Veridian's control | Already there; this is the request |
| Stable client identity (account name, install id, device id) | — | — | **No — never assembled, never sent; B3 enforces this** |

The useful half of this table is the left column staying local. The honest half is the two rows
that say "Yes": the prompt and the key reach the provider by design, and what the provider does
with them afterwards is its policy, not Veridian's.

---

## Three limits that are structural, not bugs

**1. Version 0 buys unlinkability, not confidentiality.** The client assembles the system prompt,
memory, conversation, and message, then sends all of it to a provider. The provider *reads* that
content. What it does not get is a durable profile keyed to an identity, because no server holds
the history. Correct phrasing: "no centralised conversation history, no persistent provider
profile." Never: "the provider cannot see your conversation."

**2. A TEE cannot protect a prompt from the model provider you forward it to.** In the Phase 2
design a trusted worker still calls OpenAI or Anthropic, so plaintext leaves the enclave by
design. A TEE only closes the gap for models running *inside* it. End-to-end encrypted inference
is therefore reachable only on self-hosted models, and Veridian presents it as a property of the
local and self-hosted adapters — not of the runtime as a whole.

**3. Remote attestation is meaningless before a hosted worker exists to attest.** That is
Milestone 4. Until there is a worker to produce an attestation, there is nothing to verify.

### Honest progression

- **V0** removes the server-side database. *(here)*
- **V1** removes the gateway operator from the trust set (client-side encryption — only meaningful
  once the gateway and the inference worker are separate parties).
- **V2** removes the infrastructure operator: self-hosted models only.
- **V3** proves it: remote attestation and end-to-end encrypted inference.

---

See also [`SECURITY.md`](../../SECURITY.md) for the capability model and isolation boundaries, and
[`specifications/protocol.md`](../specifications/protocol.md) for the wire format.
