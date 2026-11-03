"""The OAuth pages a user actually sees, in the panel's own design.

FastMCP renders its consent and error pages with its own light theme, and offers
no hook to replace them: `oauth_proxy.consent` and `oauth_proxy.proxy` import
`create_consent_html` / `create_error_html` by name and call them directly. So
the two names are rebound here, once, at import of this module.

What is *not* replaced matters as much as what is: the transaction lookup, the
CSRF double-submit cookie, the browser-binding check and the redirect-target
validation all stay FastMCP's. This module only decides what the page looks
like — the hidden form fields keep the names and the `name="…" value="…"`
attribute order the form handler and the tests read.

ponytail: rebinding module globals is a patch, not an API. Upgrade to a real
template hook if FastMCP grows one.
"""

from __future__ import annotations

import html as htmllib

_INSTALLED = False

# One page, so one stylesheet. Values mirror `web/src/styles.css` — the panel is
# the only place these tokens are authored; keep them in sync by eye, not by a
# build step, because this HTML is served by Python with no bundler in reach.
#
# No remote font: the CSP below forbids it, and the page must render correctly
# on a machine that cannot reach the font CDN at all. The stack degrades to the
# platform sans, which is why the type scale is set in relative steps, not in
# pixel-exact sizes copied from the panel.
_STYLES = """
  :root {
    --bg: #0f0e0d; --panel: #151412; --panel-2: #1b1a17; --panel-3: #22201d;
    --glass: rgba(21, 20, 18, .93);
    --line: rgba(250, 250, 249, .08); --line-2: rgba(250, 250, 249, .16);
    --line-3: rgba(250, 250, 249, .25);
    --fg: #fafaf9; --fg-2: #d6d4cf; --fg-3: #a6a39c; --fg-4: #85827c; --fg-5: #6f6c66;
    --apur: #3dd68c; --warn: #e8b43a; --danger: #e4784f;
    --sans: "Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --serif: "Faustina", Georgia, "Times New Roman", serif;
    --mono: "Geist Mono", ui-monospace, "SF Mono", Menlo, monospace;
    --radius: 16px; --radius-sm: 6px; --pill: 100px;
    --t-body: 13px; --t-label: 12px; --t-micro: 11px;
    --ease: cubic-bezier(.4,0,.2,1); --hover: .12s;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; padding: 24px;
    background: radial-gradient(110% 90% at 55% 50%, #141310, var(--bg) 58%, #0b0a09);
    color: var(--fg); font: 400 var(--t-body)/1.45 var(--sans);
    -webkit-font-smoothing: antialiased;
  }
  .card {
    width: 100%; max-width: 34rem;
    background: var(--glass); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 22px;
  }
  .logo { display: flex; align-items: center; gap: 9px; margin-bottom: 20px; }
  .logo-mark { width: 8px; height: 22px; background: var(--apur); border-radius: 2px; }
  .logo-text { color: var(--fg); font: 500 14px/1 var(--sans); letter-spacing: -.01em; }
  .logo-text span { color: var(--fg-3); }
  h1 {
    margin: 0 0 8px; color: var(--fg);
    font: 400 27px/1.15 var(--serif); letter-spacing: -.015em;
  }
  .sub { color: var(--fg-3); font-size: var(--t-body); margin-bottom: 16px; }
  .sub strong { color: var(--fg); font-weight: 500; }
  .info {
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: var(--radius-sm); padding: 11px 13px; margin-bottom: 12px;
    font-size: var(--t-body); color: var(--fg-2);
  }
  .info strong { color: var(--fg); font-weight: 500; }
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    height: 22px; padding: 0 8px; margin-bottom: 12px;
    border: 1px solid rgba(61,214,140,.28); border-radius: var(--radius-sm);
    background: rgba(61,214,140,.12); color: var(--apur);
    font: 500 var(--t-label)/1 var(--sans); white-space: nowrap;
  }
  .badge strong { font-weight: 500; }
  .target {
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: var(--radius-sm); padding: 11px 13px; margin-bottom: 12px;
  }
  .target .label {
    display: block; margin-bottom: 5px; color: var(--fg-3);
    font: 500 var(--t-micro)/1.3 var(--sans);
  }
  .target .value {
    font-family: var(--mono); font-size: var(--t-label); color: var(--fg);
    word-break: break-all;
  }
  details { margin-bottom: 4px; }
  summary {
    color: var(--fg-4); font: 500 var(--t-micro)/1.3 var(--sans);
    cursor: pointer; padding: 6px 0; user-select: none;
  }
  summary:hover { color: var(--fg-2); }
  .details {
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: var(--radius-sm); padding: 4px 13px; margin-top: 4px;
  }
  .row {
    display: flex; gap: 12px; padding: 7px 0;
    font-size: var(--t-label);
    border-bottom: 1px solid var(--line);
  }
  .row:last-child { border-bottom: 0; }
  .row .k { color: var(--fg-4); min-width: 132px; flex: 0 0 auto; }
  .row .v { color: var(--fg-2); font-family: var(--mono); word-break: break-all; }
  .actions { display: flex; gap: 8px; margin-top: 18px; }
  button {
    display: inline-flex; align-items: center; justify-content: center;
    height: 34px; padding: 0 16px;
    border: 1px solid var(--line-2); border-radius: var(--pill);
    background: rgba(21,20,18,.6); color: var(--fg);
    font: 500 var(--t-body)/1 var(--sans); cursor: pointer; white-space: nowrap;
    transition: background-color var(--hover) var(--ease), border-color var(--hover) var(--ease);
  }
  button:hover { background: var(--panel-2); border-color: var(--line-3); }
  /* Emphasis is an inversion, not a colour — the accent is reserved for state. */
  .primary {
    background: var(--fg); border-color: var(--fg); color: var(--bg);
    font-weight: 500; flex: 1;
  }
  .primary:hover { background: var(--fg-2); border-color: var(--fg-2); color: var(--bg); }
  .deny:hover {
    background: rgba(228,120,79,.12); border-color: var(--danger); color: var(--danger);
  }
  .foot { color: var(--fg-4); font-size: var(--t-micro); line-height: 1.6; margin-top: 16px; }
  .foot a { color: var(--fg-2); text-decoration: none; border-bottom: 1px solid var(--line-3); }
  .foot a:hover { color: var(--fg); border-bottom-color: var(--fg); }
  .error {
    background: rgba(228,120,79,.1); border: 1px solid rgba(228,120,79,.3);
    border-radius: var(--radius-sm); padding: 11px 13px; margin-bottom: 12px;
    color: #f0c4b2; font-size: var(--t-body);
  }
  .mono { font-family: var(--mono); font-size: var(--t-label); }
"""

# The pages carry no script and no remote asset, so the policy can stay this
# tight. `form-action` is deliberately absent: the browser applies it across the
# whole redirect chain, which breaks callbacks that hand off to a custom scheme.
_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'"


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="Content-Security-Policy" content="{_CSP}" />
  <title>{htmllib.escape(title)}</title>
  <style>{_STYLES}</style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-mark"></div>
      <div class="logo-text">OpenMCP<span>Gateway</span></div>
    </div>
    {body}
  </div>
</body>
</html>
"""


def _logo_subtitle(server_name: str | None) -> str:
    return htmllib.escape(server_name or "MCP server")


def consent_html(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
    client_name: str | None = None,
    title: str = "Application Access Request",
    server_name: str | None = None,
    server_icon_url: str | None = None,
    server_website_url: str | None = None,
    client_website_url: str | None = None,
    csp_policy: str | None = None,
    is_cimd_client: bool = False,
    cimd_domain: str | None = None,
) -> str:
    """The consent interstitial, as the panel's dark card.

    Same inputs as FastMCP's, same form contract — a different skin only. The
    `name`/`value` attribute order is load-bearing: the form handler reads it,
    and so does `tests/steps/test_oauth.py` when it approves.
    """
    del server_icon_url, csp_policy  # No remote images; the policy is fixed above.

    client_label = htmllib.escape(client_name or client_id)
    server_label = htmllib.escape(server_name or "this MCP server")

    badge = ""
    if is_cimd_client and cimd_domain:
        badge = (
            '<div class="badge"><span>&#x2713;</span> Verified domain: '
            f"<strong>{htmllib.escape(cimd_domain)}</strong></div>"
        )

    rows = [
        ("Application", client_name or client_id),
        ("Website", client_website_url or "N/A"),
        ("Application ID", client_id),
        ("Callback", redirect_uri),
        ("Scopes", ", ".join(scopes) if scopes else "None"),
    ]
    details = "".join(
        f'<div class="row"><div class="k">{htmllib.escape(k)}</div>'
        f'<div class="v">{htmllib.escape(v)}</div></div>'
        for k, v in rows
    )

    body = f"""
    <h1>{htmllib.escape(title)}</h1>
    <p class="sub">
      <strong>{client_label}</strong> wants to call {server_label}.
    </p>
    {badge}
    <div class="target">
      <span class="label">Credentials will be sent to</span>
      <div class="value">{htmllib.escape(redirect_uri)}</div>
    </div>
    <details>
      <summary>Advanced details</summary>
      <div class="details">{details}</div>
    </details>
    <form id="consentForm" method="POST" action="">
      <input type="hidden" name="txn_id" value="{htmllib.escape(txn_id)}" />
      <input type="hidden" name="csrf_token" value="{htmllib.escape(csrf_token)}" />
      <input type="hidden" name="submit" value="true" />
      <div class="actions">
        <button type="submit" name="action" value="approve" class="primary">Allow access</button>
        <button type="submit" name="action" value="deny" class="deny">Deny</button>
      </div>
    </form>
    <p class="foot">
      Approving lets this client act as you on this server. Only continue if you
      started this request. <a href="https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices#confused-deputy-problem"
      target="_blank" rel="noopener noreferrer">Why this screen exists</a>.
    </p>
    """
    return _page(title, body)


def error_html(
    error_title: str,
    error_message: str,
    error_details: dict[str, str] | None = None,
    server_name: str | None = None,
    server_icon_url: str | None = None,
) -> str:
    """The failure page: what broke, and where to try again."""
    del server_icon_url

    rows = "".join(
        f'<div class="row"><div class="k">{htmllib.escape(k)}</div>'
        f'<div class="v">{htmllib.escape(v)}</div></div>'
        for k, v in (error_details or {}).items()
    )
    details = f'<div class="details">{rows}</div>' if rows else ""

    body = f"""
    <h1>{htmllib.escape(error_title)}</h1>
    <p class="sub">{_logo_subtitle(server_name)}</p>
    <div class="error">{htmllib.escape(error_message)}</div>
    {details}
    <p class="foot">
      Close this tab and start again from your MCP client, or ask the owner of
      this gateway to check its authentication settings.
    </p>
    """
    return _page(error_title, body)


def install() -> None:
    """Point FastMCP's two page builders at the ones above. Idempotent.

    Both modules import the names at load time (`from ... import
    create_consent_html`), so the binding to replace is theirs, not the
    defining module's.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from fastmcp.server.auth.oauth_proxy import consent, proxy

    consent.create_consent_html = consent_html
    proxy.create_error_html = error_html
    _INSTALLED = True


__all__ = ["consent_html", "error_html", "install"]
