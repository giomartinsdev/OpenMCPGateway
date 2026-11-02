import { useEffect, useState } from "react";
import { api } from "./api.js";
import { can } from "./ui.jsx";
import { Login } from "./pages/Login.jsx";
import { Gateways } from "./pages/Gateways.jsx";
import { GatewayDetail } from "./pages/GatewayDetail.jsx";
import { Traffic } from "./pages/Traffic.jsx";
import { ConfigPage } from "./pages/Config.jsx";
import { Audit } from "./pages/Audit.jsx";
import { Settings } from "./pages/Settings.jsx";
import { Members } from "./pages/Members.jsx";
import { Roles } from "./pages/Roles.jsx";

// Hash routing: 15 lines instead of a router dependency, and it survives being
// served from any reverse-proxy path without a rewrite rule for every route.
function useRoute() {
  const read = () => window.location.hash.replace(/^#/, "") || "/";
  const [path, setPath] = useState(read);
  useEffect(() => {
    const onChange = () => setPath(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return path;
}

export function navigate(to) {
  window.location.hash = to;
}

// `read` is the permission that makes the entry useful: a page you cannot read
// is a page that renders a 403, so it is not offered. The server decides on the
// click regardless.
const NAV = [
  ["/", "Gateways", "gateways:read"],
  ["/traffic", "Traffic", "traffic:read"],
  ["/audit", "Audit log", "audit:read"],
  ["/config", "Config as code", "config:read"],
  ["/members", "Members", "members:read"],
  ["/roles", "Roles", "roles:read"],
  ["/settings", "Settings", "settings:read"],
];

export function App() {
  const [me, setMe] = useState(undefined); // undefined = loading
  const path = useRoute();

  useEffect(() => {
    api.me().then(setMe, () => setMe(null));
  }, []);

  if (me === undefined) return <div className="login-wrap hint">Loading…</div>;
  if (me === null) return <Login onLogin={setMe} />;

  const segment = path.split("/").filter(Boolean);
  const isGatewayDetail = segment[0] === "gateways" && segment[1];

  let page;
  if (isGatewayDetail) page = <GatewayDetail id={segment[1]} me={me} />;
  else if (path === "/traffic") page = <Traffic />;
  else if (path === "/audit") page = <Audit />;
  else if (path === "/config") page = <ConfigPage me={me} />;
  else if (path === "/members") page = <Members me={me} />;
  else if (path === "/roles") page = <Roles me={me} />;
  else if (path === "/settings") page = <Settings />;
  else page = <Gateways me={me} />;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-mark" />
          <div className="logo-text">
            OpenMCP<span>Gateway</span>
          </div>
        </div>
        <nav className="nav">
          {NAV.filter(([, , permission]) => can(me, permission)).map(([href, label]) => (
            <a
              key={href}
              href={`#${href}`}
              className={
                (href === "/" ? !isGatewayDetail && path === "/" : path.startsWith(href))
                  ? "active"
                  : ""
              }
            >
              <span className="dot" />
              {label}
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="who">
            {me.email}
            <div style={{ marginTop: 2 }}>
              {(me.roles ?? []).join(", ") || "no role"} · {me.tenant_id}
            </div>
          </div>
          <button
            className="ghost sm"
            onClick={async () => {
              await api.logout();
              setMe(null);
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">{page}</main>
    </div>
  );
}
