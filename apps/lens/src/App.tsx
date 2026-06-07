import type { ReactNode } from "react";
import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import type { Identity } from "./api.ts";
import Admin from "./pages/Admin.tsx";
import Chat from "./pages/Chat.tsx";
import Search from "./pages/Search.tsx";

// The demo identity bar. In a real deployment the user and groups come from the
// Identity port and a session; retrieval enforces permissions regardless of what
// is typed here, because the authz bulk check is the source of truth.
export default function App() {
  const [identity, setIdentity] = useState<Identity>({
    user: "alice",
    groups: ["g1"],
    tenant_id: "default",
  });

  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
          <span className="text-lg font-semibold lowercase">pansoph · lens</span>
          <nav className="flex gap-4 text-sm">
            <Tab to="/">chat</Tab>
            <Tab to="/search">search</Tab>
            <Tab to="/admin">admin</Tab>
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs text-neutral-600">
            <span>acting as</span>
            <input
              className="w-20 rounded border px-2 py-1"
              value={identity.user}
              onChange={(e) => setIdentity({ ...identity, user: e.target.value })}
            />
            <input
              className="w-28 rounded border px-2 py-1"
              value={identity.groups.join(",")}
              onChange={(e) =>
                setIdentity({
                  ...identity,
                  groups: e.target.value.split(",").map((g) => g.trim()).filter(Boolean),
                })
              }
              placeholder="groups"
            />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Chat identity={identity} />} />
          <Route path="/search" element={<Search identity={identity} />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </main>
    </div>
  );
}

function Tab({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `hover:text-blue-600 ${isActive ? "font-semibold text-blue-600" : "text-neutral-600"}`
      }
      end
    >
      {children}
    </NavLink>
  );
}
