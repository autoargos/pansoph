import { useEffect, useState } from "react";

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
import { Badge, Button, Card, Dot, Field, Input, PageHeader, relativeTime, Spinner } from "../ui.tsx";

function runKind(status?: string): "ok" | "warn" | "err" {
  if (status === "success") return "ok";
  if (status === "running") return "warn";
  return "err";
}

function fmtDuration(s: number | null): string {
  if (s == null) return "-";
  return s < 1 ? `${Math.round(s * 1000)}ms` : `${s.toFixed(1)}s`;
}

export default function Connectors() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
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
      ) : instances.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted">
          No connector instances yet. Add one to start ingesting. Available types:{" "}
          <span className="font-medium text-ink">{types.join(", ") || "none"}</span>.
        </Card>
      ) : (
        <div className="space-y-3">
          {instances.map((inst) => {
            const last = inst.last_run;
            const kind = last ? runKind(last.status) : "warn";
            return (
              <Card key={inst.id} className="overflow-hidden">
                <div className="flex items-center gap-4 px-5 py-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{inst.name}</span>
                      <Badge kind="accent">{inst.connector}</Badge>
                    </div>
                    <div className="mt-1 flex items-center gap-3 text-xs text-muted">
                      <span className="font-mono">{inst.id}</span>
                      <span>·</span>
                      <span>
                        {inst.schedule_seconds > 0
                          ? `every ${inst.schedule_seconds}s`
                          : "manual"}
                      </span>
                    </div>
                  </div>

                  <div className="hidden w-44 text-xs text-muted sm:block">
                    {last ? (
                      <div className="flex items-center gap-1.5">
                        <Dot kind={kind} />
                        <span className="capitalize text-ink">{last.status}</span>
                        <span>· {last.docs_indexed} docs</span>
                      </div>
                    ) : (
                      <span className="text-faint">never run</span>
                    )}
                    <div className="mt-0.5">
                      {last ? `${relativeTime(last.started_at)} · ${fmtDuration(last.duration_seconds)}` : ""}
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <Button variant="ghost" onClick={() => toggleRuns(inst.id)}>
                      Runs
                    </Button>
                    <Button onClick={() => sync(inst.id)} disabled={syncing === inst.id}>
                      {syncing === inst.id ? <Spinner className="border-white/40 border-t-white" /> : "Sync now"}
                    </Button>
                    <button
                      onClick={() => deleteInstance(inst.id).then(refresh)}
                      className="rounded-lg px-2 py-2 text-faint hover:bg-err-soft hover:text-err"
                      title="Delete"
                    >
                      ✕
                    </button>
                  </div>
                </div>

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
        </div>
      )}

      {adding && (
        <AddConnector
          types={types}
          onClose={() => setAdding(false)}
          onCreated={() => {
            setAdding(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function AddConnector({
  types,
  onClose,
  onCreated,
}: {
  types: string[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [connector, setConnector] = useState(types.includes("github") ? "github" : (types[0] ?? ""));
  const [name, setName] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [token, setToken] = useState("");
  const [configJson, setConfigJson] = useState("{}");
  const [schedule, setSchedule] = useState("0");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      let config: Record<string, unknown> = {};
      if (connector === "github") {
        config = { owner, repo, ...(token ? { token } : {}) };
      } else {
        config = JSON.parse(configJson || "{}");
      }
      await createInstance({
        connector,
        name: name || `${connector} ${owner}/${repo}`.trim(),
        instance_id: instanceId || name.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
        tenant_id: "default",
        config,
        schedule_seconds: parseInt(schedule, 10) || 0,
      });
      onCreated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-ink/30 p-4" onClick={onClose}>
      <Card className="w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 text-lg font-semibold">Add connector</h2>
        <div className="space-y-3">
          <Field label="Type">
            <select
              value={connector}
              onChange={(e) => setConnector(e.target.value)}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm"
            >
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My GitHub repo" />
          </Field>
          <Field label="Instance id (stable, namespacing)">
            <Input
              value={instanceId}
              onChange={(e) => setInstanceId(e.target.value)}
              placeholder="my-repo"
            />
          </Field>
          {connector === "github" ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Owner">
                <Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="octocat" />
              </Field>
              <Field label="Repo">
                <Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="Spoon-Knife" />
              </Field>
              <div className="col-span-2">
                <Field label="Token (optional, for private repos)">
                  <Input value={token} onChange={(e) => setToken(e.target.value)} placeholder="ghp_..." />
                </Field>
              </div>
            </div>
          ) : (
            <Field label="Config (JSON)">
              <textarea
                value={configJson}
                onChange={(e) => setConfigJson(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs"
              />
            </Field>
          )}
          <Field label="Schedule (seconds, 0 = manual)">
            <Input value={schedule} onChange={(e) => setSchedule(e.target.value)} />
          </Field>
          {error && <p className="text-sm text-err">{error}</p>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? <Spinner className="border-white/40 border-t-white" /> : "Create"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
