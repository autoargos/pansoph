import { useEffect, useMemo, useState } from "react";

import {
  connectorTypes,
  createInstance,
  deleteInstance,
  type Instance,
  instanceRuns,
  listInstances,
  type RunInfo,
  syncInstance,
} from "../api.ts";
import { Badge, Button, Card, Dot, Field, PageHeader, relativeTime, Spinner } from "../ui.tsx";

// What each connector type needs. Drives the Add form so it shows the right fields
// instead of a raw JSON box.
interface FieldDef {
  key: string;
  label: string;
  placeholder?: string;
  help?: string;
  kind?: "text" | "number" | "lines" | "csv" | "checkbox";
}
interface ConnDef {
  label: string;
  blurb: string;
  fields: FieldDef[];
}

const CONNECTORS: Record<string, ConnDef> = {
  github: {
    label: "GitHub",
    blurb: "A repository's docs and code.",
    fields: [
      { key: "owner", label: "Owner", placeholder: "facebook" },
      { key: "repo", label: "Repository", placeholder: "react" },
      {
        key: "token",
        label: "Token",
        placeholder: "github_pat_…",
        help: "Optional for public repos. Needed for private repos or to avoid rate limits.",
      },
      { key: "path", label: "Path filter", placeholder: "docs", help: "Optional. Limit to a folder." },
      { key: "max_files", label: "Max files", placeholder: "300", kind: "number" },
    ],
  },
  weblink: {
    label: "Public links",
    blurb: "Any directly-fetchable URLs.",
    fields: [
      { key: "urls", label: "URLs (one per line)", placeholder: "https://example.com/page", kind: "lines" },
      { key: "public", label: "Public to everyone", kind: "checkbox" },
      { key: "allow_groups", label: "Restrict to groups", placeholder: "g1, g2", kind: "csv" },
    ],
  },
  google_drive: {
    label: "Google Drive",
    blurb: "Files and Docs from Drive.",
    fields: [
      {
        key: "token",
        label: "OAuth access token",
        placeholder: "ya29.…",
        help: "From the OAuth Playground with scope drive.readonly.",
      },
      { key: "query", label: "Query", placeholder: "'<folderId>' in parents and trashed=false", help: "Optional scope." },
    ],
  },
  confluence: {
    label: "Confluence",
    blurb: "Pages from a Confluence Cloud site.",
    fields: [
      { key: "base_url", label: "Base URL", placeholder: "https://your-site.atlassian.net" },
      { key: "email", label: "Email", placeholder: "you@company.com" },
      { key: "token", label: "API token", placeholder: "ATATT…", help: "id.atlassian.com → API tokens." },
      { key: "space_key", label: "Space key", placeholder: "ENG", help: "Optional." },
    ],
  },
  granola: {
    label: "Granola",
    blurb: "Meeting notes via Granola's MCP server.",
    fields: [{ key: "server_url", label: "Granola MCP URL", placeholder: "https://…/mcp" }],
  },
  mcp: {
    label: "MCP bridge",
    blurb: "Any MCP server that exposes resources.",
    fields: [
      { key: "server_url", label: "MCP server URL", placeholder: "http://mcp-sample:8000/mcp" },
      { key: "public", label: "Public to everyone", kind: "checkbox" },
      { key: "allow_groups", label: "Restrict to groups", placeholder: "g1", kind: "csv" },
    ],
  },
  memory: { label: "In-memory (test)", blurb: "A built-in test connector.", fields: [] },
};

const SCHEDULES: Array<[string, number]> = [
  ["Manual", 0],
  ["Every 5 minutes", 300],
  ["Every 15 minutes", 900],
  ["Every hour", 3600],
  ["Every 6 hours", 21600],
  ["Every day", 86400],
];

function scheduleLabel(s: number): string {
  if (!s) return "manual";
  if (s % 86400 === 0) return `every ${s / 86400}d`;
  if (s % 3600 === 0) return `every ${s / 3600}h`;
  if (s % 60 === 0) return `every ${s / 60}m`;
  return `every ${s}s`;
}

function runKind(status?: string): "ok" | "warn" | "err" {
  if (status === "success") return "ok";
  if (status === "running") return "warn";
  return "err";
}

function fmtDuration(s: number | null): string {
  if (s == null) return "-";
  return s < 1 ? `${Math.round(s * 1000)}ms` : `${s.toFixed(1)}s`;
}

function configToValues(connector: string, config: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of CONNECTORS[connector]?.fields ?? []) {
    if (f.kind === "checkbox") continue;
    const v = config[f.key];
    if (Array.isArray(v)) out[f.key] = v.join(f.kind === "lines" ? "\n" : ", ");
    else if (v != null) out[f.key] = String(v);
  }
  return out;
}

function configToChecks(connector: string, config: Record<string, unknown>): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const f of CONNECTORS[connector]?.fields ?? []) {
    if (f.kind === "checkbox") out[f.key] = Boolean(config[f.key]);
  }
  return out;
}

function Avatar({ connector }: { connector: string }) {
  const letter = (CONNECTORS[connector]?.label ?? connector).slice(0, 1).toUpperCase();
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-sm font-semibold text-accent-ink">
      {letter}
    </div>
  );
}

export default function Connectors() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Instance | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);

  async function refresh() {
    const [inst, ty] = await Promise.all([listInstances(), connectorTypes()]);
    setInstances(inst);
    setTypes(ty);
    setLoading(false);
  }

  useEffect(() => {
    refresh().catch(() => setLoading(false));
    const t = setInterval(() => listInstances().then(setInstances).catch(() => {}), 6000);
    return () => clearInterval(t);
  }, []);

  async function sync(id: string) {
    setSyncing(id);
    try {
      await syncInstance(id);
      await refresh();
      if (expanded === id) setRuns(await instanceRuns(id));
    } finally {
      setSyncing(null);
    }
  }

  async function toggleRuns(id: string) {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    setRuns(await instanceRuns(id));
  }

  async function remove(inst: Instance) {
    if (!window.confirm(`Delete connector "${inst.name}"? Indexed documents stay until re-synced.`)) {
      return;
    }
    await deleteInstance(inst.id);
    await refresh();
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Connectors"
        subtitle="Sources feeding pansoph. Every instance has its own credentials, schedule, and ACL namespace."
        action={<Button onClick={() => setAdding(true)}>+ Add connector</Button>}
      />

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <div className="space-y-3">
          {instances.map((inst) => {
            const last = inst.last_run;
            const kind = last ? runKind(last.status) : "warn";
            return (
              <Card key={inst.id} className="overflow-hidden">
                <div className="flex items-center gap-4 px-5 py-4">
                  <Avatar connector={inst.connector} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{inst.name}</span>
                      <Badge kind="accent">{inst.connector}</Badge>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                      <span className="font-mono">{inst.id}</span>
                      <span>·</span>
                      <span className="inline-flex items-center gap-1">
                        <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2">
                          <circle cx="12" cy="12" r="9" />
                          <path d="M12 7v5l3 2" strokeLinecap="round" />
                        </svg>
                        {scheduleLabel(inst.schedule_seconds)}
                      </span>
                    </div>
                  </div>

                  <div className="hidden w-44 text-xs text-muted sm:block">
                    {last ? (
                      <>
                        <div className="flex items-center gap-1.5">
                          <Dot kind={kind} />
                          <span className="capitalize text-ink">{last.status}</span>
                          <span>· {last.docs_indexed} docs</span>
                        </div>
                        <div className="mt-0.5">
                          {relativeTime(last.started_at)} · {fmtDuration(last.duration_seconds)}
                        </div>
                      </>
                    ) : (
                      <span className="text-faint">never run</span>
                    )}
                  </div>

                  <div className="flex items-center gap-2">
                    <Button variant="ghost" onClick={() => setEditing(inst)}>
                      Edit
                    </Button>
                    <Button variant="ghost" onClick={() => toggleRuns(inst.id)}>
                      Runs
                    </Button>
                    <Button onClick={() => sync(inst.id)} disabled={syncing === inst.id}>
                      {syncing === inst.id ? (
                        <Spinner className="border-white/40 border-t-white" />
                      ) : (
                        "Sync now"
                      )}
                    </Button>
                    <button
                      onClick={() => remove(inst)}
                      className="rounded-lg px-2 py-2 text-faint hover:bg-err-soft hover:text-err"
                      title="Delete"
                    >
                      ✕
                    </button>
                  </div>
                </div>

                {last?.status === "error" && last.error && (
                  <div className="border-t border-line bg-err-soft px-5 py-2 text-xs text-err">
                    {last.error.length > 200 ? `${last.error.slice(0, 200)}…` : last.error}
                  </div>
                )}

                {expanded === inst.id && (
                  <div className="border-t border-line bg-canvas px-5 py-3">
                    {runs.length === 0 ? (
                      <div className="py-2 text-xs text-muted">No runs yet.</div>
                    ) : (
                      <table className="w-full text-xs">
                        <thead className="text-faint">
                          <tr className="text-left">
                            <th className="py-1 font-medium">Started</th>
                            <th className="py-1 font-medium">Status</th>
                            <th className="py-1 font-medium">Docs</th>
                            <th className="py-1 font-medium">Duration</th>
                            <th className="py-1 font-medium">Detail</th>
                          </tr>
                        </thead>
                        <tbody className="text-muted">
                          {runs.map((r, i) => (
                            <tr key={i} className="border-t border-line/70">
                              <td className="py-1.5">{relativeTime(r.started_at)}</td>
                              <td className="py-1.5">
                                <span className="inline-flex items-center gap-1.5">
                                  <Dot kind={runKind(r.status)} />
                                  <span className="capitalize">{r.status}</span>
                                </span>
                              </td>
                              <td className="py-1.5">{r.docs_indexed}</td>
                              <td className="py-1.5">{fmtDuration(r.duration_seconds)}</td>
                              <td className="max-w-xs truncate py-1.5 text-err">{r.error ?? ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
          {instances.length === 0 && (
            <Card className="p-10 text-center text-sm text-muted">
              No connectors yet. Click <span className="font-medium text-ink">Add connector</span> to start.
            </Card>
          )}
        </div>
      )}

      {(adding || editing) && (
        <ConnectorForm
          types={types}
          editing={editing}
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
          onSaved={() => {
            setAdding(false);
            setEditing(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function ConnectorForm({
  types,
  editing,
  onClose,
  onSaved,
}: {
  types: string[];
  editing: Instance | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const ordered = useMemo(
    () => types.filter((t) => t in CONNECTORS).sort((a, b) => (a === "github" ? -1 : b === "github" ? 1 : 0)),
    [types],
  );
  const [connector, setConnector] = useState(editing?.connector ?? ordered[0] ?? "github");
  const [name, setName] = useState(editing?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(() =>
    editing ? configToValues(editing.connector, editing.config) : {},
  );
  const [checks, setChecks] = useState<Record<string, boolean>>(() =>
    editing ? configToChecks(editing.connector, editing.config) : {},
  );
  const [schedule, setSchedule] = useState(editing?.schedule_seconds ?? 0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const def = CONNECTORS[connector];

  function changeType(t: string) {
    setConnector(t);
    setValues({});
    setChecks({});
  }

  function buildConfig(): Record<string, unknown> {
    const config: Record<string, unknown> = {};
    for (const f of def.fields) {
      const raw = (values[f.key] ?? "").trim();
      if (f.kind === "checkbox") {
        if (checks[f.key]) config[f.key] = true;
      } else if (f.kind === "number") {
        if (raw) config[f.key] = parseInt(raw, 10);
      } else if (f.kind === "lines") {
        const arr = raw.split("\n").map((s) => s.trim()).filter(Boolean);
        if (arr.length) config[f.key] = arr;
      } else if (f.kind === "csv") {
        const arr = raw.split(",").map((s) => s.trim()).filter(Boolean);
        if (arr.length) config[f.key] = arr;
      } else if (raw) {
        config[f.key] = raw;
      }
    }
    return config;
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const finalName = name.trim() || def.label;
      await createInstance({
        connector,
        name: finalName,
        instance_id:
          editing?.id ?? finalName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""),
        tenant_id: "default",
        config: buildConfig(),
        schedule_seconds: schedule,
      });
      onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent";

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-ink/40 p-4"
      onClick={onClose}
    >
      <Card className="max-h-[88vh] w-full max-w-md overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">{editing ? "Edit connector" : "Add connector"}</h2>
        <p className="mb-4 mt-0.5 text-sm text-muted">{def?.blurb}</p>
        <div className="space-y-3">
          <Field label="Type">
            <select
              value={connector}
              onChange={(e) => changeType(e.target.value)}
              className={`${inputCls} disabled:opacity-60`}
              disabled={!!editing}
            >
              {ordered.map((t) => (
                <option key={t} value={t}>
                  {CONNECTORS[t].label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Name">
            <input
              className={inputCls}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={`e.g. ${def?.label}`}
            />
          </Field>

          {def?.fields.map((f) =>
            f.kind === "checkbox" ? (
              <label key={f.key} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={!!checks[f.key]}
                  onChange={(e) => setChecks({ ...checks, [f.key]: e.target.checked })}
                />
                {f.label}
              </label>
            ) : (
              <Field key={f.key} label={f.label}>
                {f.kind === "lines" ? (
                  <textarea
                    className={`${inputCls} font-mono text-xs`}
                    rows={3}
                    value={values[f.key] ?? ""}
                    onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                    placeholder={f.placeholder}
                  />
                ) : (
                  <input
                    className={inputCls}
                    value={values[f.key] ?? ""}
                    onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                    placeholder={f.placeholder}
                  />
                )}
                {f.help && <span className="mt-1 block text-xs text-faint">{f.help}</span>}
              </Field>
            ),
          )}

          <Field label="Schedule">
            <select
              value={schedule}
              onChange={(e) => setSchedule(parseInt(e.target.value, 10))}
              className={inputCls}
            >
              {SCHEDULES.map(([label, v]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </Field>

          {error && <p className="text-sm text-err">{error}</p>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? (
              <Spinner className="border-white/40 border-t-white" />
            ) : editing ? (
              "Save"
            ) : (
              "Create"
            )}
          </Button>
        </div>
      </Card>
    </div>
  );
}
