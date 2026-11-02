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

### Sign in with Google

There are **two** OAuth setups in this project, and they are unrelated:

| what | who logs in | where the client lives |
|---|---|---|
| **Admin UI** | you, the operator | `OMG_GOOGLE_CLIENT_ID` / `OMG_GOOGLE_CLIENT_SECRET` |
| **A gateway** | callers of one proxied MCP server | per-gateway, in the UI or a YAML file |

**You set this in the panel, not in the deployment.** Sign in as an administrator,
open **Settings**, and fill in:

| field | what to put |
|---|---|
| Authorised redirect URI | `https://<your-host>/api/auth/google/callback` |
| Client ID | `…apps.googleusercontent.com` |
| Client secret | write-only; it never comes back out of the API |
| Who may sign in | comma-separated addresses |

Create the OAuth client (type *Web application*) in Google Cloud and register that
same redirect URI there — it must match byte for byte, or the exchange fails at
Google with no mention of the panel.

The environment variables `OMG_GOOGLE_CLIENT_ID`, `OMG_GOOGLE_CLIENT_SECRET` and
`OMG_OPERATOR_ALLOWLIST` exist only to seed a deployment that has to come up with
sign-in already working. Once anything is saved in the panel, the panel wins: the
client id belongs to whoever runs the installation, and making them redeploy to
change it would be absurd.

The password form keeps working, so a misconfigured IdP never locks you out. If no
client is configured the Google button is not rendered. Signing in creates the
account on first success; what it can see depends on the tenant it then creates or
joins. Only an administrator can change these settings.

`OMG_OPERATOR_ALLOWLIST` is an address book, not a gate: those addresses skip the
tenant choice and land in the installation's own tenant (`TENANT_OF_DEPLOYMENT`)
with the viewer role. Everyone else gets an account with no tenant and is asked
what to do next.

### Tenants

The first thing an account sees after signing up is not the panel but a choice:
**create a tenant** or **join one with a code**.

* Creating a tenant makes the account its `owner`. It is the only path that grants
  ownership, and it needs no permission because the account has none yet.
* Joining needs the tenant's **join code** — eight characters, no `O/0` or `I/1`,
  shown under **Members**. Joining lands on `viewer`. There is no email delivery
  in this process, so a code the owner can paste into chat is the whole invitation
  flow.
* An account that has not chosen yet has `tenant_id: null`, no permissions, and
  `403 onboarding: create or join a tenant first` on every gateway route.

Everything is scoped by that tenant: gateways, roles, the member list, traffic
stats, the audit log. Reads take the tenant as an argument (`list_gateways(tenant_id)`
and friends), so a missing filter is a type error rather than a leak, and
`/api/gateways/{id}/...` resolves the gateway *through* the tenant — a guessed id
from another tenant is a 404, not a 403 that confirms the id exists.

Gateways written before tenancy (and accounts too) fold to `TENANT_OF_DEPLOYMENT`,
and a boot step (`adopt_legacy_tenants`) gives that tenant a real row so it has a
join code. That is why upgrading an existing installation loses nothing.

Public slugs stay global: `/g/{slug}/mcp` is the URL clients are configured with,
and a per-tenant namespace would need a tenant segment in the path. Two tenants
cannot both own `rancher`.

### Roles

Roles are per tenant.

Permissions are a **closed catalog** (`backend/src/gateway/domain/permissions.py`),
not free-form strings: `gateways:read`, `gateways:write`, `keys:read`, `keys:write`,
`traffic:read`, `audit:read`, `config:read`, `config:write`, `members:read`,
`members:write`, `roles:read`, `roles:write`, `settings:read`, `settings:write`.
A typo is refused with a 422 naming the field, because the alternative is a role
that grants nothing and says nothing — discovered days later as a 403 on a button
the panel offered.

Two roles are built in and cannot be deleted or edited, because neither is a policy
decision and every installation needs them:

| Role | Id | Permissions |
| --- | --- | --- |
| `owner` | `role_owner` | everything |
| `viewer` | `role_viewer` | the read permissions above (`gateways`, `keys`, `traffic`, `audit`, `config`) |

Everything else is defined by the tenant under **Roles**: name plus a set of
permissions. `members:read/write`, `roles:read/write` and `settings:read/write` are
deliberately not in `viewer` — a member list is an address book and sign-in settings
name an identity provider. Sign-in settings are deployment-wide; roles are not.

A new member added without an explicit role lands on **`viewer`**: an omission must
narrow access, never widen it. An administrator adds people under **Members**, and
can change their roles at any time.

Delegation may only narrow: nobody can grant a permission they do not hold
themselves (`_grantable` in `interface/api.py`), otherwise `members:write` would be
a path to `settings:write` — add an account holding the owner role, sign in as it,
done. The default role is exempt, so a recruiter who may add people but not read
gateways can still create a viewer account.

Permissions are resolved **per request** from the read models, not carried in the
cookie. Editing a role or reassigning someone takes effect on their next click; no
re-login, no stale session. `/api/auth/me` returns `role_ids`, role names and the
flat `permissions` list the panel uses to decide what to render — while every route
checks the permission again on the server, since a hidden button is not a rule.

A tenant gets members three ways: the creator owns it, an administrator adds
someone under **Members**, or someone joins with the join code. Only the first path
grants ownership, so a tenant never ends up with two owners.

The session deliberately does not carry the tenant: an account signs up, then
creates or joins one, and a claim about the past cannot answer "which tenant is
this request about". It is read fresh per request, which is also why joining takes
effect on the next click.

The flow is verified against a real OIDC provider in the tests
(`tests/steps/fake_google.py`), including the failures that matter: a callback with
a state this browser never received, an `id_token` minted for a different client, an
unverified address, and a stranger who gets an account with no tenant. The tenancy
scenarios live in `backend/tests/features/tenants.feature` (including two tenants
that must not see each other's gateways, members or roles), RBAC in
`backend/tests/features/roles.feature`, and Playwright suites
(`web/e2e/specs/rbac.spec.js`, `web/e2e/specs/tenants.spec.js`) walk the same paths
through the real UI against a real Postgres:

```bash
cd web && npm run build && npx playwright install chromium && npm run e2e
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
omg export --tenant tn_local > gateways.yaml   # never includes secrets
omg apply gateways.yaml --tenant tn_local      # reconcile; prints new keys
```

`/config` in the UI does the same with a diff-free "validate, then apply".

## Architecture

Event-sourced: every change is an append-only event, read models are projections.
That is what makes the audit log trustworthy and lets two replicas share one
database without a cache-coherence story.

```
backend/src/gateway/
  domain/
    events/base.py    event envelope (Pydantic), shared enums
    events/write.py   events that change an aggregate
    events/read.py    events that only feed read models (traffic)
    model.py          aggregates and invariants — the only place rules live
    readmodels.py     the query side's rows
  application/        command handler, container wiring
  infrastructure/
    db/               SQLAlchemy metadata, engine, session factory
    eventstore/       event log adapter and projections
  proxy/              FastMCP proxy per gateway, guard middleware, rate limiter
  interface/          admin REST API, session cookies, ASGI dispatcher
  log.py              JSON logging for Loki/Grafana
web/                  React admin UI (Kong-style dark theme)
```

Persistence is SQLAlchemy (async, `asyncpg` driver) against Postgres. There is no
string SQL in the application: tables are declared once in
`infrastructure/db/tables.py` and both the event store and the projections query
through the expression language.

The command handler depends on an `EventStore` protocol, not on the Postgres
adapter, so the write path can be tested without a database and a different log is
a wiring change.

### Logs

Every line is one JSON object on stdout, intended for Loki → Grafana:

```json
{"ts":"2026-11-02T11:04:14.538Z","level":"info","logger":"gateway","msg":"proxy.request","event":"proxy.request","gateway":"rancher","subject":"api-key:key_ab12","outcome":"allowed","status":200,"duration_ms":18,"ip":"203.0.113.7"}
```

Useful fields to build dashboards on: `event` (`proxy.request`, `gateway.installed`,
`command.committed`, `http.request`, `traffic.write_failed`), `gateway`, `subject`,
`outcome`, `status`, `duration_ms`. Context bound with `log.context(...)` follows
every line logged inside that block.

Set the level with `OMG_LOG_LEVEL` (`info` by default). uvicorn's own loggers are
routed through the same JSON handler, so there is exactly one format.

```
cd backend && .venv/bin/python -m pytest tests/
```

The suite is Gherkin scenarios against a real ASGI server and a real Postgres in a
container: no mocks of the parts that break.

## License

Apache-2.0
