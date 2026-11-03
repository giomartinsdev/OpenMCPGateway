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

**You configure this in the deployment, not in the panel.** Google sign-in is a
property of the installation, not of a tenant: an owner of one tenant must never be
able to change how everybody signs in. So it comes from the environment, and there
is no screen for it.

Create the OAuth client (type *Web application*) in Google Cloud and register this
redirect URI there — it must match byte for byte, or the exchange fails at Google
with no mention of the gateway:

| variable | what to put |
|---|---|
| `OMG_GOOGLE_CLIENT_ID` | `…apps.googleusercontent.com` |
| `OMG_GOOGLE_CLIENT_SECRET` | the client secret |
| `OMG_OPERATOR_ALLOWLIST` | comma-separated addresses |

The redirect URI is derived from `OMG_BASE_URL` as
`https://<your-host>/api/auth/google/callback`.

The password form keeps working, so a misconfigured IdP never locks you out. If no
client is configured the Google button is not rendered. Signing in creates the
account on first success; what it can see depends on the tenant it then creates or
joins. Changing sign-in configuration means changing the deployment.

`OMG_OPERATOR_ALLOWLIST` is an address book, not a gate: those addresses skip the
tenant choice and land in the installation's own tenant (`TENANT_OF_DEPLOYMENT`)
with the viewer role. Everyone else gets an account with no tenant and is asked
what to do next.

### Tenants

The first thing an account sees after signing up is not the panel but a choice:
**create a tenant** or **join one with a code**.

* Creating a tenant makes the account its `owner`. It is the only path that grants
  ownership, and it needs no permission because the account has none yet. The
  tenant also arrives with one gateway already configured — `console`, pointing
  at this deployment's own read-only MCP (`/mcp`) — and its API key, shown once.
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
`members:write`, `roles:read`, `roles:write`.
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
permissions. `members:read/write` and `roles:read/write` are deliberately not in
`viewer` — a member list is an address book, and roles decide what everyone else may
do. Sign-in configuration has no permission at all, because it is not a tenant's to
change.

A new member added without an explicit role lands on **`viewer`**: an omission must
narrow access, never widen it. An administrator adds people under **Members**, and
can change their roles at any time.

Delegation may only narrow: nobody can grant a permission they do not hold
themselves (`RoleCatalog.ensure_grantable` in
`application/services/roles.py`), otherwise `members:write` would be a path to
`role_owner` — add an account holding the owner role, sign in as it, done. The default role is exempt, so a recruiter who may add people but not read
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

A tenant never starts empty: the `console` gateway created with it points at this
deployment's own read-only MCP, so an agent can be asked "what broke in the last
hour" before anyone configures an upstream:

```jsonc
{
  "mcp": {
    "gateway-console": {
      "type": "remote",
      "url": "https://gateway.example.com/g/console/mcp",
      "headers": { "Authorization": "Bearer omgc_..." }
    }
  }
}
```

Delete it if you would rather point agents at `/mcp` directly. The slug is global,
because `/g/<slug>/mcp` is a public URL, so the first tenant on a deployment gets
`console` and later ones get `console-<tenant id>` — named after the tenant rather
than numbered, so the address still says whose gateway it is.

Each gateway's page in the panel carries a **Test this gateway** card with the
exact requests for its auth mode, as `curl` or Python. A public gateway is one
request; an API-key gateway adds the bearer header; an OAuth gateway shows the
whole chain — the 401 that starts it, discovery, client registration, the
authorize URL, the code-for-token exchange, and finally the authenticated call.
The text is generated server-side (`GET /api/gateways/{id}/connect`) because the
OAuth steps name endpoints the proxy itself serves.

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
caveat. See `ponytail:` comments in `backend/src/gateway/proxy/rate_limit.py`.

## Configuration as code

The UI and a file are equivalent — both reach the same command handler. The file
declares **gateways, roles and members**, so a tenant can be rebuilt from a commit
or managed only by IaC.

```yaml
gateways:
  - slug: rancher
    name: Rancher cluster
    target_url: http://rancher-mcp.support.svc.cluster.local:8000/mcp
    auth:
      mode: oauth
      idp: github
      client_id: ${GITHUB_CLIENT_ID}
      client_secret: ${GITHUB_CLIENT_SECRET}
      upstream_token: ${RANCHER_MCP_TOKEN}
    rate_limit: 120
    allow: [giomartinsdev]

roles:
  - name: Auditor
    permissions: [gateways:read, traffic:read, audit:read]

members:
  - email: auditor@example.com
    password: ${MEMBER_PASSWORD}   # only used when the account is new
    roles: [Auditor]

prune: false
```

```bash
omg export --tenant tn_local > gateways.yaml   # never includes secrets
omg apply gateways.yaml --tenant tn_local      # reconcile; prints new keys
```

Merge semantics: the file is authoritative for what it declares. A gateway left
out survives, so a typo cannot delete production; `prune: true` opts into
deletion, and it applies to gateways only — members are never pruned by
omission. Built-in roles (`owner`, `viewer`) may be named but not redefined.
Applying the file obeys the same delegation rule as the panel: nobody grants a
permission it does not hold itself.

`/config` in the UI does the same with a diff-free "validate, then apply".

## Documentation

`/#/docs` serves the full manual from the running deployment — concepts, every
gateway field, auth modes, the config-as-code schema, the HTTP API, the console
MCP, environment variables and a troubleshooting table. It is reachable signed
out, and linked from the landing page. The copy lives in
`backend/src/gateway/docs/sections.json` and is served by `GET /api/docs`, so the
panel and the console MCP read the same text.

## Schema

The schema is versioned in code: `backend/src/gateway/infrastructure/db/migrations.py`
holds a numbered list of steps, and `schema_migrations` records what a database
has already applied. Boot runs whatever this build has not seen, inside one
transaction behind an advisory lock, so a rollout is the upgrade path and two
pods starting together queue instead of colliding.

Tables prefixed `rm_` are read models — projections of the event log,
rebuildable by replay. `events` is the log itself.

## Architecture

Event-sourced: every change is an append-only event, read models are projections.
That is what makes the audit log trustworthy and lets two replicas share one
database without a cache-coherence story.

```
backend/src/gateway/
  domain/
    events/           event envelope (Pydantic), shared enums
    model/            aggregates, one module per stream — the only place rules live
    permissions.py    the closed permission catalog and built-in roles
    readmodels.py     the query side's rows
  application/
    commands.py       the command handler
    principal.py      `Caller` — who a use case acts for
    services/         use cases: gateways, members, roles, sign-in, tenants,
                      traffic, config export, connection snippets
  infrastructure/
    db/               SQLAlchemy metadata, engine, session factory
    eventstore/       event log adapter; projections split into
                      handlers / runner / read models
    google_oidc.py    outbound Google OIDC client
  proxy/              FastMCP proxy per gateway: router, guard middleware,
                      identity, rate limiter, provider factory
  interface/          HTTP only: routers per concern, dependencies, schemas,
                      error handlers, middleware, ASGI dispatcher
  configexport.py     current state rendered back to YAML
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
