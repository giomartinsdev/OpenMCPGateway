// The manual. One page, sections anchored by id, and a sticky table of contents
// — a docs *site* with its own router would be a second app to keep in sync with
// this one, and the whole manual fits in a page.
//
// Content is a data structure, not JSX: the TOC, the anchors and the prose come
// from the same array, so a section can never be listed and missing.

import { useEffect, useState } from "react";

const SECTIONS = [
  {
    id: "overview",
    title: "What this is",
    body: [
      "OpenMCPGateway sits in front of one or more MCP servers. Clients point at `https://<host>/g/<slug>/mcp`; the gateway authenticates the caller, applies an allowlist and a per-minute budget, forwards the request to the real server over the private network, and records what happened.",
      "The upstream does not need to be reachable from the internet, and it does not need to know who the caller is: the gateway terminates identity and forwards its own credential when the upstream demands one (`upstream_token`).",
    ],
    table: {
      head: ["Layer", "Behaviour"],
      rows: [
        ["Authentication", "OAuth proxy per gateway (Google, GitHub, Microsoft, Discord, custom OIDC) or a bearer key hashed with SHA-256"],
        ["Authorisation", "Optional allowlist of subjects. Empty means “any authenticated caller”"],
        ["Rate limit", "Per subject, per gateway, counting JSON-RPC requests. Handshakes are free"],
        ["Observability", "Every attempt logged: subject, outcome, status, latency, IP"],
        ["Fail closed", "An OAuth gateway without credentials answers 503, never as an open proxy"],
      ],
    },
  },
  {
    id: "quickstart",
    title: "Quickstart",
    body: [
      "You need a Postgres the gateway can reach and a hostname that terminates TLS in front of it.",
      "```bash\n# 1. run it\nexport OMG_DATABASE_URL=postgresql://omg:omg@127.0.0.1:5432/omg\nexport OMG_SESSION_SECRET=$(openssl rand -hex 32)\nexport OMG_BASE_URL=https://gateway.example.com\nomg serve\n\n# 2. create the first account, then a tenant, in the browser\nopen https://gateway.example.com\n```",
      "The first account creates a tenant and becomes its `owner`. Everyone else either joins with the tenant's join code or is added by an administrator.",
      "Point a client at a gateway as soon as one exists:",
      "```jsonc\n// opencode.json\n{\n  \"mcp\": {\n    \"my-server\": {\n      \"type\": \"remote\",\n      \"url\": \"https://gateway.example.com/g/my-server/mcp\"\n    }\n  }\n}\n```",
    ],
  },
  {
    id: "gateways",
    title: "Gateways",
    body: [
      "A gateway is a slug, an upstream URL, and a policy. The slug is global: `/g/<slug>/mcp` is the string people paste into clients, so two tenants cannot both own `rancher`.",
      "`target_url` is the streamable-HTTP endpoint of the upstream, including its path (`http://rancher-mcp.support.svc.cluster.local:8000/mcp`). It must be `http://` or `https://`.",
    ],
    table: {
      head: ["Field", "Meaning"],
      rows: [
        ["slug", "`[a-z0-9-]+`, 2–40 chars. Appears in the public path"],
        ["target_url", "Upstream MCP endpoint, including the path"],
        ["auth_mode", "`none`, `api_key` or `oauth`"],
        ["idp", "With `oauth`: `github`, `google`, `azure`, `discord` or `oidc`"],
        ["upstream_token", "Bearer the upstream demands. Write-only: never returned by the API"],
        ["rate_limit", "Requests per minute per subject. `0` means unlimited"],
        ["enabled", "A disabled gateway answers 404, not 403 — it is not advertised"],
      ],
    },
  },
  {
    id: "auth",
    title: "Authentication modes",
    body: [
      "**`none`** — no authentication. Fine on a private network; the panel warns while any such gateway is enabled.",
      "**`api_key`** — the caller sends `Authorization: Bearer omg_…`. Keys are stored as SHA-256 hashes and shown in plaintext exactly once, in the response that minted them.",
      "**`oauth`** — the gateway is its own OAuth resource server. A client with no token gets a `401` with `WWW-Authenticate: Bearer resource_metadata=…`; it discovers the metadata documents, registers itself, and opens a browser. The operator approves once, and the subject is allowlisted from then on.",
      "The client does not need a token checked into a dotfile for OAuth mode — that is the point. For API-key mode it does, which is why the key is per gateway and revocable on its own.",
      "```bash\n# API-key call\ncurl -sS https://gateway.example.com/g/rancher/mcp \\\n  -H 'Authorization: Bearer omg_…' \\\n  -H 'Content-Type: application/json' \\\n  -H 'Accept: application/json, text/event-stream' \\\n  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl\",\"version\":\"0\"}}}'\n```",
    ],
  },
  {
    id: "rbac",
    title: "Members and roles",
    body: [
      "Everything is scoped to a tenant: gateways, members, roles, traffic, the audit log. An id from another tenant is a 404, not a 403 that confirms it exists.",
      "Permissions are a closed catalog. A typo is refused with a 422 naming the field, because the alternative is a role that grants nothing and says nothing.",
    ],
    table: {
      head: ["Permission", "Grants"],
      rows: [
        ["gateways:read / write", "See the gateway list, and change slugs, targets, auth and limits"],
        ["keys:read / write", "See key ids, and issue, rotate or revoke them"],
        ["traffic:read", "Read request stats and the recent-request log"],
        ["audit:read", "Read the append-only event log"],
        ["config:read / write", "Export and apply the YAML file"],
        ["members:read / write", "See the address book, and add people"],
        ["roles:read / write", "See the role table, and define or edit roles"],
      ],
    },
    bodyAfter: [
      "Two roles are built in and cannot be edited or deleted, because neither is a policy decision:",
      "| Role | Id | Permissions |\n| --- | --- | --- |\n| `owner` | `role_owner` | everything |\n| `viewer` | `role_viewer` | every read permission |",
      "A member added without a role lands on `viewer`: an omission narrows access, never widens it. Delegation may only narrow too — nobody can grant a permission they do not hold themselves, or `members:write` would be a path to `role_owner`. The same rule applies to the config file.",
      "Permissions are resolved per request from the read models, so editing a role or reassigning someone takes effect on their next click. No re-login, no stale session.",
    ],
  },
  {
    id: "config",
    title: "Config as code",
    body: [
      "The panel and a YAML file are two front doors onto the same command handler. Neither is privileged: a gateway created in the browser exports to the file, and a file applied by CI lands in the same event log as a click.",
      "`gateways`, `roles` and `members` are all declared in the file, so a tenant can be rebuilt from a commit. Secrets are never exported — write `${ENV_VAR}` and the server resolves it from its own environment at apply time.",
      "```yaml\n# gateways.yaml\ngateways:\n  - slug: rancher\n    name: Rancher cluster\n    target_url: http://rancher-mcp.support.svc.cluster.local:8000/mcp\n    description: read the cluster, restart and scale\n    auth:\n      mode: oauth\n      idp: google\n      client_id: ${GOOGLE_CLIENT_ID}\n      client_secret: ${GOOGLE_CLIENT_SECRET}\n      upstream_token: ${RANCHER_MCP_TOKEN}\n    rate_limit: 120\n    allow: [someone@example.com]\n\nroles:\n  - name: Auditor\n    permissions: [gateways:read, traffic:read, audit:read]\n\nmembers:\n  - email: auditor@example.com\n    password: ${MEMBER_PASSWORD}\n    roles: [Auditor]\n\nprune: false\n```",
      "Merge semantics: the file is authoritative for what it declares. A gateway left out of the file survives, so a typo cannot delete production. `prune: true` opts into deletion — it applies to gateways only; members are never pruned by omission, because an account that disappears from a file is far more likely to be a merge accident than an intention.",
      "Members reconcile by email: an existing member whose roles differ gets them replaced, and one whose roles match is left alone. `password` is only used when the account does not exist yet, so it appears in a reviewed file as `${MEMBER_PASSWORD}` and never in an export — which is also why exporting and re-applying is a no-op. A new account with no password is refused, because inventing one would be a credential nobody can sign in with.",
      "A role whose name matches a built-in (`owner`, `viewer`) may be named but not redefined: the file would otherwise narrow every existing member at once.",
    ],
    bodyAfter: [
      "```bash\n# round-trip from the shell\nomg export --tenant tn_local > gateways.yaml\nomg apply gateways.yaml --tenant tn_local\n```",
      "`omg apply` prints what changed and any errors, and exits non-zero when a declaration failed — which is what makes it usable in CI. Newly issued API keys are printed once, because that is the only time the plaintext exists.",
      "The panel's Config as code page does the same thing: **Validate** parses and resolves placeholders without touching state, **Apply** runs the commands.",
    ],
  },
  {
    id: "api",
    title: "HTTP API",
    body: [
      "Everything the panel does is an endpoint under `/api`, authenticated by an `HttpOnly` session cookie. The MCP routes themselves live at `/g/<slug>/mcp` and speak JSON-RPC, not this API.",
    ],
    table: {
      head: ["Method and path", "Does"],
      rows: [
        ["POST /api/auth/login", "Sign in with email and password"],
        ["GET /api/auth/me", "Identity, tenant, roles and flat permission list"],
        ["GET /api/gateways", "Every gateway in the caller's tenant"],
        ["POST /api/gateways", "Create one (requires `gateways:write`)"],
        ["GET /api/gateways/{id}/connect", "Generated copy-paste guide for that gateway"],
        ["PUT /api/gateways/{id}/auth", "Auth mode, IdP credentials, upstream token"],
        ["POST /api/gateways/{id}/keys", "Issue an API key; plaintext returned once"],
        ["POST /api/gateways/{id}/keys/{kid}/rotate", "Rotate in place: old key dies in the same append"],
        ["GET /api/stats?minutes=60", "Totals, averages, by-outcome, by-IP, series"],
        ["GET /api/requests?limit=100", "Recent request log"],
        ["GET /api/events?limit=50", "Audit log"],
        ["POST /api/config/validate", "Parse a document without applying it"],
        ["POST /api/config/apply", "Reconcile gateways, roles and members"],
        ["GET /api/config/export", "Render current state as YAML, without secrets"],
      ],
    },
  },
  {
    id: "ops",
    title: "Operating it",
    body: [
      "Logs are one JSON object per line on stdout, built for Loki → Grafana. Useful fields to build dashboards on: `event`, `gateway`, `subject`, `outcome`, `status`, `duration_ms`. Set the level with `OMG_LOG_LEVEL`.",
      "```json\n{\"ts\":\"2026-11-02T11:04:14.538Z\",\"level\":\"info\",\"logger\":\"gateway\",\"msg\":\"proxy.request\",\"event\":\"proxy.request\",\"gateway\":\"rancher\",\"subject\":\"api-key:key_ab12\",\"outcome\":\"allowed\",\"status\":200,\"duration_ms\":18,\"ip\":\"203.0.113.7\"}\n```",
      "Rate limiting is in-process: with N replicas the effective limit is N×. Correct for a single-replica deployment, which is the current design.",
      "State is event-sourced. Every change is an append-only event and the read models are projections of it, which is what makes the audit log trustworthy and lets two replicas share one database without a cache-coherence story.",
    ],
    table: {
      head: ["Symptom", "What it usually is"],
      rows: [
        ["503 on a gateway route", "An OAuth gateway with no client credentials. Fail closed, by design"],
        ["401 on every call", "The upstream token is wrong or unset, or the client's key was rotated"],
        ["403 with a subject", "The gateway has an allowlist and this subject is not on it"],
        ["429", "Per-subject budget reached for that gateway"],
        ["Tools appear to hang", "Proxy buffering on the ingress in front of the gateway: turn it off, raise the read timeout"],
      ],
    },
  },
  {
    id: "env",
    title: "Environment",
    body: ["Sign-in configuration is deployment configuration, not tenant configuration: an owner of one tenant must never be able to change how everybody signs in. So it comes from the environment, and there is no screen for it."],
    table: {
      head: ["Variable", "Meaning"],
      rows: [
        ["OMG_DATABASE_URL", "Postgres DSN (required)"],
        ["OMG_SESSION_SECRET", "At least 32 bytes (required)"],
        ["OMG_BASE_URL", "Public URL; the OAuth redirect is derived from it"],
        ["OMG_ADMIN_EMAIL / OMG_ADMIN_PASSWORD", "Optional bootstrap account"],
        ["OMG_GOOGLE_CLIENT_ID / SECRET", "Google sign-in for the panel itself"],
        ["OMG_OPERATOR_ALLOWLIST", "Comma-separated addresses that may create an account"],
        ["OMG_LOG_LEVEL", "`info` by default"],
        ["OMG_CONFIG_FILE", "Optional YAML applied at boot"],
      ],
    },
  },
];

/** Split `code fences` out of prose so they render as blocks, not inline text. */
function Prose({ text }) {
  const parts = text.split(/```[a-z]*\n?/);
  const fenced = (text.match(/```[a-z]*\n?/g) ?? []).length;
  if (!fenced) return <Inline text={text} />;
  return parts.map((part, index) =>
    index % 2 === 1 ? (
      <pre className="code docs-code" key={index}>
        {part.replace(/\n$/, "")}
      </pre>
    ) : (
      <Inline text={part.trim()} key={index} />
    ),
  );
}

/** `inline code` inside a paragraph, without pulling in a markdown parser. */
function Inline({ text }) {
  if (!text) return null;
  const pieces = text.split(/(`[^`]+`)/g);
  return (
    <p>
      {pieces.map((piece, index) =>
        piece.startsWith("`") ? (
          <code key={index}>{piece.slice(1, -1)}</code>
        ) : (
          <span key={index}>{piece}</span>
        ),
      )}
    </p>
  );
}

function Table({ table }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {table.head.map((cell) => (
              <th key={cell}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, index) => (
                <td key={index} className={index === 0 ? "strong" : ""}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Docs({ signedIn = false }) {
  const [active, setActive] = useState(SECTIONS[0].id);

  // Highlight the section you are reading, not the one you clicked. The
  // observer's rootMargin leaves the top third of the viewport as "current".
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) setActive(visible.target.id);
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );
    SECTIONS.forEach((section) => {
      const node = document.getElementById(section.id);
      if (node) observer.observe(node);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="docs">
      <div className="docs-head">
        <span className="eyebrow">Documentation</span>
        <h1>The manual</h1>
        <p className="sub">
          What the gateway does, how a request gets through, and how to keep the
          configuration in a file you can review. Apache-2.0, self-hosted, no account
          to create.
        </p>
        <div className="row" style={{ marginTop: "var(--s4)" }}>
          <a
            className="cta-secondary docs-cta"
            href="https://github.com/giomartinsdev/OpenMCPGateway"
            target="_blank"
            rel="noreferrer"
          >
            Source on GitHub
          </a>
          {/* Signed out, `#/` is the landing page and its sign-in form; signed
              in, it is the gateway list. Same link, honest label for each. */}
          <a className="cta-secondary docs-cta" href="#/">
            {signedIn ? "Back to the panel" : "Sign in"}
          </a>
        </div>
      </div>

      <div className="docs-body">
        <nav className="docs-toc" aria-label="Sections">
          <div className="nav-label">On this page</div>
          {SECTIONS.map((section) => (
            <a
              key={section.id}
              href={`#/docs`}
              onClick={(event) => {
                event.preventDefault();
                document
                  .getElementById(section.id)
                  ?.scrollIntoView({ behavior: "smooth", block: "start" });
                setActive(section.id);
              }}
              className={active === section.id ? "active" : ""}
            >
              {section.title}
            </a>
          ))}
        </nav>

        <article className="docs-article">
          {SECTIONS.map((section) => (
            <section key={section.id} id={section.id} className="docs-section">
              <h2>{section.title}</h2>
              {section.body.map((text, index) => (
                <Prose text={text} key={index} />
              ))}
              {section.table ? <Table table={section.table} /> : null}
              {(section.bodyAfter ?? []).map((text, index) =>
                text.startsWith("|") ? (
                  <Table key={index} table={parseTable(text)} />
                ) : (
                  <Prose text={text} key={index} />
                ),
              )}
            </section>
          ))}
          <footer className="docs-foot">
            <span>OpenMCPGateway · Apache-2.0</span>
            <a href="https://github.com/giomartinsdev/OpenMCPGateway#readme" target="_blank" rel="noreferrer">
              README →
            </a>
          </footer>
        </article>
      </div>
    </div>
  );
}

/** A markdown table written inline in `bodyAfter`, parsed into the same shape. */
function parseTable(text) {
  const rows = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|"))
    .map((line) =>
      line
        .slice(1, -1)
        .split("|")
        .map((cell) => cell.trim().replaceAll("`", "")),
    );
  const [head, , ...body] = rows;
  return { head, rows: body };
}
