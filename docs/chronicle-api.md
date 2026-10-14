# Chronicle API (primitives entities)

**Applies to:** `agent-chronicle` 1.0 and later. The bodies are the
[`agentplane-primitives`](https://github.com/theagentplane/primitives) entities (`Trace`, `Span`,
`Envelope`), validated with the same models Chronicle builds them with. The request and response
wrappers below belong to this service (primitives ADR 0003).

Auth: bearer API key with a tenant and scopes. Writes need `ingest`, reads need `read`.
Everything is tenant-scoped; a tenant never sees another tenant's traces.

| Method and path | Scope | Body | Result |
|---|---|---|---|
| `POST /v1/traces` | ingest | `Trace` | `201` new, `200` existing; `{trace_id, name, metadata}` |
| `PUT /v1/traces/{trace_id}/spans/{span_id}` | ingest | `Span` | `200 {span_id, status: stored \| deduped}` |
| `POST /v1/envelopes:batch` | ingest | `{"envelopes": [Envelope, ...]}` | `201 {status, accepted, deduped, envelope_ids}` |
| `GET /v1/traces/{trace_id}/spans/{span_id}/envelopes` | read | none | `{"envelopes": [Envelope, ...]}` in call order |
| `GET /v1/traces/{trace_id}/envelopes` | read | none | every envelope of the trace in start order |
| `GET /v1/traces/{trace_id}` | read | none | `{trace_id, name, metadata, attributes, state, started_at, ended_at, span_count, envelope_count, spans}`; `attributes` is the mutable trace-level dict (the halt flag lives there, ADR 0006) |
| `GET /v1/traces` | read | none | `{"traces": [...]}`, recent first |

`GET /v1/traces` accepts `session_id`, `message_id`, `user_id`, `label=key:value` (repeatable),
`q` (matches id, name or a label value), `limit` (1 to 500) and `offset`.

Errors: `400 {"detail": ...}` for a payload that fails validation (the **whole batch** is
rejected), `401` missing or invalid key, `403` wrong scope, `404` unknown trace, `413` too large.

## Semantics

- **Acknowledged after commit.** A write returns only after its transaction commits. There is no
  queued acknowledgement and no `Durability` header.
- **Trace registration is idempotent.** The id is client-supplied. Labels (`metadata`, string
  values) are written **once**: a repeat returns the existing trace and never changes them. A trace
  has no state or status of its own; `state` is `open` while any of its spans is open.
- **A trace may be created implicitly** by the first span or envelope that names it, so Chronicle
  can use lazy registration. The first explicit registration of such a trace may then name it and
  set its labels, if it has none.
- **Spans** are upserted at open and at close. A closed span is immutable: a later write is a
  `deduped` no-op.
- **Envelopes are one record, mutable until closed.** An open write (`emit_open`) is replaced by
  its close write. A write for an envelope that is already closed is a dedupe, never a reopen. The
  key is `(tenant, trace_id, envelope_id)`.
- **Schema versions.** Every entity carries `schema_version` (`major.minor`). The service accepts
  the major its primitives package speaks and the previous major; any other major is a `400`. A
  newer minor of an accepted major is read (unknown fields are ignored).
- **Replay reads** one span of one trace. `metadata["chronicle.sequence"]` is stored as the
  `sequence` column and orders a span's envelopes on read.
- **Only primitives bodies.** An envelope in the pre-1.0 Chronicle shape is rejected.
