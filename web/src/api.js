// Thin fetch wrapper. Every call is same-origin; the session lives in an
// HttpOnly cookie, so there is no token to store and nothing to leak to XSS.

/**
 * Query string from a params object, dropping blanks.
 *
 * A blank filter input is an absent filter, not `field=`: the API treats empty
 * as absent too, and sending it anyway would put a `?` on every first paint.
 */
function query(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  return search.toString();
}

async function call(method, path, body) {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  // Not every failure arrives as JSON: a 500 from the ASGI stack is the plain
  // string "Internal Server Error". Parsing that throws a syntax error, which
  // surfaces as "Unexpected token 'I'" and hides the actual status.
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text.slice(0, 300) };
    }
  }
  if (!response.ok) {
    const error = new Error(data.detail || response.statusText);
    error.status = response.status;
    throw error;
  }
  return data;
}

export const api = {
  me: () => call("GET", "/auth/me"),
  login: (email, password) => call("POST", "/auth/login", { email, password }),
  register: (email, password) => call("POST", "/auth/register", { email, password }),
  // Onboarding: an account belongs nowhere until one of these succeeds.
  createTenant: (name) => call("POST", "/tenants", { name }),
  joinTenant: (code) => call("POST", "/tenants/join", { code }),
  tenant: () => call("GET", "/tenants/current"),
  // An administrator adding a member straight into this tenant.
  operators: (params) => call("GET", `/operators?${query(params)}`),
  // No role_ids means the default role: an omission must never widen access.
  addOperator: (email, password, roleIds = []) =>
    call("POST", "/operators", { email, password, role_ids: roleIds }),
  assignRoles: (operatorId, roleIds) =>
    call("PUT", `/operators/${operatorId}/roles`, { role_ids: roleIds }),

  roles: (params) => call("GET", `/roles?${query(params)}`),
  defineRole: (name, permissions) => call("POST", "/roles", { name, permissions }),
  updateRole: (id, name, permissions) =>
    call("PUT", `/roles/${id}`, { name, permissions }),
  deleteRole: (id) => call("DELETE", `/roles/${id}`),
  logout: () => call("POST", "/auth/logout"),
  authMethods: () => call("GET", "/auth/methods"),

  gateways: (params) => call("GET", `/gateways?${query(params)}`),
  gateway: (id) => call("GET", `/gateways/${id}`),
  // Server-generated: the OAuth steps name endpoints the proxy serves.
  connectGuide: (id) => call("GET", `/gateways/${id}/connect`),
  createGateway: (payload) => call("POST", "/gateways", payload),
  updateGateway: (id, payload) => call("PATCH", `/gateways/${id}`, payload),
  deleteGateway: (id) => call("DELETE", `/gateways/${id}`),
  setAuth: (id, payload) => call("PUT", `/gateways/${id}/auth`, payload),
  setRateLimit: (id, requests_per_minute) =>
    call("PUT", `/gateways/${id}/rate-limit`, { requests_per_minute }),
  enable: (id) => call("POST", `/gateways/${id}/enable`),
  disable: (id) => call("POST", `/gateways/${id}/disable`),

  addPrincipal: (id, subject, note = "") =>
    call("POST", `/gateways/${id}/principals`, { subject, note }),
  removePrincipal: (id, subject) =>
    call("DELETE", `/gateways/${id}/principals/${encodeURIComponent(subject)}`),

  issueKey: (id, label) => call("POST", `/gateways/${id}/keys`, { label }),
  // Rotation returns the new plaintext once; the old key stops working in the
  // same append, so there is no window where neither one authenticates.
  // The eye icon. Answers 409 once the key has been revealed or when it was
  // issued before sealing existed; the panel turns that into "rotate it".
  revealKey: (id, keyId) => call("POST", `/gateways/${id}/keys/${keyId}/reveal`),
  rotateKey: (id, keyId) =>
    call("POST", `/gateways/${id}/keys/${keyId}/rotate`),
  revokeKey: (id, keyId) => call("DELETE", `/gateways/${id}/keys/${keyId}`),
  // The upstream's real catalog, fetched by the panel (not through the gateway,
  // which would answer with the filter it is already applying).
  upstreamTools: (id) => call("GET", `/gateways/${id}/upstream-tools`),
  // Which upstream tools this gateway exposes. Empty means all of them.
  setGatewayTools: (id, tools) =>
    call("PUT", `/gateways/${id}/tools`, { tools }),

  stats: (gatewayId, minutes = 60) =>
    call("GET", `/stats?minutes=${minutes}${gatewayId ? `&gateway_id=${gatewayId}` : ""}`),
  // Cursor lists: `before` is the cursor the previous page answered with, and
  // every other key is a filter field the endpoint declares.
  requests: (params) => call("GET", `/requests?${query(params)}`),
  events: (params) => call("GET", `/events?${query(params)}`),
  // The manual is served per language; an unknown code gets English.
  docs: (lang) => call("GET", `/docs${lang ? `?lang=${lang}` : ""}`),
  // A read-only token for the console MCP; the plaintext is returned once.
  mintConsoleToken: () => call("POST", "/mcp/token"),

  exportConfig: () => call("GET", "/config/export"),
  applyConfig: (yaml) => call("POST", "/config/apply", { yaml }),
  validateConfig: (yaml) => call("POST", "/config/validate", { yaml }),
};
