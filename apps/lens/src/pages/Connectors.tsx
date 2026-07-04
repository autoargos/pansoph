import { useEffect, useMemo, useState } from "react";

import {
  type ConnectorSpec,
  connectorSpecs,
  createInstance,
  deleteInstance,
  type Instance,
  instanceRuns,
  listInstances,
  type RunInfo,
  type SpecField,
  type SpecFieldKind,
  syncInstance,
} from "../api.ts";
import { Badge, Button, Card, Dot, Field, PageHeader, relativeTime, Spinner } from "../ui.tsx";

// Fields whose value is a checkbox in the form. A single source of truth so the value
// bookkeeping and the config builder agree.
function isBoolField(kind: SpecFieldKind): boolean {
  return kind === "bool";
}

// Fields the browser should mask. Secrets never round-trip through the bundle as plain
// text, and the backend never returns their values, so these render empty every time.
function isSecretField(kind: SpecFieldKind): boolean {
  return kind === "secret" || kind === "secret_file" || kind === "oauth";
}

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

// What to show on a card: a cron expression takes precedence over the interval.
function instanceSchedule(inst: Instance): string {
  if (inst.schedule_cron) return `cron ${inst.schedule_cron}`;
  return scheduleLabel(inst.schedule_seconds);
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

function configToValues(spec: ConnectorSpec | undefined, config: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of spec?.fields ?? []) {
    if (isBoolField(f.kind) || isSecretField(f.kind)) continue;
    const v = config[f.key];
    if (Array.isArray(v)) out[f.key] = v.join("\n");
    else if (v != null) out[f.key] = String(v);
  }
  return out;
}

function configToChecks(spec: ConnectorSpec | undefined, config: Record<string, unknown>): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const f of spec?.fields ?? []) {
    if (isBoolField(f.kind)) out[f.key] = Boolean(config[f.key]);
  }
  return out;
}

function Avatar({ title }: { title: string }) {
  const letter = title.slice(0, 1).toUpperCase();
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-sm font-semibold text-accent-ink">
      {letter}
    </div>
  );
}

export default function Connectors({ canManage }: { canManage: boolean }) {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [specs, setSpecs] = useState<ConnectorSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Instance | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);

  // Human title per registry name, so a card shows "GitHub" not "github". Falls back to
  // the raw name for an instance whose connector no longer declares a spec.
  const titleOf = useMemo(() => {
    const map = new Map(specs.map((s) => [s.name, s.title]));
    return (name: string): string => map.get(name) ?? name;
  }, [specs]);

  async function refresh() {
    const [inst, sp] = await Promise.all([listInstances(), connectorSpecs()]);
    setInstances(inst);
    setSpecs(sp);
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
        subtitle="Sources feeding pansoph. Every instance has its own credentials, schedule, scope, and ACL namespace."
        action={
          canManage ? <Button onClick={() => setAdding(true)}>+ Add connector</Button> : undefined
        }
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
                  <Avatar title={titleOf(inst.connector)} />
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
                        {instanceSchedule(inst)}
                      </span>
                      <span>·</span>
                      <span className="inline-flex items-center gap-1 capitalize" title="instance scope">
                        <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M12 3l8 4v5c0 4.5-3 6.8-8 7.5C7 18.8 4 16.5 4 12V7z" strokeLinejoin="round" />
                        </svg>
                        {inst.scope_type}
                        {inst.scope_ref ? `: ${inst.scope_ref}` : ""}
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
                    <Button variant="ghost" onClick={() => toggleRuns(inst.id)}>
                      Runs
                    </Button>
                    {canManage && (
                      <>
                        <Button variant="ghost" onClick={() => setEditing(inst)}>
                          Edit
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
                      </>
                    )}
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
          specs={specs}
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
  specs,
  editing,
  onClose,
  onSaved,
}: {
  specs: ConnectorSpec[];
  editing: Instance | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  // The backend already returns specs sorted by title, so preserve that order.
  const [connector, setConnector] = useState(editing?.connector ?? specs[0]?.name ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const def = useMemo<ConnectorSpec | undefined>(
    () => specs.find((s) => s.name === connector),
    [specs, connector],
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    editing ? configToValues(specs.find((s) => s.name === editing.connector), editing.config) : {},
  );
  const [checks, setChecks] = useState<Record<string, boolean>>(() =>
    editing ? configToChecks(specs.find((s) => s.name === editing.connector), editing.config) : {},
  );
  const [schedule, setSchedule] = useState(editing?.schedule_seconds ?? 0);
  const [cronMode, setCronMode] = useState(!!editing?.schedule_cron);
  const [cron, setCron] = useState(editing?.schedule_cron ?? "");
  const [scopeType, setScopeType] = useState(editing?.scope_type ?? "org");
  const [scopeRef, setScopeRef] = useState(editing?.scope_ref ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function changeType(t: string) {
    setConnector(t);
    setValues({});
    setChecks({});
  }

  // Build the config object with the type each field kind implies: bool a boolean, int a
  // number, list a string array (split on newlines), everything else a string.
  function buildConfig(): Record<string, unknown> {
    const config: Record<string, unknown> = {};
    for (const f of def?.fields ?? []) {
      if (isBoolField(f.kind)) {
        if (checks[f.key]) config[f.key] = true;
        continue;
      }
      const raw = (values[f.key] ?? "").trim();
      if (f.kind === "int") {
        if (raw) config[f.key] = Number(raw);
      } else if (f.kind === "list") {
        const arr = raw.split("\n").map((s) => s.trim()).filter(Boolean);
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
      const finalName = name.trim() || def?.title || connector;
      await createInstance({
        connector,
        name: finalName,
        instance_id:
          editing?.id ?? finalName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""),
        tenant_id: "default",
        config: buildConfig(),
        schedule_seconds: cronMode ? 0 : schedule,
        schedule_cron: cronMode ? cron.trim() : "",
        scope_type: scopeType,
        scope_ref: scopeType === "org" ? "" : scopeRef.trim(),
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
        <p className="mb-4 mt-0.5 text-sm text-muted">{def?.description}</p>
        <div className="space-y-3">
          <Field label="Type">
            <select
              value={connector}
              onChange={(e) => changeType(e.target.value)}
              className={`${inputCls} disabled:opacity-60`}
              disabled={!!editing}
            >
              {specs.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.title}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Name">
            <input
              className={inputCls}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={`e.g. ${def?.title ?? connector}`}
            />
          </Field>

          {def?.fields.map((f) => <SpecFieldInput key={f.key} field={f} values={values} checks={checks} setValues={setValues} setChecks={setChecks} inputCls={inputCls} />)}

          <Field label="Scope">
            <select
              value={scopeType}
              onChange={(e) => setScopeType(e.target.value)}
              className={inputCls}
            >
              <option value="org">Organization (whole source)</option>
              <option value="group">Group</option>
              <option value="individual">Individual</option>
            </select>
            {scopeType !== "org" && (
              <input
                className={`${inputCls} mt-2`}
                value={scopeRef}
                onChange={(e) => setScopeRef(e.target.value)}
                placeholder={scopeType === "group" ? "group id, e.g. g1" : "user id, e.g. alice"}
              />
            )}
            <span className="mt-1 block text-xs text-faint">
              Which slice this instance covers. Management metadata; the source ACL still governs
              who can read its documents.
            </span>
          </Field>

          <Field label="Schedule">
            <select
              value={cronMode ? "cron" : String(schedule)}
              onChange={(e) => {
                if (e.target.value === "cron") {
                  setCronMode(true);
                } else {
                  setCronMode(false);
                  setSchedule(parseInt(e.target.value, 10));
                }
              }}
              className={inputCls}
            >
              {SCHEDULES.map(([label, v]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
              <option value="cron">Custom (cron)…</option>
            </select>
            {cronMode && (
              <>
                <input
                  className={`${inputCls} mt-2 font-mono text-xs`}
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 */6 * * *"
                />
                <span className="mt-1 block text-xs text-faint">
                  Standard 5-field cron (min hour day month weekday), evaluated in UTC.
                </span>
              </>
            )}
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

// One form control, chosen from the field's declared kind. This is the whole of the
// spec-driven rendering: string renders text, int renders number, bool renders a checkbox,
// list renders a newline textarea, and secret / secret_file / oauth render a masked
// password input (oauth has no interactive flow yet, so it is entered by hand for now).
function SpecFieldInput({
  field,
  values,
  checks,
  setValues,
  setChecks,
  inputCls,
}: {
  field: SpecField;
  values: Record<string, string>;
  checks: Record<string, boolean>;
  setValues: (v: Record<string, string>) => void;
  setChecks: (v: Record<string, boolean>) => void;
  inputCls: string;
}) {
  const { key, label, kind, required, help, placeholder } = field;
  const legend = required ? label : `${label} (optional)`;

  if (isBoolField(kind)) {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={!!checks[key]}
          onChange={(e) => setChecks({ ...checks, [key]: e.target.checked })}
        />
        {label}
      </label>
    );
  }

  const control =
    kind === "list" ? (
      <textarea
        className={`${inputCls} font-mono text-xs`}
        rows={3}
        value={values[key] ?? ""}
        onChange={(e) => setValues({ ...values, [key]: e.target.value })}
        placeholder={placeholder}
      />
    ) : (
      <input
        className={inputCls}
        type={isSecretField(kind) ? "password" : kind === "int" ? "number" : "text"}
        autoComplete={isSecretField(kind) ? "off" : undefined}
        value={values[key] ?? ""}
        onChange={(e) => setValues({ ...values, [key]: e.target.value })}
        placeholder={placeholder}
      />
    );

  return (
    <Field label={legend}>
      {control}
      {help && <span className="mt-1 block text-xs text-faint">{help}</span>}
    </Field>
  );
}
