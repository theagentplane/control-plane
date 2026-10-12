# Chronicle API (primitives entities)

**Applies to:** `agent-chronicle` 1.0 and later. The bodies are the
[`agentplane-primitives`](https://github.com/theagentplane/primitives) entities (`Trace`, `Span`,
`Envelope`), validated with the same models Chronicle builds them with. The request and response
wrappers below belong to the control plane (primitives ADR 0003).

Auth is unchanged: bearer key with a tenant and scopes. Writes need `ingest`, reads need `read`.
Everything is tenant-scoped.

| Method and path | Scope | Body | Result |
|---|---|---|---|
| `POST /v1/traces` | ingest | `Trace` | `201` new, `200` existing; `{trace_id, name, metadata}` |
| `PUT /v1/traces/{trace_id}/spans/{span_id}` | ingest | `Span` | `200 {span_id, status: stored \| deduped}` |
| `POST /v1/envelopes:batch` | ingest | `{"envelopes": [Envelope, ...]}` | `201 {status, accepted, deduped, envelope_ids, durability}` |
| `GET /v1/traces/{trace_id}/spans/{span_id}/envelopes` | read | none | `{"envelopes": [Envelope, ...]}` in call order |
| `GET /v1/traces/{trace_id}` | read | none | trace summary with `metadata`, derived `state`, `spans` |

Errors: `400 {"detail": ...}` for a payload that fails validation (the whole batch is rejected),
`401` missing or invalid key, `403` wrong scope, `413` too large, `503` store unavailable.

## Semantics

- **Trace registration is idempotent.** The id is client-supplied. Labels (`metadata`, string
  values) are written **once**: a repeat returns the existing trace and never changes them. A trace
  has no state or status of its own; `state` is `open` while any of its spans is open.
- **A trace may be created implicitly** by the first span or envelope that names it, so Chronicle
  can use lazy registration. A later explicit registration may still name it.
- **Spans** are upserted at open and at close. A closed span is immutable: a later write is a
  `deduped` no-op.
- **Envelopes are one record, mutable until closed.** An open write (`emit_open`) is replaced by its
  close write. A write for an envelope that is already closed is a dedupe, never a reopen. The key is
  `(tenant, trace_id, envelope_id)`.
- **Replay reads** one span of one trace. Whole-trace listing is a UI concern.
- **Legacy bodies.** A body without `envelope_status` is a pre-1.0 Chronicle envelope and keeps the
  old ingest path, so 0.x clients keep working. Mixed batches are accepted.
- `chronicle.sequence` in an envelope's `metadata` is stored as the `sequence` column and orders a
  span's envelopes on read.
