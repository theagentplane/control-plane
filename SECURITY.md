# Security policy

## Supported versions

Security fixes are applied to the latest released version.

## Reporting a vulnerability

Please report security issues **privately**. Do not open a public issue. Use GitHub's private
vulnerability reporting: open the **Security** tab of this repository and choose **Report a
vulnerability**.

We aim to acknowledge reports within 3 business days and to share a remediation plan after triage.

## Data-handling posture

The control plane stores prompts, tool inputs and outputs, and the raw provider responses that
Chronicle sends it. Treat the database as a store of production data.

- **Keys:** API keys are stored as SHA-256 hashes; the secret is shown once at creation.
  Environment keys (`CONTROL_PLANE_API_KEYS`) are held by the process.
- **Open mode:** with no keys configured the service is open (tenant `local`, all scopes). That is
  for a single trusted local host. **Set keys before exposing the service on a network.**
- **Tenant isolation:** every row and every query is scoped by the key's tenant.
- **Transport:** terminate TLS in front of the service; it speaks plain HTTP.
- **Logging:** request bodies are not logged by the service.
- **Retention:** run `control-plane db prune` (or schedule it) so traces do not live forever.
- Redaction of secrets in prompts happens in Chronicle **before** data is sent; the control plane
  does not alter what it receives.
