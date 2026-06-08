import { useEffect, useMemo, useState } from "react";

import {
  type Identity,
  type Instance,
  listInstances,
  search,
  type SearchResult,
} from "../api.ts";
import { Badge, Button, Card, Field, PageHeader, Spinner } from "../ui.tsx";

// Access administration. The headline feature, made visible: who you are, what roles
// gate, how connector instances are scoped, and a live check of what any identity can
// retrieve. Built on the existing API (no new endpoints): the check tool drives the
// same permission-enforced /search path agora uses for everyone.

const ROLE_GUIDE: Array<{ role: string; gates: string }> = [
  { role: "admin", gates: "Everything: manage connectors, access administration, all settings." },
  { role: "connector_admin", gates: "Create, edit, sync, and delete connector instances." },
  { role: "member", gates: "Read-only: search and chat within their permitted documents." },
];

const inputCls =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent";

export default function Access({ identity }: { identity: Identity }) {
  const [instances, setInstances] = useState<Instance[]>([]);
  useEffect(() => {
    listInstances().then(setInstances).catch(() => setInstances([]));
  }, []);

  const byScope = useMemo(() => {
    const groups: Record<string, Instance[]> = { org: [], group: [], individual: [] };
    for (const i of instances) (groups[i.scope_type] ??= []).push(i);
    return groups;
  }, [instances]);

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageHeader
        title="Access administration"
        subtitle="Who can see what, and why. Roles gate management actions; groups gate document retrieval."
      />

      <Card className="p-5">
        <h3 className="text-sm font-semibold">Your access</h3>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">signed in as</span>
          <span className="font-medium">{identity.user}</span>
          <span className="text-muted">· roles</span>
          {identity.roles.length ? (
            identity.roles.map((r) => (
              <Badge key={r} kind="neutral">
                {r}
              </Badge>
            ))
          ) : (
            <span className="text-faint">none</span>
          )}
          <span className="text-muted">· groups</span>
          {identity.groups.length ? (
            identity.groups.map((g) => (
              <Badge key={g} kind="accent">
                {g}
              </Badge>
            ))
          ) : (
            <span className="text-faint">none</span>
          )}
        </div>
      </Card>

      <Card className="p-5">
        <h3 className="text-sm font-semibold">Roles</h3>
        <table className="mt-3 w-full text-sm">
          <tbody>
            {ROLE_GUIDE.map((r) => (
              <tr key={r.role} className="border-t border-line/70 first:border-0">
                <td className="py-2 pr-4 align-top">
                  <Badge kind="neutral">{r.role}</Badge>
                </td>
                <td className="py-2 text-muted">{r.gates}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card className="p-5">
        <h3 className="text-sm font-semibold">Connector instances by scope</h3>
        <p className="mt-0.5 text-xs text-muted">
          Each source is configured as many independent instances, scoped to an organization, a
          group, or an individual, each with its own credentials and ACL namespace.
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          {(["org", "group", "individual"] as const).map((scope) => (
            <div key={scope} className="rounded-lg border border-line bg-canvas p-3">
              <div className="text-xs font-semibold capitalize text-ink">{scope}</div>
              <div className="mt-2 space-y-1.5">
                {byScope[scope]?.length ? (
                  byScope[scope].map((i) => (
                    <div key={i.id} className="flex items-center gap-2 text-xs">
                      <span className="truncate font-medium">{i.name}</span>
                      <Badge kind="accent">{i.connector}</Badge>
                      {i.scope_ref && <span className="text-faint">{i.scope_ref}</span>}
                    </div>
                  ))
                ) : (
                  <div className="text-xs text-faint">none</div>
                )}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <AccessCheck />
    </div>
  );
}

// A live "who can see what" probe. Runs the real permission-enforced retrieval path
// as the named identity, so an admin can prove that a given user sees only what they
// are allowed to. This is the same /search agora serves; nothing is bypassed.
function AccessCheck() {
  const [who, setWho] = useState("alice");
  const [groups, setGroups] = useState("g1");
  const [query, setQuery] = useState("confidential roadmap");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setResults(null);
    try {
      const id: Identity = {
        user: who.trim(),
        groups: groups.split(",").map((g) => g.trim()).filter(Boolean),
        roles: [],
        tenant_id: "default",
      };
      setResults(await search(query, id));
    } catch {
      setResults([]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-5">
      <h3 className="text-sm font-semibold">Check access</h3>
      <p className="mt-0.5 text-xs text-muted">
        See exactly what a given identity can retrieve. Runs the same permission-enforced path as
        chat and search.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Field label="User">
          <input className={inputCls} value={who} onChange={(e) => setWho(e.target.value)} />
        </Field>
        <Field label="Groups (comma-separated)">
          <input className={inputCls} value={groups} onChange={(e) => setGroups(e.target.value)} />
        </Field>
        <Field label="Query">
          <input className={inputCls} value={query} onChange={(e) => setQuery(e.target.value)} />
        </Field>
      </div>
      <div className="mt-3">
        <Button onClick={run} disabled={busy}>
          {busy ? <Spinner className="border-white/40 border-t-white" /> : "Check"}
        </Button>
      </div>
      {results != null && (
        <div className="mt-4">
          {results.length === 0 ? (
            <div className="text-sm text-muted">
              No permitted results: this identity cannot retrieve anything for that query.
            </div>
          ) : (
            <ul className="space-y-1.5">
              {results.map((r) => (
                <li key={r.chunk_id} className="flex items-center gap-2 text-xs">
                  <span className="text-ok">✓</span>
                  <span className="font-mono text-muted">{r.doc_id}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}
