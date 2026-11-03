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
# the only place these colours are authored; keep them in sync by eye, not by a
# build step, because this HTML is served by Python with no bundler in reach.
_STYLES = """
  :root {
    --brand: #ccff00; --brand-hover: #a3cc00;
    --bg: #0d0f0d; --raised: #242824; --inset: #070807;
    --border: #242824; --border-strong: #3d443d;
    --text: #d2d7d2; --text-strong: #e3e8e3; --text-dim: #9da99d;
    --on-brand: #070807; --danger-solid: #d78392; --danger-bg: #361118;
    --ok-bg: #143029; --ok-fg: #cce3de; --ok-border: #1d443b;
    --radius: 3px;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; padding: 24px;
    background: var(--bg); color: var(--text);
    font: 400 14px/1.55 var(--sans); -webkit-font-smoothing: antialiased;
  }
  .card {
    width: 100%; max-width: 32rem; background: var(--raised);
    border: 1px solid var(--border); border-radius: var(--radius); padding: 24px;
  }
  .logo { display: flex; align-items: center; gap: 9px; margin-bottom: 18px; }
  .logo-mark {
    width: 9px; height: 22px; background: var(--brand);
    clip-path: polygon(0 0, 100% 0, 55% 100%, 0 100%);
  }
  .logo-text { color: #fff; font-weight: 650; font-size: 14px; letter-spacing: -0.01em; }
  .logo-text span { color: var(--brand); }
  h1 {
    margin: 0 0 6px; font-size: 20px; font-weight: 600;
    letter-spacing: -0.02em; color: var(--text-strong);
  }
  .sub { color: var(--text-dim); font-size: 13px; margin-bottom: 18px; }
  .info {
    background: var(--inset); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 12px 14px; margin-bottom: 12px;
    font-size: 13px;
  }
  .info strong { color: var(--text-strong); font-weight: 600; }
  .badge {
    display: inline-flex; align-items: center; gap: 5px; border-radius: 2px;
    padding: 2px 7px; font-size: 11px; font-weight: 500;
    background: var(--ok-bg); color: var(--ok-fg); border: 1px solid var(--ok-border);
    margin-bottom: 12px;
  }
  .target {
    background: var(--inset); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 11px 13px; margin-bottom: 12px;
  }
  .target .label {
    color: var(--text-dim); font-size: 11px; text-transform: uppercase;
    letter-spacing: .07em; display: block; margin-bottom: 5px;
  }
  .target .value {
    font-family: var(--mono); font-size: 12.5px; color: var(--text-strong);
    word-break: break-all;
  }
  details { margin-bottom: 4px; }
  summary {
    color: var(--text-dim); font-size: 12px; cursor: pointer;
    padding: 6px 0; user-select: none;
  }
  summary:hover { color: var(--text-strong); }
  .details {
    background: var(--inset); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 11px 13px; margin-top: 4px;
  }
  .row { display: flex; gap: 12px; padding: 5px 0; font-size: 12.5px; }
  .row + .row { border-top: 1px solid var(--border); }
  .row .k { color: var(--text-dim); min-width: 132px; }
  .row .v {
    color: var(--text); font-family: var(--mono); font-size: 12px;
    word-break: break-all;
  }
  .actions { display: flex; gap: 10px; margin-top: 18px; }
  button {
    font: 500 13px var(--sans); border-radius: var(--radius); cursor: pointer;
    border: 1px solid var(--border-strong); background: transparent;
    color: var(--text); padding: 8px 16px;
    transition: background .12s, border-color .12s, color .12s;
  }
  button:hover { background: #2f342f; color: var(--text-strong); }
  .primary {
    background: var(--brand); border-color: var(--brand);
    color: var(--on-brand); font-weight: 600; flex: 1;
  }
  .primary:hover {
    background: var(--brand-hover); border-color: var(--brand-hover);
    color: var(--on-brand);
  }
  .deny:hover { background: var(--danger-bg); color: var(--danger-solid); border-color: #4a1f27; }
  .foot { color: var(--text-dim); font-size: 11.5px; margin-top: 16px; }
  .foot a { color: var(--brand); text-decoration: none; }
  .foot a:hover { color: var(--brand-hover); }
  .error {
    background: var(--danger-bg); color: #efcdd3; border: 1px solid #4a1f27;
    border-radius: var(--radius); padding: 12px 14px; margin-bottom: 12px;
    font-size: 13px;
  }
  .mono { font-family: var(--mono); font-size: 12px; }
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
