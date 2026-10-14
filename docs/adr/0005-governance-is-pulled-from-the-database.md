# 0005. Governance configuration is stored here and pulled by the agent

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-14 |
| Deciders | Susheem Koul |

## Context

TokenOps governors need budgets, policy instances and segments. They can come from a file next to
each agent, or from one place the agents fetch from. A shared ledger already needs the plane.

## Options considered

### Option A: configuration files per agent

- Good: no network at start-up.
- Bad: drift between agents; no single place to change a budget; no tenant scoping.

### Option B: configuration in the control plane database, pulled by the agent

- Good: one source of truth, edited in one place (API, CLI or the TokenOps tab), per tenant,
  with a conditional request so agents can poll cheaply.
- Bad: the agent needs the plane at start-up (it can cache the last response).

## Decision

We will store the configuration in the database, per tenant, and serve it with
`GET /v1/governance/{agent}`: exactly the dict `build_governor` consumes, with an `ETag` so a
client can send `If-None-Match` and receive `304`. It is seeded from YAML (`control-plane db seed`,
and the packaged default for tenant `local` on first start) and edited through the CRUD routes
or the web page. A policy template must be one of the known TokenOps templates.

## Consequences

- **Good:** one place to change governance; tenants are isolated.
- **Bad or risky:** a tenant with no configuration receives an empty one, which means ungoverned;
  seeding a new tenant is an explicit step.
- **Follow-ups:** push or long-poll notification if polling proves too slow.

## Impact on other repositories

- tokenops: fetch configuration over HTTP and cache it by `ETag`.

## Links

[governance API](../governance-api.md)
