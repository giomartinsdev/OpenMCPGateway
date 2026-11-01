# OpenMCPGateway

Auth, authorisation, rate limiting and observability in front of any MCP server.

Point an MCP client at the gateway instead of at the server. The gateway terminates
OAuth (GitHub, Google, Microsoft, Discord, any OIDC provider) or verifies a bearer
key, checks an allowlist, enforces a per-subject budget, and records every request.

```
MCP client ──▶ https://gateway.example.com/g/<slug>/mcp ──▶ your MCP server
                        │
                        ├─ 401 + RFC 9728 / 8414 discovery, when OAuth
                        ├─ allowlist                403
                        ├─ requests per minute      429
                        └─ request log + stats      →  UI
```

## Why

An MCP server usually ships with one static token in every client config, or with
no auth at all because it sits on a VPC. Both fail the moment the server is worth
using: the token ends up in a dotfile on five laptops and cannot be revoked per
person, and "on the VPC" is one misrouted Service away from the internet.

A gateway puts the decision in one place, uses the identity provider you already
have, and gives you the call log you need to answer "who used this and what broke".

## Run it

```bash
git clone https://github.com/giomartinsdev/OpenMCPGateway && cd OpenMCPGateway
export OMG_SESSION_SECRET=$(openssl rand -hex 32)
docker compose up -d --build
open http://localhost:8000          # create the first operator account
```

Then **New gateway**: give it a slug, the upstream URL (`http://my-mcp:8000/mcp`),
and choose who may call it. The endpoint shown is what you paste into your client.

### Without Docker

```bash
cd backend && uv sync --extra dev
export OMG_DATABASE_URL=postgresql://user:pass@localhost:5432/omg
export OMG_SESSION_SECRET=$(openssl rand -hex 32)
export OMG_BASE_URL=http://localhost:8000
npm --prefix web install && npm --prefix web run build   # or: npm --prefix web run dev
.venv/bin/omg serve
```

### Kubernetes

```bash
kubectl apply -k k8s/
```

Edit `k8s/secret.example.yaml` → `k8s/secret.yaml` first (git-ignored), and set the
host in `k8s/ingress.yaml`. The deployment expects a Postgres you already run; point
`OMG_DATABASE_URL` at it.

## Calling a gateway

The gateway is a normal MCP server, so any client works:

```jsonc
// opencode.json
{
  "mcp": {
    "my-server": {
      "type": "remote",
      "url": "https://gateway.example.com/g/my-server/mcp"
    }
  }
}
```

With `auth: api_key`, add `"headers": {"Authorization": "Bearer omg_..."}`.
With `auth: oauth`, add nothing: the client reads the 401 challenge, finds the
metadata at the well-known URL, registers itself, and opens a browser.

## What it enforces

| Layer | Behaviour |
| --- | --- |
| Authentication | OAuth proxy per gateway, or bearer key hashed with SHA-256 |
| Authorisation | Optional allowlist of subjects; empty means "any authenticated caller" |
| Rate limit | Per subject, per gateway, counting JSON-RPC requests (handshakes are free) |
| Observability | Every attempt logged with subject, outcome, status, latency, IP |
| Fail closed | An OAuth gateway without credentials serves 503, never an open proxy |

Rate limiting is in-process: with N replicas the effective limit is N×. Fine for a
single-replica deployment, which is the current design; Redis would remove the
caveat. See `ponytail:` comments in `backend/src/gateway/proxy/runtime.py`.

## Configuration as code

The UI and a file are equivalent — both reach the same command handler.

```bash
omg export > gateways.yaml          # never includes secrets
omg apply gateways.yaml             # reconcile; prints any newly issued keys
```

`/config` in the UI does the same with a diff-free "validate, then apply".

## Architecture

Event-sourced: every change is an append-only event, read models are projections.
That is what makes the audit log trustworthy and lets two replicas share one
database without a cache-coherence story.

```
backend/src/gateway/
  domain/           aggregates and invariants — the only place rules live
  application/      command handler, container wiring
  infrastructure/    event store, projections, password hashing
  proxy/            FastMCP proxy per gateway, guard middleware, rate limiter
  interface/        admin REST API, session cookies, ASGI dispatcher
web/                React admin UI (Kong-style dark theme)
```

```
cd backend && .venv/bin/python -m pytest tests/
```

The suite is Gherkin scenarios against a real ASGI server and a real Postgres in a
container: no mocks of the parts that break.

## License

Apache-2.0
