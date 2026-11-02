// Thin fetch wrapper. Every call is same-origin; the session lives in an
// HttpOnly cookie, so there is no token to store and nothing to leak to XSS.

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
  operators: () => call("GET", "/operators"),
  // No role_ids means the default role: an omission must never widen access.
  addOperator: (email, password, roleIds = []) =>
    call("POST", "/operators", { email, password, role_ids: roleIds }),
  assignRoles: (operatorId, roleIds) =>
    call("PUT", `/operators/${operatorId}/roles`, { role_ids: roleIds }),

  roles: () => call("GET", "/roles"),
  defineRole: (name, permissions) => call("POST", "/roles", { name, permissions }),
  updateRole: (id, name, permissions) =>
    call("PUT", `/roles/${id}`, { name, permissions }),
  deleteRole: (id) => call("DELETE", `/roles/${id}`),
  logout: () => call("POST", "/auth/logout"),
  authMethods: () => call("GET", "/auth/methods"),

  signInSettings: () => call("GET", "/settings/sign-in"),
  // `google_client_secret: null` means "keep the stored one".
  saveSignInSettings: (payload) => {
    const body = { ...payload };
    if (body.google_client_secret == null) delete body.google_client_secret;
    return call("PUT", "/settings/sign-in", body);
  },

  gateways: () => call("GET", "/gateways"),
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
  revokeKey: (id, keyId) => call("DELETE", `/gateways/${id}/keys/${keyId}`),

  stats: (gatewayId, minutes = 60) =>
    call("GET", `/stats?minutes=${minutes}${gatewayId ? `&gateway_id=${gatewayId}` : ""}`),
  requests: (gatewayId, limit = 100) =>
    call("GET", `/requests?limit=${limit}${gatewayId ? `&gateway_id=${gatewayId}` : ""}`),
  events: (limit = 50) => call("GET", `/events?limit=${limit}`),

  exportConfig: () => call("GET", "/config/export"),
  applyConfig: (yaml) => call("POST", "/config/apply", { yaml }),
  validateConfig: (yaml) => call("POST", "/config/validate", { yaml }),
};
