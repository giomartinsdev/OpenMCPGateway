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
  const data = text ? JSON.parse(text) : {};
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
  logout: () => call("POST", "/auth/logout"),

  gateways: () => call("GET", "/gateways"),
  gateway: (id) => call("GET", `/gateways/${id}`),
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
