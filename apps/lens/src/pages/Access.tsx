import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";

import {
  type Identity,
  type Instance,
  listInstances,
  search,
  type SearchResult,
} from "../api.ts";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Spinner,
  StatusDot,
  Tooltip,
} from "../ui.tsx";
import { accountUrl, logout } from "../auth.ts";

// ============================================================
// Access . the profile + access page
//
// Identity now lives here, not in the top bar. This page answers three
// questions honestly and in full:
//   1. Who am I?            (avatar, handle, tenant, session identity)
//   2. What do I gate?      (roles = management actions, groups = document reach)
//   3. What can I retrieve? (the access model, plus a live permission probe)
//
// Every fact shown is derived from the signed-in Identity that auth.ts builds
// from the OIDC token, or from the connector instances the API returns. The
// live check runs the same permission-enforced /search path chat and search
// use, so nothing here bypasses an ACL. The load-bearing invariant, stated
// plainly on the page: the admin role does NOT bypass document ACLs.
// ============================================================

// What each management role grants. Roles gate ACTIONS in lens and agora, not
// document reach. Mirrors auth.ts (canManageConnectors / isAdmin) and the agora
// role claims, which are the source of truth; this is the human explanation.
interface RoleGrant {
  role: string;
  title: string;
  grants: string;
  kind: "accent" | "cyan" | "neutral";
}

const ROLE_GRANTS: RoleGrant[] = [
  {
    role: "admin",
    title: "Administrator",
    grants:
      "Full management: connectors, access administration, and every workspace setting. Does not widen document reach: an admin still only retrieves what their own groups allow.",
    kind: "accent",
  },
  {
    role: "connector_admin",
    title: "Connector admin",
    grants:
      "Create, edit, sync, and delete connector instances and their credentials. No access to the rest of administration.",
    kind: "cyan",
  },
  {
    role: "member",
    title: "Member",
    grants:
      "Read only. Search and chat across the documents their groups permit. The baseline every signed-in user has.",
    kind: "neutral",
  },
];

// The three ways a document becomes visible to a user, enforced at retrieval.
// This is the whole access model, in the order the enforcement path considers it.
interface AccessPath {
  title: string;
  body: string;
  icon: ReactNode;
  tone: "accent" | "cyan" | "ok";
}

const GlobeIcon = (
  <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.5 2.6 3.9 5.7 4 9-.1 3.3-1.5 6.4-4 9-2.5-2.6-3.9-5.7-4-9 .1-3.3 1.5-6.4 4-9Z" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const GroupIcon = (
  <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 20v-2a4 4 0 0 0-3-3.87M16 3.13A4 4 0 0 1 16 11" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const GrantIcon = (
  <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M12 3 4 6v5c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V6z" strokeLinejoin="round" />
  </svg>
);

const ACCESS_PATHS: AccessPath[] = [
  {
    title: "Public or tenant-wide",
    body:
      "Documents a source marks public, or that are shared with your whole tenant, are visible to every member of the tenant. No group or grant needed.",
    icon: GlobeIcon,
    tone: "cyan",
  },
  {
    title: "Group grants",
    body:
      "A document inherits its source ACLs. If those ACLs name a group you belong to, you can retrieve it. This is how most access flows: your groups, mapped from the source.",
    icon: GroupIcon,
    tone: "accent",
  },
  {
    title: "Direct grants",
    body:
      "A source ACL can name you individually. A direct grant reaches you even without a shared group, at the same granularity the source exposes.",
    icon: GrantIcon,
    tone: "ok",
  },
];

const TONE_TEXT: Record<AccessPath["tone"], string> = {
  accent: "text-accent",
  cyan: "text-cyan",
  ok: "text-ok",
};

// ------------------------------------------------------------
// Page
// ------------------------------------------------------------
export default function Access({ identity }: { identity: Identity }) {
  const roles = identity.roles;
  const groups = identity.groups;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader
        breadcrumb={["lens", "access"]}
        title="Profile and access"
        subtitle="Who you are, what your roles let you do, and exactly what you are allowed to retrieve. Nothing here bypasses a document ACL."
      />

      <IdentityCard identity={identity} />

      <div className="grid gap-6 lg:grid-cols-2">
        <RolesCard roles={roles} />
        <GroupsCard groups={groups} />
      </div>

      <AccessModelCard />

      <ScopeCard />

      <AccessCheck identity={identity} />
    </div>
  );
}

// ------------------------------------------------------------
// 1. Identity . the signed-in user, richly
// ------------------------------------------------------------
function IdentityCard({ identity }: { identity: Identity }) {
  const roleCount = identity.roles.length;
  const groupCount = identity.groups.length;

  return (
    <Card glow className="overflow-hidden">
      {/* banded header with the ambient accent wash */}
      <div className="relative flex items-center gap-5 px-6 py-6">
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(420px 130px at 88% 0, var(--color-accent-soft), transparent 70%)",
          }}
        />
        <div className="relative">
          <Avatar name={identity.user} size={64} className="shadow-[var(--shadow-float)]" />
          <span className="absolute -bottom-0.5 -right-0.5 grid place-items-center rounded-full border-2 border-surface bg-surface">
            <StatusDot kind="ok" size={11} pulse />
          </span>
        </div>

        <div className="relative min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 className="text-[22px] font-bold tracking-[-0.03em] text-text">{identity.user}</h2>
            {identity.roles.includes("admin") && <Badge kind="accent" dot>admin</Badge>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted">
            <span className="inline-flex items-center gap-1.5">
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <path d="m4 7 8 6 8-6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="font-mono text-xs">{identity.user}@{identity.tenant_id}</span>
            </span>
            <span className="opacity-40">.</span>
            <Tooltip label="The signed-in session, active now">
              <span className="inline-flex items-center gap-1.5">
                <StatusDot kind="ok" size={6} />
                signed in
              </span>
            </Tooltip>
          </div>
        </div>

        {/* Account actions: manage the identity at the IdP, and sign out. Identity lives
            on this page (not the chrome), so these are the primary place to do both. */}
        <div className="relative flex shrink-0 items-center gap-2 self-start">
          <a
            href={accountUrl()}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-[var(--radius-md)] border border-border bg-surface px-3.5 py-2 text-[13px] font-medium text-text transition hover:border-accent-line hover:text-accent"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
              <circle cx="12" cy="12" r="3" />
              <path
                d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"
                strokeLinejoin="round"
              />
            </svg>
            Manage account
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 opacity-60" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
              <path d="M7 17 17 7M8 7h9v9" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </a>
          <button
            type="button"
            onClick={logout}
            className="inline-flex items-center gap-2 rounded-[var(--radius-md)] border border-border bg-surface px-3.5 py-2 text-[13px] font-medium text-muted transition hover:border-[rgba(244,63,94,0.4)] hover:text-err"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M16 17l5-5-5-5M21 12H9" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Sign out
          </button>
        </div>
      </div>

      {/* identity facts strip */}
      <div className="grid grid-cols-2 border-t border-border sm:grid-cols-4">
        <Fact label="Handle" value={identity.user} mono />
        <Fact label="Tenant" value={identity.tenant_id} mono />
        <Fact label="Roles" value={String(roleCount)} accent={roleCount > 0} />
        <Fact label="Groups" value={String(groupCount)} accent={groupCount > 0} />
      </div>
    </Card>
  );
}

function Fact({
  label,
  value,
  mono = false,
  accent = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
  accent?: boolean;
}) {
  return (
    <div className="border-r border-border px-5 py-3.5 last:border-r-0 [&:nth-child(2)]:border-r-0 sm:[&:nth-child(2)]:border-r">
      <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-muted">{label}</div>
      <div
        className={`mt-1 truncate text-[15px] font-semibold tabular-nums ${
          mono ? "font-mono text-[13.5px]" : ""
        } ${accent ? "text-accent" : "text-text"}`}
      >
        {value}
      </div>
    </div>
  );
}

// ------------------------------------------------------------
// 2a. Roles . what you can DO
// ------------------------------------------------------------
function RolesCard({ roles }: { roles: string[] }) {
  const held = new Set(roles);

  return (
    <Card className="flex flex-col p-6">
      <SectionTitle
        icon={
          <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
          </svg>
        }
        title="Roles"
        note="gate management actions"
      />

      <div className="mt-4 space-y-2.5">
        {ROLE_GRANTS.map((r) => {
          const active = held.has(r.role);
          return (
            <div
              key={r.role}
              className={`rounded-[var(--radius-md)] border px-4 py-3 transition ${
                active
                  ? "border-accent-line bg-accent-soft/40"
                  : "border-border bg-bg opacity-75"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Badge kind={active ? r.kind : "neutral"} dot={active}>
                    {r.role}
                  </Badge>
                  <span className="text-[13px] font-semibold text-text">{r.title}</span>
                </div>
                {active ? (
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-ok">
                    <StatusDot kind="ok" size={6} /> held
                  </span>
                ) : (
                  <span className="text-[11px] font-medium text-muted">not held</span>
                )}
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted">{r.grants}</p>
            </div>
          );
        })}
      </div>

      <Alert kind="warn" className="mt-4">
        Roles never widen document reach. An administrator manages the system but still retrieves
        only what their own groups and grants allow.
      </Alert>
    </Card>
  );
}

// ------------------------------------------------------------
// 2b. Groups . what you can SEE
// ------------------------------------------------------------
function GroupsCard({ groups }: { groups: string[] }) {
  return (
    <Card className="flex flex-col p-6">
      <SectionTitle
        icon={GroupIcon}
        title="Groups"
        note="gate document retrieval"
      />

      {groups.length > 0 ? (
        <>
          <p className="mt-3 text-xs leading-relaxed text-muted">
            You belong to{" "}
            <span className="font-semibold text-text tabular-nums">{groups.length}</span>{" "}
            {groups.length === 1 ? "group" : "groups"}. Any document whose source ACLs name one of
            these becomes retrievable for you.
          </p>
          <div className="mt-4 flex flex-col gap-2">
            {groups.map((g) => (
              <div
                key={g}
                className="group flex items-center gap-3 rounded-[var(--radius-md)] border border-border bg-bg px-3.5 py-2.5 transition hover:border-accent-line hover:bg-accent-soft/30"
              >
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[var(--radius-sm)] border border-border bg-surface text-accent transition group-hover:border-accent-line">
                  {GroupIcon}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium text-text">
                  {g}
                </span>
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-ok">
                  <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.4">
                    <path d="M20 6 9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  grants access
                </span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <EmptyState
          className="mt-4 py-10"
          icon={
            <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.6">
              <circle cx="9" cy="7" r="4" />
              <path d="M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          }
          title="No groups"
          description="You are not in any group yet, so you can retrieve only public or tenant-wide documents, plus anything granted to you directly."
        />
      )}
    </Card>
  );
}

// ------------------------------------------------------------
// 3. The access model . what you can retrieve, and why
// ------------------------------------------------------------
function AccessModelCard() {
  return (
    <Card className="p-6">
      <SectionTitle
        icon={
          <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="4" y="10" width="16" height="11" rx="2" />
            <path d="M8 10V7a4 4 0 0 1 8 0v3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        }
        title="What you can access"
        note="enforced at retrieval"
      />
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted">
        A document reaches you through one of three paths. Every retrieval, search, and chat runs the
        same check: overfetch candidates, bulk-check them against these paths, keep only the allowed
        subset, then rerank. A document you cannot see never reaches the model.
      </p>

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        {ACCESS_PATHS.map((p, i) => (
          <div
            key={p.title}
            className="relative flex flex-col gap-2.5 rounded-[var(--radius-lg)] border border-border bg-bg p-4 transition hover:border-border-hover"
          >
            <div className="flex items-center gap-2.5">
              <span className={`${TONE_TEXT[p.tone]}`}>{p.icon}</span>
              <span className="font-mono text-[10px] font-semibold text-muted">0{i + 1}</span>
            </div>
            <div className="text-sm font-semibold text-text">{p.title}</div>
            <p className="text-xs leading-relaxed text-muted">{p.body}</p>
          </div>
        ))}
      </div>

      <div className="mt-5 flex items-start gap-3 rounded-[var(--radius-md)] border border-[rgba(244,63,94,0.28)] bg-err-soft px-4 py-3">
        <svg viewBox="0 0 24 24" className="mt-0.5 h-4 w-4 shrink-0 text-err" fill="none" stroke="currentColor" strokeWidth="1.9">
          <path d="M12 9v4M12 17h.01" strokeLinecap="round" />
          <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" strokeLinejoin="round" />
        </svg>
        <p className="text-xs leading-relaxed text-text/90">
          <span className="font-semibold text-err">The admin role does not bypass document ACLs.</span>{" "}
          Administration lets you manage connectors and settings, never read a document you were not
          granted. There is no override, no god mode, no back door. The only way to see a document is
          one of the three paths above.
        </p>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------
// Connector instances by scope . where your access could come from
// ------------------------------------------------------------
const SCOPES: Array<{ key: string; label: string; blurb: string }> = [
  { key: "org", label: "Organization", blurb: "Covers the whole source for the tenant." },
  { key: "group", label: "Group", blurb: "Scoped to a single group's credential." },
  { key: "individual", label: "Individual", blurb: "A personal credential, one user." },
];

function ScopeCard() {
  const [instances, setInstances] = useState<Instance[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listInstances()
      .then((v) => live && setInstances(v))
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, []);

  const byScope = useMemo(() => {
    const groups: Record<string, Instance[]> = { org: [], group: [], individual: [] };
    for (const i of instances ?? []) (groups[i.scope_type] ??= []).push(i);
    return groups;
  }, [instances]);

  return (
    <Card className="p-6">
      <SectionTitle
        icon={
          <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="3" y="3" width="7" height="7" rx="1.5" />
            <rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" />
            <path d="M17.5 14v7M14 17.5h7" strokeLinecap="round" />
          </svg>
        }
        title="Sources by scope"
        note="each instance owns its ACL namespace"
      />
      <p className="mt-3 max-w-2xl text-xs leading-relaxed text-muted">
        Each connected source is one or more independent instances, scoped to the organization, a
        group, or an individual, each with its own credential and ACL namespace. Different scopes
        never collide, so a personal source never leaks into an org one.
      </p>

      {error ? (
        <Alert kind="err" className="mt-4" title="Could not load sources">
          {error}
        </Alert>
      ) : instances === null ? (
        <div className="mt-6 flex items-center justify-center gap-2 py-8 text-sm text-muted">
          <Spinner /> loading sources...
        </div>
      ) : instances.length === 0 ? (
        <EmptyState
          className="mt-5"
          icon={
            <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.6">
              <rect x="3" y="3" width="7" height="7" rx="1.5" />
              <rect x="14" y="3" width="7" height="7" rx="1.5" />
              <rect x="3" y="14" width="7" height="7" rx="1.5" />
              <path d="M17.5 14v7M14 17.5h7" strokeLinecap="round" />
            </svg>
          }
          title="No sources connected"
          description="Once a connector instance is added, it appears here grouped by the scope its credential covers."
        />
      ) : (
        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          {SCOPES.map((sc) => {
            const list = byScope[sc.key] ?? [];
            return (
              <div
                key={sc.key}
                className="flex flex-col rounded-[var(--radius-lg)] border border-border bg-bg p-4"
              >
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-semibold text-text">{sc.label}</span>
                  <span className="font-mono text-[11px] tabular-nums text-muted">
                    {list.length}
                  </span>
                </div>
                <span className="mt-0.5 text-[11px] leading-snug text-muted">{sc.blurb}</span>
                <div className="mt-3 space-y-1.5">
                  {list.length > 0 ? (
                    list.map((i) => (
                      <div
                        key={i.id}
                        className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-border bg-surface px-2.5 py-1.5"
                      >
                        <StatusDot kind={i.enabled ? "ok" : "neutral"} size={6} />
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-text">
                          {i.name}
                        </span>
                        <Badge kind="accent">{i.connector}</Badge>
                      </div>
                    ))
                  ) : (
                    <div className="rounded-[var(--radius-sm)] border border-dashed border-border px-2.5 py-2 text-center text-[11px] text-muted">
                      none
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------
// Live access probe . prove what an identity can retrieve
// ------------------------------------------------------------
function AccessCheck({ identity }: { identity: Identity }) {
  const [who, setWho] = useState(identity.user);
  const [groups, setGroups] = useState(identity.groups.join(", "));
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the probe seeded with the signed-in identity as it resolves.
  useEffect(() => {
    setWho(identity.user);
    setGroups(identity.groups.join(", "));
  }, [identity.user, identity.groups]);

  const isSelf = who.trim() === identity.user;

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!query.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResults(null);
    try {
      const probe: Identity = {
        user: who.trim(),
        groups: groups.split(",").map((g) => g.trim()).filter(Boolean),
        roles: [],
        tenant_id: identity.tenant_id,
      };
      setResults(await search(query, probe));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card glow className="p-6">
      <SectionTitle
        icon={
          <svg viewBox="0 0 24 24" className="h-[15px] w-[15px]" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.3-4.3" strokeLinecap="round" />
          </svg>
        }
        title="Check access"
        note="runs the real enforced path"
      />
      <p className="mt-3 max-w-2xl text-xs leading-relaxed text-muted">
        See exactly what a given identity can retrieve for a query. This calls the same
        permission-enforced retrieval path chat and search use, so a result here means the identity
        is genuinely allowed to read that document, and an empty result means it is not.
      </p>

      <form onSubmit={run} className="mt-5 grid gap-3 sm:grid-cols-3">
        <Field label="User" help="Whose access to test">
          <Input value={who} onChange={(e) => setWho(e.target.value)} placeholder="alice" />
        </Field>
        <Field label="Groups" help="Comma-separated group ids">
          <Input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="g1, g2" />
        </Field>
        <Field label="Query" help="What they are searching for" required>
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="confidential roadmap" />
        </Field>
      </form>

      <div className="mt-4 flex items-center gap-3">
        <Button type="submit" onClick={run} disabled={busy || !query.trim()}>
          {busy ? (
            <>
              <Spinner className="border-white/40 border-t-white" /> Checking
            </>
          ) : (
            "Check access"
          )}
        </Button>
        {isSelf ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted">
            <StatusDot kind="run" size={6} /> testing your own identity
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs text-warn">
            <StatusDot kind="warn" size={6} /> testing as{" "}
            <span className="font-mono font-medium">{who.trim() || "?"}</span>
          </span>
        )}
      </div>

      {error && (
        <Alert kind="err" className="mt-4" title="Check failed">
          {error}
        </Alert>
      )}

      {results != null && !error && (
        <div className="mt-5">
          <div className="mb-2.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            <StatusDot kind={results.length > 0 ? "ok" : "err"} size={6} />
            {results.length > 0 ? (
              <span>
                <span className="tabular-nums text-text">{results.length}</span> retrievable{" "}
                {results.length === 1 ? "document" : "documents"}
              </span>
            ) : (
              <span>no permitted results</span>
            )}
          </div>

          {results.length === 0 ? (
            <div className="rounded-[var(--radius-md)] border border-border bg-bg px-4 py-4 text-sm text-muted">
              This identity cannot retrieve anything for that query. Every candidate was filtered out
              by the access check, which is the enforcement working as intended.
            </div>
          ) : (
            <ul className="space-y-1.5">
              {results.map((r) => (
                <li
                  key={r.chunk_id}
                  className="flex items-center gap-3 rounded-[var(--radius-md)] border border-border bg-bg px-3.5 py-2.5 transition hover:border-border-hover"
                >
                  <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-ok" fill="none" stroke="currentColor" strokeWidth="2.2">
                    <path d="M20 6 9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-text">{r.doc_id}</span>
                  <Badge kind="cyan">score {r.score.toFixed(2)}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------
// Shared section header used across the cards
// ------------------------------------------------------------
function SectionTitle({
  icon,
  title,
  note,
}: {
  icon: ReactNode;
  title: string;
  note?: string;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-[var(--radius-sm)] border border-border bg-bg text-cyan">
        {icon}
      </span>
      <h3 className="text-[15px] font-bold tracking-[-0.02em] text-text">{title}</h3>
      {note && (
        <span className="ml-auto font-mono text-[10.5px] font-medium lowercase text-muted">
          {note}
        </span>
      )}
    </div>
  );
}
