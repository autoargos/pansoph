import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { health, type Health, type Identity } from "./api.ts";
import {
  canManageConnectors,
  groupsOf,
  isAdmin,
  login,
  logout,
  rolesOf,
  useAuth,
  usernameOf,
} from "./auth.ts";
import Access from "./pages/Access.tsx";
import Chat from "./pages/Chat.tsx";
import Connectors from "./pages/Connectors.tsx";
import Search from "./pages/Search.tsx";
import System from "./pages/System.tsx";
import { Button } from "./ui.tsx";

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  icon: string;
}

const NAV_CHAT: NavItem = { to: "/", label: "Chat", end: true, icon: "M4 5h16M4 12h10M4 19h7" };
const NAV_SEARCH: NavItem = {
  to: "/search",
  label: "Search",
  icon: "M11 18a7 7 0 100-14 7 7 0 000 14zM21 21l-4.3-4.3",
};
const NAV_CONNECTORS: NavItem = {
  to: "/connectors",
  label: "Connectors",
  icon: "M8 7h8M8 12h8M8 17h5M5 4h14v16H5z",
};
const NAV_ACCESS: NavItem = {
  to: "/access",
  label: "Access",
  icon: "M12 3l7 4v5c0 5-3 7.5-7 8-4-.5-7-3-7-8V7zM9.5 12l1.8 1.8L15 10",
};
const NAV_SYSTEM: NavItem = {
  to: "/system",
  label: "System",
  icon: "M12 3l8 4v6c0 5-3.5 7.5-8 8-4.5-.5-8-3-8-8V7z",
};

// Role-aware navigation. Connectors needs management rights, Access is admin only.
// This is convenience, not security: agora enforces every action by role regardless.
function navFor(roles: string[]): NavItem[] {
  const nav: NavItem[] = [NAV_CHAT, NAV_SEARCH];
  if (canManageConnectors(roles)) nav.push(NAV_CONNECTORS);
  if (isAdmin(roles)) nav.push(NAV_ACCESS);
  nav.push(NAV_SYSTEM);
  return nav;
}

export default function App() {
  const { user, ready } = useAuth();

  if (!ready) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">loading...</div>
    );
  }
  if (!user) return <Login />;

  const identity: Identity = {
    user: usernameOf(user),
    groups: groupsOf(user),
    roles: rolesOf(user),
    tenant_id: "default",
  };

  return <Shell identity={identity} />;
}

function Login() {
  return (
    <div className="flex h-full items-center justify-center bg-canvas">
      <div className="w-full max-w-sm rounded-[var(--radius-card)] border border-line bg-surface p-8 text-center shadow-sm">
        <div className="text-2xl font-semibold tracking-tight">
          pansoph<span className="text-accent"> · lens</span>
        </div>
        <p className="mt-2 text-sm text-muted">
          Sign in to your organization. You will only ever see what you are permitted to.
        </p>
        <div className="mt-6">
          <Button onClick={login} className="w-full">
            Sign in
          </Button>
        </div>
        <p className="mt-5 text-xs text-faint">
          Demo users: <span className="font-medium text-muted">alice / alice</span> (group g1),{" "}
          <span className="font-medium text-muted">bob / bob</span> (no group)
        </p>
      </div>
    </div>
  );
}

function Shell({ identity }: { identity: Identity }) {
  const [hp, setHp] = useState<Health | null>(null);
  useEffect(() => {
    const tick = () => health().then(setHp).catch(() => setHp(null));
    tick();
    const t = setInterval(tick, 8000);
    return () => clearInterval(t);
  }, []);
  const healthy = hp?.ok ?? false;
  const nav = navFor(identity.roles);
  const canManage = canManageConnectors(identity.roles);
  const admin = isAdmin(identity.roles);

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col bg-ink text-white/80">
        <div className="px-5 py-5">
          <div className="text-lg font-semibold tracking-tight text-white">
            pansoph<span className="text-accent"> · lens</span>
          </div>
          <div className="mt-0.5 text-[11px] text-white/40">access-aware knowledge</div>
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-3">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
                  isActive
                    ? "bg-white/10 text-white"
                    : "text-white/60 hover:bg-white/5 hover:text-white"
                }`
              }
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                className="h-4 w-4"
              >
                <path d={n.icon} strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-5 py-4 text-[11px] text-white/40">Apache-2.0 · self-hosted</div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-line bg-surface px-8 py-3">
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className={`inline-block h-2 w-2 rounded-full ${healthy ? "bg-ok" : "bg-err"}`} />
            {hp == null ? "agora unreachable" : healthy ? "all systems healthy" : "degraded"}
          </div>
          <div className="flex items-center gap-3 text-xs">
            <div className="flex items-center gap-1.5 text-muted">
              <span>signed in as</span>
              <span className="font-medium text-ink">{identity.user}</span>
              {identity.roles.map((r) => (
                <span
                  key={r}
                  className="rounded-full bg-ink/10 px-2 py-0.5 text-[11px] font-medium text-ink"
                  title="management role"
                >
                  {r}
                </span>
              ))}
              {identity.groups.map((g) => (
                <span
                  key={g}
                  className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-ink"
                  title="document group"
                >
                  {g}
                </span>
              ))}
              {identity.groups.length === 0 && <span className="text-faint">no groups</span>}
            </div>
            <button onClick={logout} className="rounded-md px-2 py-1 text-muted hover:bg-canvas hover:text-ink">
              Sign out
            </button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-auto px-8 py-7">
          <Routes>
            <Route path="/" element={<Chat identity={identity} />} />
            <Route path="/search" element={<Search identity={identity} />} />
            <Route path="/connectors" element={<Connectors canManage={canManage} />} />
            {admin && <Route path="/access" element={<Access identity={identity} />} />}
            <Route path="/system" element={<System />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
