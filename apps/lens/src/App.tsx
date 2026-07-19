import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";

import { CommandPalette, type Command } from "./components/CommandPalette.tsx";
import { BrandMark, Lockup, Wordmark } from "./components/Logo.tsx";
import { health, type Health, type Identity } from "./api.ts";
import {
  accountUrl,
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
import { Avatar, Button, Kbd, StatusDot } from "./ui.tsx";

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  icon: string;
}

const NAV_CHAT: NavItem = {
  to: "/",
  label: "Chat",
  end: true,
  icon: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
};
const NAV_SEARCH: NavItem = {
  to: "/search",
  label: "Search",
  icon: "M11 18a7 7 0 100-14 7 7 0 000 14zM21 21l-4.3-4.3",
};
const NAV_CONNECTORS: NavItem = {
  to: "/connectors",
  label: "Connectors",
  icon: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM17 14v6M14 17h6",
};
const NAV_ACCESS: NavItem = {
  to: "/access",
  label: "Access",
  icon: "M4 10h16v11H4zM8 10V7a4 4 0 0 1 8 0v3",
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

// Theme: dark by default (Obsidian), switchable to light. The choice is persisted and
// applied to the document root as data-theme, which flips the CSS token overrides.
function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try {
      return localStorage.getItem("pansoph-theme") === "light" ? "light" : "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("pansoph-theme", theme);
    } catch {
      /* storage may be unavailable; the attribute is what actually drives the theme */
    }
  }, [theme]);
  return { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}

export default function App() {
  const { user, ready } = useAuth();

  if (!ready) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">
        <StatusDot kind="run" pulse className="mr-2" />
        loading...
      </div>
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
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-[var(--radius-xl)] border border-border bg-surface p-8 text-center shadow-[var(--shadow-float)]">
        <div className="mb-6 flex justify-center">
          <Lockup tagline="access-aware knowledge" size={40} />
        </div>
        <p className="mt-2 text-sm text-muted">
          Sign in to your organization. You will only ever see what you are permitted to.
        </p>
        <div className="mt-6">
          <Button onClick={login} block>
            Sign in
          </Button>
        </div>
        <p className="mt-5 text-xs text-muted">
          Demo users: <span className="font-medium text-text">alice / alice</span> (group g1),{" "}
          <span className="font-medium text-text">bob / bob</span> (no group)
        </p>
      </div>
    </div>
  );
}

function Shell({ identity }: { identity: Identity }) {
  const [hp, setHp] = useState<Health | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  useEffect(() => {
    const tick = () => health().then(setHp).catch(() => setHp(null));
    tick();
    const t = setInterval(tick, 8000);
    return () => clearInterval(t);
  }, []);
  // Cmd/Ctrl+K toggles the command palette. The palette searches the app itself (pages
  // and actions), not documents. Document search stays on the /search page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const healthy = hp?.ok ?? false;
  const nav = navFor(identity.roles);
  const canManage = canManageConnectors(identity.roles);
  const admin = isAdmin(identity.roles);

  const healthLabel = hp == null ? "agora unreachable" : healthy ? "all systems healthy" : "degraded";
  const healthKind: "ok" | "warn" | "err" = hp == null ? "err" : healthy ? "ok" : "warn";

  // The commands the palette can run. Navigation entries come from the same role-aware
  // nav the sidebar renders, so a route the user cannot reach never appears here. Actions
  // are wired to the real callbacks (theme toggle, account console, sign out, add
  // connector). Only actions the user is permitted to take are included.
  const commands: Command[] = [
    ...nav.map(
      (n): Command => ({
        id: `nav:${n.to}`,
        group: "Navigate",
        label: n.label,
        icon: <NavIcon d={n.icon} />,
        run: () => navigate(n.to),
      }),
    ),
    ...(canManage
      ? [
          {
            id: "action:add-connector",
            group: "Actions",
            label: "Add connector",
            icon: <NavIcon d={NAV_CONNECTORS.icon} />,
            run: () => navigate("/connectors?add=1"),
          } satisfies Command,
        ]
      : []),
    {
      id: "action:toggle-theme",
      group: "Actions",
      label: `Toggle theme (${theme === "dark" ? "to light" : "to dark"})`,
      icon: <ActionIcon kind="theme" />,
      run: toggle,
    },
    {
      id: "action:manage-account",
      group: "Actions",
      label: "Manage account",
      icon: <ActionIcon kind="account" />,
      run: () => window.open(accountUrl(), "_blank", "noopener,noreferrer"),
    },
    {
      id: "action:sign-out",
      group: "Actions",
      label: "Sign out",
      icon: <ActionIcon kind="signout" />,
      run: logout,
    },
  ];

  return (
    <div className="flex h-full">
      {/* ---- Slim icon rail (aperture) ---- */}
      <aside className="flex w-[70px] shrink-0 flex-col items-center border-r border-border bg-[color-mix(in_srgb,var(--ob-surface)_88%,transparent)] py-4 backdrop-blur-md">
        {/* owl -> home */}
        <NavLink to="/" end aria-label="pansoph home" className="mb-5 grid place-items-center">
          <BrandMark size={30} />
        </NavLink>

        <nav className="flex flex-1 flex-col items-center gap-1.5">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              title={n.label}
              aria-label={n.label}
              className={({ isActive }) =>
                `relative grid h-11 w-11 place-items-center rounded-[13px] transition ${
                  isActive
                    ? "bg-surface text-accent shadow-[inset_0_0_0_1px_var(--ob-border)]"
                    : "text-faint hover:bg-surface hover:text-text"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute -left-[13px] bottom-3 top-3 w-[3px] rounded-full bg-accent" />
                  )}
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    className="h-[20px] w-[20px]"
                  >
                    <path d={n.icon} strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* permissions-live shield: the brand's soul, as an iris/shield with a tooltip */}
        <div
          title="Permissions live. Every answer is filtered by your group ACLs at retrieval time. Nothing you cannot see reaches the model."
          aria-label="Permissions live"
          className="mb-2 grid h-10 w-10 place-items-center rounded-[13px] text-accent"
        >
          <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M12 2 4 5v6c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V5z" strokeLinejoin="round" />
            <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>

        {/* signed-in user -> Access */}
        <button
          type="button"
          onClick={() => navigate("/access")}
          title={`${identity.user} . open Access`}
          aria-label="Open Access page"
          className="mb-1.5 grid place-items-center rounded-full outline-offset-2 transition hover:brightness-110"
        >
          <Avatar name={identity.user} size={32} />
        </button>
        {/* sign out */}
        <button
          type="button"
          onClick={logout}
          title="Sign out"
          aria-label="Sign out"
          className="grid h-8 w-8 place-items-center rounded-[10px] text-faint transition hover:bg-surface hover:text-err"
        >
          <svg viewBox="0 0 24 24" className="h-[17px] w-[17px]" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M16 17l5-5-5-5M21 12H9" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </aside>

      {/* ---- Main column ---- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-4 border-b border-border bg-[color-mix(in_srgb,var(--ob-bg)_72%,transparent)] px-6 backdrop-blur-md">
          {/* wordmark, since the rail is now icon-only */}
          <NavLink to="/" end className="flex items-center gap-2.5">
            <Wordmark className="text-[17px] text-text" />
          </NavLink>

          <span className="mx-1 hidden h-6 w-px bg-border sm:block" />

          {/* Command bar: opens the command palette (also Cmd/Ctrl+K). */}
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            aria-label="Open command palette"
            className="flex h-[36px] min-w-[240px] flex-1 items-center gap-2.5 rounded-[var(--radius-md)] border border-border bg-surface px-3 text-left text-[13px] text-muted transition hover:border-border-hover hover:text-text sm:flex-none sm:min-w-[300px]"
          >
            <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" strokeLinecap="round" />
            </svg>
            <span>Search actions and pages</span>
            <span className="ml-auto flex gap-1">
              <Kbd>Cmd</Kbd>
              <Kbd>K</Kbd>
            </span>
          </button>

          <TopBarRight
            healthKind={healthKind}
            healthLabel={healthLabel}
            theme={theme}
            onToggleTheme={toggle}
          />
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

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
      />
    </div>
  );
}

// A stroked line icon rendered from a single SVG path, matching the sidebar nav icons.
// Used for the palette's Navigate entries so a command reads visually like its page.
function NavIcon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d={d} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Icons for the palette's action commands (theme toggle, account console, sign out).
function ActionIcon({ kind }: { kind: "theme" | "account" | "signout" }) {
  const paths: Record<typeof kind, string> = {
    theme: "M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z",
    account: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
    signout: "M15 12H3m0 0 4-4m-4 4 4 4M11 4h6a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6",
  };
  return (
    <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d={paths[kind]} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// The compact right cluster in the top bar: a health pill and the theme toggle. The
// identity affordance now lives in the rail (avatar -> Access), so it is not repeated
// here. No group/role chips, no "signed in as" block.
function TopBarRight({
  healthKind,
  healthLabel,
  theme,
  onToggleTheme,
}: {
  healthKind: "ok" | "warn" | "err";
  healthLabel: string;
  theme: "dark" | "light";
  onToggleTheme: () => void;
}) {
  return (
    <div className="ml-auto flex items-center gap-3">
      <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted">
        <StatusDot kind={healthKind} pulse={healthKind === "ok"} />
        {healthLabel}
      </div>

      <button
        type="button"
        onClick={onToggleTheme}
        aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        title={`Theme: ${theme}. Click for ${theme === "dark" ? "light" : "dark"}.`}
        className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-muted transition hover:border-border-hover hover:text-text"
      >
        {/* the theme knob reads as an aperture (owl iris), open in light, closed in dark */}
        {theme === "dark" ? (
          <svg viewBox="0 0 24 24" className="h-[15px] w-[15px] text-accent" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="8" />
            <circle cx="12" cy="12" r="3.2" fill="currentColor" stroke="none" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" className="h-[15px] w-[15px] text-accent" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="8" />
            <path d="M12 4v16M4 12h16" strokeLinecap="round" />
          </svg>
        )}
        <span>{theme}</span>
      </button>
    </div>
  );
}
