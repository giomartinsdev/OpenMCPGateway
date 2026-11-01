import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Login } from "./pages/Login.jsx";
import { Gateways } from "./pages/Gateways.jsx";
import { GatewayDetail } from "./pages/GatewayDetail.jsx";
import { Traffic } from "./pages/Traffic.jsx";
import { ConfigPage } from "./pages/Config.jsx";
import { Audit } from "./pages/Audit.jsx";

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

const NAV = [
  ["/", "Gateways"],
  ["/traffic", "Traffic"],
  ["/audit", "Audit log"],
  ["/config", "Config as code"],
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
  if (isGatewayDetail) page = <GatewayDetail id={segment[1]} />;
  else if (path === "/traffic") page = <Traffic />;
  else if (path === "/audit") page = <Audit />;
  else if (path === "/config") page = <ConfigPage />;
  else page = <Gateways />;

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
          {NAV.map(([href, label]) => (
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
          <div className="who">{me.email}</div>
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
