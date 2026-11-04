import { useEffect, useState } from "react";
import { api } from "./api.js";
import { can } from "./ui.jsx";
import { Icon } from "./icons.jsx";
import { LanguagePicker, useT } from "./i18n.jsx";
import { Mark } from "./Mark.jsx";
import { ThemeToggle, useTheme } from "./theme.jsx";
import { Landing } from "./pages/Landing.jsx";
import { Gateways } from "./pages/Gateways.jsx";
import { GatewayDetail } from "./pages/GatewayDetail.jsx";
import { Traffic } from "./pages/Traffic.jsx";
import { ConfigPage } from "./pages/Config.jsx";
import { Audit } from "./pages/Audit.jsx";
import { Members } from "./pages/Members.jsx";
import { Roles } from "./pages/Roles.jsx";
import { Onboarding } from "./pages/Onboarding.jsx";
import { Docs } from "./pages/Docs.jsx";
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
//
// Three groups, in the order an operator asks the questions: what am I routing,
// what is happening, how is it governed. `docs` is the only entry with no
// permission — it is the manual, and a manual behind a login is a manual nobody
// reads before asking for access.
const NAV = [
  {
    group: "Gateway",
    items: [
      { to: "/", label: "Gateways", read: "gateways:read", icon: "gateway" },
    ],
  },
  {
    group: "Traffic & Logs",
    items: [
      { to: "/traffic", label: "Traffic", read: "traffic:read", icon: "pulse" },
      { to: "/audit", label: "Audit log", read: "audit:read", icon: "logs" },
    ],
  },
  {
    group: "Settings",
    items: [
      // RBAC first, then the file that can rewrite it: the forms and the YAML
      // edit the same state, and reading them in that order says so.
      { to: "/members", label: "Members", read: "members:read", icon: "users" },
      { to: "/roles", label: "Roles", read: "roles:read", icon: "shield" },
      { to: "/config", label: "Config as code", read: "config:read", icon: "code" },
    ],
  },
  {
    group: "Help",
    items: [{ to: "/docs", label: "Documentation", icon: "book" }],
  },
];

function useVisibleNav(me) {
  return NAV
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.read || can(me, item.read)),
    }))
    .filter((group) => group.items.length > 0);
}

/** The frame every signed-in page renders inside.
 *
 * A bar, not a rail. The rail cost 248px of every screen for seven links — on
 * the pages that matter here (traffic tables, gateway config) that width is the
 * content. The bar keeps the same groups, marked by hairline dividers instead of
 * labels, and gives the width back.
 */
function Frame({ me, page, path, isDetail, onSignOut }) {
  const t = useT();
  const groups = useVisibleNav(me);
  const [theme, setTheme] = useTheme();
  return (
    <div className="frame">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="logo" href="#/" aria-label={t("OpenMCPGateway home")}>
            <Mark size={28} />
            <div className="logo-text">
              OpenMCP<span>Gateway</span>
            </div>
          </a>
          <nav className="nav" aria-label={t("Main")}>
            {groups.map((group) => (
              <div className="nav-group" key={group.group}>
                {group.items.map((item) => {
                  const active =
                    item.to === "/"
                      ? !isDetail && path === "/"
                      : path.startsWith(item.to);
                  return (
                    <a
                      key={item.to}
                      href={`#${item.to}`}
                      className={active ? "active" : undefined}
                      aria-current={active ? "page" : undefined}
                    >
                      <Icon name={item.icon} size={14} />
                      <span>{t(item.label)}</span>
                    </a>
                  );
                })}
              </div>
            ))}
          </nav>
          <div className="topbar-side">
            <div className="who">
              <span className="avatar" aria-hidden="true">
                {(me.email || "?").slice(0, 1)}
              </span>
              <div className="who-text">
                <strong>{me.email}</strong>
                <span>
                  {me.tenant_name || t("tenant")} · {(me.roles ?? []).join(", ") || t("no role")}
                </span>
              </div>
            </div>
            <LanguagePicker />
            <ThemeToggle theme={theme} onSelect={setTheme} />
            <button className="ghost sm" onClick={onSignOut}>
              {t("Sign out")}
            </button>
          </div>
        </div>
      </header>
      <main className="content">
        <div className="page">{page}</div>
      </main>
    </div>
  );
}

export function App() {
  const t = useT();
  const [theme, setTheme] = useTheme();
  const [me, setMe] = useState(undefined); // undefined = loading
  const path = useRoute();

  useEffect(() => {
    api.me().then(setMe, () => setMe(null));
  }, []);

  // The first paint after a reload is a skeleton of the bar, not a centred
  // "Loading…": the bar is the same height either way, so nothing shifts.
  if (me === undefined) {
    return (
      <div className="frame">
        <header className="topbar">
          <div className="topbar-inner">
            <div className="logo">
              <Mark size={28} />
              <div className="logo-text">
                OpenMCP<span>Gateway</span>
              </div>
            </div>
            <div className="nav" style={{ gap: 8 }}>
              {[0, 1, 2, 3].map((i) => (
                <Bone key={i} height={13} width={i === 3 ? 70 : 86} />
              ))}
            </div>
            <div className="topbar-side">
              <Bone height={13} width={150} />
            </div>
          </div>
        </header>
        <main className="content">
          <div className="page">
            <div className="page-head">
              <div style={{ width: "100%", maxWidth: 420 }}>
                <Bone height={12} width={90} />
                <Bone height={28} width={240} style={{ marginTop: 8 }} />
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
              <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
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
  //
  // `/docs` is checked first, and for both states: the manual is what someone
  // reads *before* deciding to sign in, and it is the one page here that has no
  // permission attached.
  if (path.startsWith("/docs")) {
    const docs = <Docs signedIn={me !== null} />;
    // Signed out, the manual stands alone: no rail, because there is nothing to
    // navigate to yet, and it is the page a stranger reads before signing up.
    if (me === null) {
      return (
        <div className="docs-shell">
          <div className="docs-top">
            <a className="logo docs-logo" href="#/" aria-label={t("OpenMCPGateway home")}>
              <Mark size={34} />
              <div className="logo-text">
                OpenMCP<span>Gateway</span>
              </div>
            </a>
            <div className="row">
              <LanguagePicker />
              <ThemeToggle theme={theme} onSelect={setTheme} />
            </div>
          </div>
          {docs}
        </div>
      );
    }
    return (
      <Frame
        me={me}
        page={docs}
        path={path}
        isDetail={false}
        onSignOut={async () => {
          await api.logout();
          setMe(null);
        }}
      />
    );
  }

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
