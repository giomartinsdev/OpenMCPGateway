import { AuthCard } from "./Login.jsx";
import { LanguagePicker, useT } from "../i18n.jsx";
import { Mark } from "../Mark.jsx";
import { ThemeToggle, useTheme } from "../theme.jsx";

// Copy lives here, not in the form: the landing is a pitch and the form is a
// component, and mixing them is how a login page becomes a brochure.
const FEATURES = [
  {
    icon: "{ }",
    title: "One endpoint per MCP server",
    body: "Clients point at /g/<slug>/mcp. The gateway terminates OAuth or a bearer key, then calls the real server over the private network.",
  },
  {
    icon: "403",
    title: "Identity you already have",
    body: "GitHub, Google, Microsoft, Discord or any OIDC provider. Who may call is decided by the tenant that owns the gateway.",
  },
  {
    icon: "429",
    title: "Per-caller budgets",
    body: "Requests per minute per subject, counting JSON-RPC calls only. Handshakes and notifications are free.",
  },
  {
    icon: "log",
    title: "Every call is a row",
    body: "Subject, outcome, status, latency and IP, appended to an event log. Secret values are redacted before they are stored.",
  },
  {
    icon: "yaml",
    title: "Panel and file are the same",
    body: "Export the tenant to YAML, edit it, apply it back. Both paths reach one command handler, so they cannot disagree.",
  },
  {
    icon: "log",
    title: "A console you can ask",
    body: "Every tenant ships with a gateway to this deployment's own read-only MCP. Point an agent at /g/console/mcp and ask what broke, or what the audit log says.",
  },
  {
    icon: "tn",
    title: "Tenants, not accounts",
    body: "Gateways, roles, members and traffic are scoped to a tenant. A guessed id from another one is a 404.",
  },
];

const STEPS = [
  {
    title: "Point it at your server",
    body: "Give the gateway a slug and the upstream MCP URL. The upstream stays unreachable from outside; the gateway is the only door.",
  },
  {
    title: "Choose who gets in",
    body: "No auth for a private network, a bearer key for machines, or SSO for people. Each gateway decides for itself.",
  },
  {
    title: "Paste the endpoint",
    body: "Copy /g/<slug>/mcp into your client. For OAuth, the client discovers the rest from the 401 challenge on its own. Every tenant starts with one gateway already there: /g/console/mcp, the deployment's own read-only console.",
  },
];

const LOG = [
  { t: "cmd", text: "docker compose up -d --build", cls: "" },
  { t: "", text: "", cls: "" },
  { t: "#", text: "the gateway is the only door to the upstream", cls: "c" },
  { t: "$", text: "curl -sS https://gateway.example.com/g/rancher/mcp \\", cls: "" },
  { t: "", text: "  -H 'Accept: application/json'", cls: "" },
  { t: "→", text: "401  WWW-Authenticate: Bearer resource_metadata=…", cls: "d" },
  { t: "", text: "", cls: "" },
  { t: "#", text: "the client registers, the operator approves once", cls: "c" },
  { t: "→", text: "200  tools/list  ·  8 tools  ·  42 ms", cls: "s" },
];

export function Landing({ onLogin }) {
  const t = useT();
  const [theme, setTheme] = useTheme();
  return (
    <div className="landing">
      <nav className="landing-nav">
        <div className="logo">
          <Mark size={28} />
          <div className="logo-text">
            OpenMCP<span>Gateway</span>
          </div>
        </div>
        <div className="links">
          <a href="#how">{t("How it works")}</a>
          <a href="#features">{t("Features")}</a>
          {/* `#/docs` is a route, not an anchor: the manual is a page of the app,
              reachable signed out, so the pitch and the reference cannot drift. */}
          <a href="#/docs">{t("Docs")}</a>
          <a href="https://github.com/giomartinsdev/OpenMCPGateway" target="_blank" rel="noreferrer">
            GitHub
          </a>
          <LanguagePicker />
          <ThemeToggle theme={theme} onSelect={setTheme} />
          <a className="pill-btn" href="#signin">{t("Sign in")}</a>
        </div>
      </nav>

      <header className="hero">
        <div>
          <span className="tag">
            <span className="dot" /> {t("Apache-2.0 · self-hosted")}
          </span>
          <h1>
            {t("Auth, limits and logs in front of any")} <em>{t("MCP server")}</em>.
          </h1>
          <p className="lead">
            {t("One endpoint per server, an identity provider you already run, and a call log that answers \"who used this and what broke\". No static token copied into five laptops — and every new tenant starts with a gateway to the deployment's own read-only console, so there is something to ask on day one.")}
          </p>
          <div className="cta">
            <a className="cta-primary" href="#signin">{t("Open the panel")}</a>
            <a className="cta-secondary" href="#/docs">
              {t("Read the docs")}
            </a>
          </div>
          <p className="fineprint">
            {t("No account to create here: the panel signs in against this deployment's own identity provider.")}
          </p>
        </div>

        <div className="terminal" aria-hidden="true">
          <div className="terminal-bar">
            <i />
            <i />
            <i />
            <span style={{ marginLeft: 6 }}>gateway</span>
          </div>
          <pre>
            {LOG.map((line, i) => (
              <div key={i}>
                <span className={line.cls || "d"}>{line.t ? `${line.t} ` : "  "}</span>
                <span className={line.cls}>{line.text}</span>
              </div>
            ))}
          </pre>
        </div>
      </header>

      <section className="band" id="signin">
        <div className="signin-band">
          <div>
            <h2>{t("Already have an account?")}</h2>
            <p>
              {t("Sign in to manage gateways, keys, roles and traffic. Signing up gives you an identity and nothing else — you then create a tenant or join one with a code.")}
            </p>
          </div>
          <AuthCard onLogin={onLogin} />
        </div>
      </section>

      <section className="band" id="how">
        <header>
          <h2>{t("Three decisions, in this order")}</h2>
          <p>
            {t("The gateway does not care what your MCP server does. It decides who reaches it, how often, and leaves a record.")}
          </p>
        </header>
        <div className="steps">
          {STEPS.map((step) => (
            <div className="step" key={step.title}>
              <h3>{t(step.title)}</h3>
              <p>{t(step.body)}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="band" id="features">
        <header>
          <h2>{t("What it does, and what it refuses")}</h2>
          <p>
            {t("Fail closed is the default: an OAuth gateway with no credentials answers 503, never as an open proxy.")}
          </p>
        </header>
        <div className="features">
          {FEATURES.map((feature) => (
            <div className="feature" key={feature.title}>
              <div className="ico">{feature.icon}</div>
              <h3>{t(feature.title)}</h3>
              <p>{t(feature.body)}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="band" id="docs">
        <header>
          <h2>{t("The manual is in the product")}</h2>
          <p>
            {t("Not a README on another tab: the same deployment serves the reference, so what you read is the version you are running.")}
          </p>
        </header>
        <div className="docs-band">
          <a className="docs-card" href="#/docs">
            <span className="eyebrow">{t("Reference")}</span>
            <h3>{t("Gateways, auth, limits, logs")}</h3>
            <p>
              {t("How a request gets through, what each auth mode costs you, and every field on a gateway.")}
            </p>
          </a>
          <a className="docs-card" href="#/docs">
            <span className="eyebrow">{t("Operations")}</span>
            <h3>{t("Config as code, roles and members")}</h3>
            <p>
              {t("The full YAML schema — gateways, roles and members — with the merge and prune semantics spelled out.")}
            </p>
          </a>
          <a className="docs-card" href="#/docs">
            <span className="eyebrow">{t("Troubleshooting")}</span>
            <h3>503, 401, 403, 429</h3>
            <p>
              {t("What each refusal means, and the ingress setting that makes an MCP stream look like a hang.")}
            </p>
          </a>
        </div>
      </section>

      <footer className="landing-foot">
        <span>OpenMCPGateway · Apache-2.0</span>
        <div className="row">
          <a href="https://github.com/giomartinsdev/OpenMCPGateway">{t("Source")}</a>
          <a href="#/docs">{t("Docs")}</a>
          <a href="#signin">{t("Sign in")}</a>
        </div>
      </footer>
    </div>
  );
}
