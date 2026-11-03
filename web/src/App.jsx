import { useEffect, useState } from "react";
import { api } from "./api.js";
import { can } from "./ui.jsx";
import { Landing } from "./pages/Landing.jsx";
import { Gateways } from "./pages/Gateways.jsx";
import { GatewayDetail } from "./pages/GatewayDetail.jsx";
import { Traffic } from "./pages/Traffic.jsx";
import { ConfigPage } from "./pages/Config.jsx";
import { Audit } from "./pages/Audit.jsx";
import { Members } from "./pages/Members.jsx";
import { Roles } from "./pages/Roles.jsx";
import { Onboarding } from "./pages/Onboarding.jsx";
import { Bone } from "./ui.jsx";

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
  { to: "/", label: "Gateways", read: "gateways:read", group: "Operate" },
  { to: "/traffic", label: "Traffic", read: "traffic:read" },
  { to: "/audit", label: "Audit log", read: "audit:read" },
  { to: "/config", label: "Config as code", read: "config:read", group: "Settings" },
  { to: "/members", label: "Members", read: "members:read" },
  { to: "/roles", label: "Roles", read: "roles:read" },
];

/** The frame every signed-in page renders inside. */
function Frame({ me, page, path, isDetail, onSignOut }) {
  return (
    <div className="frame">
      <aside className="rail">
        <div className="logo">
          <div className="logo-mark" />
          <div className="logo-text">
            OpenMCP<span>Gateway</span>
          </div>
        </div>
        <nav className="nav">
          {NAV.filter((item) => can(me, item.read)).map((item) => {
            const active =
              item.to === "/"
                ? !isDetail && path === "/"
                : path.startsWith(item.to);
            return (
              <div key={item.to} style={{ display: "contents" }}>
                {item.group ? <div className="nav-label">{item.group}</div> : null}
                <a href={`#${item.to}`} className={active ? "active" : ""}>
                  <span className="dot" />
                  {item.label}
                </a>
              </div>
            );
          })}
        </nav>
        <div className="rail-foot">
          <div className="who">
            <strong>{me.email}</strong>
            <span>{me.tenant_name || "tenant"}</span>
            <span className="roles">{(me.roles ?? []).join(", ") || "no role"}</span>
          </div>
          <button className="ghost sm" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">
        <div className="page">{page}</div>
      </main>
    </div>
  );
}

export function App() {
  const [me, setMe] = useState(undefined); // undefined = loading
  const path = useRoute();

  useEffect(() => {
    api.me().then(setMe, () => setMe(null));
  }, []);

  // The first paint after a reload is a skeleton of the frame, not a centred
  // "Loading…": the rail is the same width either way, so nothing shifts.
  if (me === undefined) {
    return (
      <div className="frame">
        <aside className="rail">
          <div className="logo">
            <div className="logo-mark" />
            <div className="logo-text">
              OpenMCP<span>Gateway</span>
            </div>
          </div>
          <div className="nav" style={{ gap: 10, padding: "0 10px" }}>
            {[0, 1, 2, 3].map((i) => (
              <Bone key={i} height={14} width={i === 3 ? "60%" : "82%"} />
            ))}
          </div>
        </aside>
        <main className="content">
          <div className="page">
            <div className="page-head">
              <div style={{ width: "100%", maxWidth: 420 }}>
                <Bone height={12} width={90} />
                <Bone height={28} width={240} style={{ marginTop: 10 }} />
                <Bone height={12} width="100%" style={{ marginTop: 12 }} />
              </div>
            </div>
            <div className="grid-3" style={{ marginBottom: 12 }}>
              {[0, 1, 2].map((i) => (
                <div className="card" key={i}>
                  <Bone height={10} width={80} />
                  <Bone height={26} width={110} style={{ marginTop: 8 }} />
                </div>
              ))}
            </div>
            <div className="card">
              <Bone height={14} width={140} />
              <div style={{ display: "grid", gap: 10, marginTop: 16 }}>
                {[0, 1, 2, 3, 4].map((i) => (
                  <Bone key={i} height={12} width={`${95 - i * 7}%`} />
                ))}
              </div>
            </div>
          </div>
        </main>
      </div>
    );
  }

  // Signed out: the landing page *is* the sign-in page. A separate /login route
  // would be a second front door to keep painted.
  if (me === null) {
    return <Landing onLogin={setMe} />;
  }

  // A signed-in account with no tenant has nothing to administer yet. Every
  // route below would answer 403, so the choice *is* the page.
  if (!me.tenant_id) return <Onboarding me={me} onReady={setMe} />;

  const segment = path.split("/").filter(Boolean);
  const isDetail = segment[0] === "gateways" && Boolean(segment[1]);

  let page;
  if (isDetail) page = <GatewayDetail id={segment[1]} me={me} />;
  else if (path === "/traffic") page = <Traffic />;
  else if (path === "/audit") page = <Audit />;
  else if (path === "/config") page = <ConfigPage me={me} />;
  else if (path === "/members") page = <Members me={me} />;
  else if (path === "/roles") page = <Roles me={me} />;
  else page = <Gateways me={me} />;

  return (
    <Frame
      me={me}
      page={page}
      path={path}
      isDetail={isDetail}
      onSignOut={async () => {
        await api.logout();
        setMe(null);
      }}
    />
  );
}
