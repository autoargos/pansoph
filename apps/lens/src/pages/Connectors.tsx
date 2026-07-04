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
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  relativeTime,
  Select,
  Spinner,
  StatusDot,
  type StatusKind,
  Table,
  TBody,
  TD,
  Textarea,
  TH,
  THead,
  Tooltip,
  TR,
  useToasts,
} from "../ui.tsx";
import {
  connectorBrand,
  IconAlert,
  IconClock,
  IconEdit,
  IconEye,
  IconEyeOff,
  IconGrid,
  IconLock,
  IconPlus,
  IconRuns,
  IconShield,
  IconSync,
  IconTrash,
  oauthProvider,
} from "./connectorGlyphs.tsx";

// ============================================================
// Connectors: the sources feeding pansoph. Spec driven: the Add form and every field
// come from GET /api/connectors/specs, so a new connector needs no UI change. Health,
// stats, schedule, runs, and the OAuth Connect flow are all rendered in the Obsidian
// design language. Behavior (fetch specs, POST instances, sync, delete) is unchanged.
// ============================================================

// The api.ts SpecField type does not yet declare oauth_provider (the OAuth backend flow
// lands in parallel). Read it defensively without editing the shared client.
type OAuthField = SpecField & { oauth_provider?: string };
function oauthProviderOf(field: SpecField): string | undefined {
  return (field as OAuthField).oauth_provider;
}

function isBoolField(kind: SpecFieldKind): boolean {
  return kind === "bool";
}

// Fields the browser masks. The backend never returns their values, so these render
// empty every time. OAuth is credential bearing too, but it has its own Connect flow.
function isSecretField(kind: SpecFieldKind): boolean {
  return kind === "secret" || kind === "secret_file";
}

const SCHEDULES: Array<[string, number]> = [
  ["Manual", 0],
  ["Every 5 minutes", 300],
  ["Every 15 minutes", 900],
  ["Every hour", 3600],
  ["Every 6 hours", 21600],
  ["Every day", 86400],
];

function scheduleLabel(sec: number): string {
  if (!sec) return "manual";
  if (sec % 86400 === 0) return `every ${sec / 86400}d`;
  if (sec % 3600 === 0) return `every ${sec / 3600}h`;
  if (sec % 60 === 0) return `every ${sec / 60}m`;
  return `every ${sec}s`;
}

function instanceSchedule(inst: Instance): string {
  if (inst.schedule_cron) return `cron ${inst.schedule_cron}`;
  return scheduleLabel(inst.schedule_seconds);
}

function runKind(status?: string): StatusKind {
  if (status === "success") return "ok";
  if (status === "running") return "run";
  return "err";
}

// A connector's overall health: green when the last run succeeded, cyan while running,
// rose on error, amber when it has never run.
function healthKind(inst: Instance): StatusKind {
  if (!inst.last_run) return "warn";
  return runKind(inst.last_run.status);
}

function healthLabel(inst: Instance): string {
  if (!inst.last_run) return "idle";
  const st = inst.last_run.status;
  if (st === "success") return "healthy";
  if (st === "running") return "syncing";
  return "attention";
}

function fmtDuration(sec: number | null): string {
  if (sec == null) return "-";
  if (sec < 1) return `${Math.round(sec * 1000)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const r = Math.round(sec % 60);
  return `${m}m ${r.toString().padStart(2, "0")}s`;
}

// Compact count: 14200 -> 14.2k. Used for the chunk stat.
function fmtCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

// Chunk estimate for a card stat. The API exposes docs_indexed but not a chunk count, so
// derive a stable estimate from documents when the config does not carry one. Reads a
// numeric config.chunks if a connector happens to report it.
function chunkEstimate(inst: Instance): number {
  const cfg = inst.config?.chunks;
  if (typeof cfg === "number" && cfg >= 0) return cfg;
  const docs = inst.last_run?.docs_indexed ?? 0;
  return docs * 8;
}

function configToValues(
  spec: ConnectorSpec | undefined,
  config: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of spec?.fields ?? []) {
    if (isBoolField(f.kind) || isSecretField(f.kind) || f.kind === "oauth") continue;
    const v = config[f.key];
    if (Array.isArray(v)) out[f.key] = v.join("\n");
    else if (v != null) out[f.key] = String(v);
  }
  return out;
}

function configToChecks(
  spec: ConnectorSpec | undefined,
  config: Record<string, unknown>,
): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const f of spec?.fields ?? []) {
    if (isBoolField(f.kind)) out[f.key] = Boolean(config[f.key]);
  }
  return out;
}

// The tinted logo tile that identifies a connector by brand.
function ConnectorLogo({ connector, size = 42 }: { connector: string; size?: number }) {
  const { glyph: Glyph, tint } = connectorBrand(connector);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-[11px] border border-border bg-elevated"
      style={{ width: size, height: size, color: tint }}
    >
      <Glyph width={size * 0.52} height={size * 0.52} />
    </span>
  );
}

type Filter = "all" | "healthy" | "attention";

export default function Connectors({ canManage }: { canManage: boolean }) {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [specs, setSpecs] = useState<ConnectorSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Instance | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const { push, ToastStack } = useToasts();

  const titleOf = useMemo(() => {
    const map = new Map(specs.map((sp) => [sp.name, sp.title]));
    return (name: string): string => map.get(name) ?? name;
  }, [specs]);

  async function refresh() {
    const [inst, sp] = await Promise.all([listInstances(), connectorSpecs()]);
    setInstances(inst);
    setSpecs(sp);
    setLoadError(null);
    setLoading(false);
  }

  useEffect(() => {
    refresh().catch((e) => {
      setLoadError(String(e));
      setLoading(false);
    });
    // Poll the instance list so a running sync updates its status without a reload.
    const t = setInterval(
      () => listInstances().then(setInstances).catch(() => {}),
      6000,
    );
    return () => clearInterval(t);
  }, []);

  const counts = useMemo(() => {
    let healthy = 0;
    let attention = 0;
    for (const i of instances) {
      const k = healthKind(i);
      if (k === "ok" || k === "run") healthy += 1;
      else attention += 1;
    }
    return { all: instances.length, healthy, attention };
  }, [instances]);

  const shown = useMemo(() => {
    if (filter === "all") return instances;
    return instances.filter((i) => {
      const k = healthKind(i);
      const ok = k === "ok" || k === "run";
      return filter === "healthy" ? ok : !ok;
    });
  }, [instances, filter]);

  async function sync(id: string) {
    setSyncing(id);
    try {
      const res = await syncInstance(id);
      await refresh();
      if (expanded === id) setRuns(await instanceRuns(id));
      if (res.status === "success" || res.status === "synced") {
        push(`Synced. ${res.docs_indexed} documents indexed.`, "ok");
      } else if (res.error) {
        push(`Sync failed: ${res.error}`, "err");
      } else {
        push("Sync started.", "info");
      }
    } catch (e) {
      push(`Sync failed: ${String(e)}`, "err");
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
    setRunsLoading(true);
    try {
      setRuns(await instanceRuns(id));
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }

  async function remove(inst: Instance) {
    if (
      !window.confirm(
        `Delete connector "${inst.name}"? Indexed documents stay until re-synced.`,
      )
    ) {
      return;
    }
    try {
      await deleteInstance(inst.id);
      await refresh();
      push(`Removed "${inst.name}".`, "ok");
    } catch (e) {
      push(`Delete failed: ${String(e)}`, "err");
    }
  }

  const filterTabs: Array<{ value: Filter; label: string; count: number }> = [
    { value: "all", label: "All", count: counts.all },
    { value: "healthy", label: "Healthy", count: counts.healthy },
    { value: "attention", label: "Attention", count: counts.attention },
  ];

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Connectors"
        breadcrumb={["lens", "connectors"]}
        subtitle="Sources feeding pansoph. Documents inherit source ACLs and are enforced at retrieval."
        action={
          canManage ? (
            <Button size="lg" onClick={() => setAdding(true)}>
              <IconPlus width={15} height={15} />
              Add connector
            </Button>
          ) : undefined
        }
      />

      {/* toolbar: segmented health filter + live count */}
      {!loading && !loadError && instances.length > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <div
            className="inline-flex gap-0.5 rounded-[var(--radius-md)] border border-border bg-surface p-0.5"
            role="tablist"
            aria-label="Filter connectors by health"
          >
            {filterTabs.map((t) => {
              const on = t.value === filter;
              return (
                <button
                  key={t.value}
                  role="tab"
                  aria-selected={on}
                  onClick={() => setFilter(t.value)}
                  className={
                    "rounded-[7px] px-3 py-1.5 text-xs font-medium transition " +
                    (on
                      ? "bg-elevated text-text shadow-[inset_0_0_0_1px_var(--color-border)]"
                      : "text-muted hover:text-text")
                  }
                >
                  {t.value !== "all" && (
                    <StatusDot
                      kind={t.value === "healthy" ? "ok" : "warn"}
                      size={6}
                      className="mr-1.5 inline-block align-middle"
                    />
                  )}
                  {t.label}
                  <span className="ml-1.5 tabular-nums opacity-60">{t.count}</span>
                </button>
              );
            })}
          </div>
          <div className="flex-1" />
          <span className="hidden items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted sm:inline-flex">
            <IconShield width={13} height={13} className="text-accent" />
            ACLs enforced at retrieval
          </span>
        </div>
      )}

      {loading ? (
        <ConnectorSkeletons />
      ) : loadError ? (
        <Alert kind="err" title="Could not load connectors">
          {loadError}. The connectors service may be unreachable.
        </Alert>
      ) : instances.length === 0 ? (
        <EmptyState
          icon={<IconGrid width={30} height={30} />}
          title="No connectors yet"
          description="Connect a source to start feeding pansoph. Every instance carries its own credentials, schedule, scope, and ACL namespace."
          action={
            canManage ? (
              <Button onClick={() => setAdding(true)}>
                <IconPlus width={15} height={15} />
                Add your first connector
              </Button>
            ) : undefined
          }
        />
      ) : shown.length === 0 ? (
        <EmptyState
          title={filter === "healthy" ? "No healthy connectors" : "Nothing needs attention"}
          description={
            filter === "healthy"
              ? "None of your connectors reported a successful run yet."
              : "Every connector is healthy. Nothing is failing or idle."
          }
          action={
            <Button variant="ghost" onClick={() => setFilter("all")}>
              Show all
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {shown.map((inst) => (
            <ConnectorCard
              key={inst.id}
              inst={inst}
              title={titleOf(inst.connector)}
              canManage={canManage}
              syncing={syncing === inst.id}
              expanded={expanded === inst.id}
              runs={expanded === inst.id ? runs : []}
              runsLoading={expanded === inst.id && runsLoading}
              onSync={() => sync(inst.id)}
              onToggleRuns={() => toggleRuns(inst.id)}
              onEdit={() => setEditing(inst)}
              onDelete={() => remove(inst)}
            />
          ))}
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
          onSaved={(name) => {
            setAdding(false);
            setEditing(null);
            refresh();
            push(editing ? `Saved "${name}".` : `Added "${name}".`, "ok");
          }}
        />
      )}

      <ToastStack />
    </div>
  );
}

// Loading placeholders sized like the real cards, so the layout does not jump.
function ConnectorSkeletons() {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <Card key={i} className="p-5">
          <div className="flex items-center gap-3">
            <div className="h-[42px] w-[42px] animate-pulse rounded-[11px] bg-elevated" />
            <div className="flex-1 space-y-2">
              <div className="h-3.5 w-32 animate-pulse rounded bg-elevated" />
              <div className="h-2.5 w-44 animate-pulse rounded bg-elevated" />
            </div>
          </div>
          <div className="my-5 flex gap-6 border-y border-border py-4">
            {[0, 1, 2].map((j) => (
              <div key={j} className="space-y-2">
                <div className="h-4 w-10 animate-pulse rounded bg-elevated" />
                <div className="h-2 w-14 animate-pulse rounded bg-elevated" />
              </div>
            ))}
          </div>
          <div className="h-8 w-full animate-pulse rounded bg-elevated" />
        </Card>
      ))}
    </div>
  );
}

// ------------------------------------------------------------
// ConnectorCard: one source, with health, stats, an ACL note, actions, and an
// expandable runs table. Stateless: the parent owns fetching and mutation.
// ------------------------------------------------------------
function ConnectorCard({
  inst,
  title,
  canManage,
  syncing,
  expanded,
  runs,
  runsLoading,
  onSync,
  onToggleRuns,
  onEdit,
  onDelete,
}: {
  inst: Instance;
  title: string;
  canManage: boolean;
  syncing: boolean;
  expanded: boolean;
  runs: RunInfo[];
  runsLoading: boolean;
  onSync: () => void;
  onToggleRuns: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const health = healthKind(inst);
  const last = inst.last_run;
  const errored = health === "err";
  const docs = last?.docs_indexed ?? 0;

  const scopeText =
    inst.scope_type === "org"
      ? "org"
      : `${inst.scope_type}${inst.scope_ref ? `: ${inst.scope_ref}` : ""}`;

  return (
    <Card
      glow
      className="group/card flex flex-col overflow-hidden p-5 transition hover:-translate-y-0.5"
    >
      {/* head */}
      <div className="flex items-start gap-3.5">
        <Tooltip label={`${title} connector`}>
          <ConnectorLogo connector={inst.connector} />
        </Tooltip>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="truncate text-[15px] font-semibold tracking-[-0.02em] text-text">
              {inst.name}
            </span>
            <Badge kind={health === "ok" ? "ok" : health === "run" ? "cyan" : health === "err" ? "err" : "warn"} dot>
              {healthLabel(inst)}
            </Badge>
          </div>
          <div className="mt-1 truncate font-mono text-[11.5px] text-muted">
            {title}
            <span className="opacity-40"> . </span>
            {scopeText}
          </div>
        </div>
        <Tooltip label={inst.id}>
          <span className="hidden shrink-0 rounded-[5px] border border-border bg-bg px-1.5 py-0.5 font-mono text-[10px] text-muted sm:inline-block">
            {inst.id.length > 14 ? `${inst.id.slice(0, 14)}...` : inst.id}
          </span>
        </Tooltip>
      </div>

      {/* stats */}
      <div className="my-5 flex gap-6 border-y border-border py-4">
        <Stat value={fmtCount(docs)} label="documents" />
        <Stat
          value={last ? relativeTime(last.started_at).replace(" ago", "") : "never"}
          label="synced ago"
          tone="cyan"
        />
        <Stat value={fmtCount(chunkEstimate(inst))} label="chunks" mono />
        <div className="ml-auto text-right">
          <div className="flex items-center justify-end gap-1.5 font-mono text-[13px] text-muted">
            <IconClock width={13} height={13} />
            {instanceSchedule(inst)}
          </div>
          <div className="mt-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted">
            schedule
          </div>
        </div>
      </div>

      {/* ACL / error note */}
      {errored && last?.error ? (
        <div className="mb-4 flex items-start gap-2 rounded-[var(--radius-sm)] border border-[rgba(244,63,94,0.3)] bg-err-soft px-3 py-2 text-[11.5px] text-err">
          <IconAlert width={14} height={14} className="mt-px shrink-0" />
          <span className="min-w-0">
            {last.error.length > 180 ? `${last.error.slice(0, 180)}...` : last.error}
          </span>
        </div>
      ) : (
        <div className="mb-4 inline-flex items-center gap-2 self-start rounded-[var(--radius-sm)] border border-border bg-elevated px-2.5 py-1.5 text-[11.5px] text-muted">
          <IconLock width={13} height={13} className="text-accent" />
          Source ACLs mapped to the{" "}
          <b className="mx-0.5 font-semibold text-text">{scopeText}</b> namespace
        </div>
      )}

      {/* actions */}
      <div className="mt-auto flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleRuns}
          aria-expanded={expanded}
        >
          <IconRuns width={14} height={14} />
          Runs
        </Button>
        {canManage && (
          <Button variant="ghost" size="sm" onClick={onEdit}>
            <IconEdit width={14} height={14} />
            Edit
          </Button>
        )}
        <div className="flex-1" />
        {canManage && (
          <>
            <Tooltip label="Delete connector">
              <Button
                variant="subtle"
                size="sm"
                onClick={onDelete}
                aria-label={`Delete ${inst.name}`}
                className="hover:text-err"
              >
                <IconTrash width={15} height={15} />
              </Button>
            </Tooltip>
            <Button size="sm" onClick={onSync} disabled={syncing}>
              {syncing ? (
                <>
                  <Spinner className="border-white/40 border-t-white" />
                  Syncing
                </>
              ) : (
                <>
                  <IconSync width={14} height={14} />
                  {errored ? "Retry sync" : "Sync now"}
                </>
              )}
            </Button>
          </>
        )}
      </div>

      {/* expandable runs */}
      {expanded && (
        <div className="animate-fade mt-4 overflow-hidden rounded-[var(--radius-md)] border border-border bg-bg">
          <div className="flex items-center gap-2 border-b border-border bg-surface px-3.5 py-2.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
            <IconClock width={13} height={13} />
            Recent runs
          </div>
          {runsLoading ? (
            <div className="flex justify-center py-6">
              <Spinner />
            </div>
          ) : runs.length === 0 ? (
            <div className="px-3.5 py-5 text-center text-xs text-muted">
              No runs recorded yet. Trigger a sync to see history here.
            </div>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Started</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Docs</TH>
                  <TH className="text-right">Duration</TH>
                  <TH>Detail</TH>
                </TR>
              </THead>
              <TBody>
                {runs.map((r, i) => (
                  <TR key={i} className="transition hover:bg-surface">
                    <TD className="whitespace-nowrap font-mono text-[12px] text-muted">
                      {relativeTime(r.started_at)}
                    </TD>
                    <TD>
                      <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold">
                        <StatusDot kind={runKind(r.status)} size={6} pulse={r.status === "running"} />
                        <span
                          className={
                            r.status === "success"
                              ? "text-ok"
                              : r.status === "running"
                                ? "text-cyan"
                                : "text-err"
                          }
                        >
                          {r.status}
                        </span>
                      </span>
                    </TD>
                    <TD className="text-right font-mono text-[12px] tabular-nums text-text">
                      {r.docs_indexed}
                    </TD>
                    <TD className="text-right font-mono text-[12px] tabular-nums text-muted">
                      {fmtDuration(r.duration_seconds)}
                    </TD>
                    <TD className="max-w-[16rem] truncate text-[12px] text-err">
                      {r.error ?? ""}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </div>
      )}
    </Card>
  );
}

// One stat cell: a large tabular value over an uppercase muted label.
function Stat({
  value,
  label,
  mono = false,
  tone,
}: {
  value: string;
  label: string;
  mono?: boolean;
  tone?: "cyan";
}) {
  return (
    <div>
      <div
        className={
          "text-[18px] font-bold leading-none tracking-[-0.03em] tabular-nums " +
          (tone === "cyan" ? "text-cyan " : "text-text ") +
          (mono ? "font-mono text-[14px]" : "")
        }
      >
        {value}
      </div>
      <div className="mt-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </div>
    </div>
  );
}

// ------------------------------------------------------------
// ConnectorForm: the Add / Edit modal. Renders the connector's declared spec fields,
// scope, and schedule. When a connector declares an oauth field, the raw token input is
// replaced by a prominent "Connect with <Provider>" button that navigates to the
// authorize endpoint; non-secret fields (like the instance name) still render.
// ------------------------------------------------------------
function ConnectorForm({
  specs,
  editing,
  onClose,
  onSaved,
}: {
  specs: ConnectorSpec[];
  editing: Instance | null;
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const [connector, setConnector] = useState(editing?.connector ?? specs[0]?.name ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const def = useMemo<ConnectorSpec | undefined>(
    () => specs.find((s) => s.name === connector),
    [specs, connector],
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    editing
      ? configToValues(specs.find((s) => s.name === editing.connector), editing.config)
      : {},
  );
  const [checks, setChecks] = useState<Record<string, boolean>>(() =>
    editing
      ? configToChecks(specs.find((s) => s.name === editing.connector), editing.config)
      : {},
  );
  const [schedule, setSchedule] = useState(editing?.schedule_seconds ?? 0);
  const [cronMode, setCronMode] = useState(!!editing?.schedule_cron);
  const [cron, setCron] = useState(editing?.schedule_cron ?? "");
  const [scopeType, setScopeType] = useState(editing?.scope_type ?? "org");
  const [scopeRef, setScopeRef] = useState(editing?.scope_ref ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The oauth field (if any) drives whether we show the Connect button instead of a token
  // input. There is at most one per connector spec.
  const oauthField = useMemo<SpecField | undefined>(
    () => def?.fields.find((f) => f.kind === "oauth"),
    [def],
  );
  const provider = oauthField
    ? oauthProvider(oauthProviderOf(oauthField) ?? def?.name)
    : null;

  function changeType(t: string) {
    setConnector(t);
    setValues({});
    setChecks({});
    setError(null);
  }

  function buildConfig(): Record<string, unknown> {
    const config: Record<string, unknown> = {};
    for (const f of def?.fields ?? []) {
      if (f.kind === "oauth") continue;
      if (isBoolField(f.kind)) {
        if (checks[f.key]) config[f.key] = true;
        continue;
      }
      const raw = (values[f.key] ?? "").trim();
      if (f.kind === "int") {
        if (raw) config[f.key] = Number(raw);
      } else if (f.kind === "list") {
        const arr = raw
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean);
        if (arr.length) config[f.key] = arr;
      } else if (raw) {
        config[f.key] = raw;
      }
    }
    return config;
  }

  // The instance id the OAuth authorize URL points at. On edit it is the existing id; on
  // add it is derived from the display name the same way submit() derives it, so the
  // token the callback stores lands on the instance the form is about to create.
  const finalName = name.trim() || def?.title || connector;
  const instanceId =
    editing?.id ??
    finalName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "");
  const tenant = editing?.tenant_id ?? "default";

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await createInstance({
        connector,
        name: finalName,
        instance_id: instanceId,
        tenant_id: tenant,
        config: buildConfig(),
        schedule_seconds: cronMode ? 0 : schedule,
        schedule_cron: cronMode ? cron.trim() : "",
        scope_type: scopeType,
        scope_ref: scopeType === "org" ? "" : scopeRef.trim(),
      });
      onSaved(finalName);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const visibleFields = (def?.fields ?? []).filter((f) => f.kind !== "oauth");

  return (
    <Modal
      open
      onClose={onClose}
      title={editing ? "Edit connector" : "Add connector"}
      subtitle={
        def?.description ??
        "Connect a source. Its documents inherit the source ACLs, enforced at retrieval."
      }
      icon={
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[11px] bg-[linear-gradient(140deg,var(--ob-accent),#5b3ce0)] shadow-[0_0_0_1px_var(--ob-accent-line)]">
          <IconGrid width={20} height={20} className="text-white" />
        </span>
      }
      footer={
        <>
          <span className="flex items-center gap-1.5 text-[11px] text-muted">
            <IconShield width={13} height={13} className="text-ok" />
            Credentials encrypted at rest, never logged
          </span>
          <div className="flex-1" />
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? (
              <>
                <Spinner className="border-white/40 border-t-white" />
                {editing ? "Saving" : "Adding"}
              </>
            ) : editing ? (
              "Save changes"
            ) : (
              "Add connector"
            )}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* Type */}
        <Field label="Type" required>
          <div className="flex items-center gap-2.5">
            <ConnectorLogo connector={connector} size={38} />
            <div className="flex-1">
              <Select
                value={connector}
                onChange={(e) => changeType(e.target.value)}
                disabled={!!editing}
                aria-label="Connector type"
              >
                {specs.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.title}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </Field>

        {/* Name */}
        <Field label="Display name" required help="Shown in the connectors list.">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={`e.g. ${def?.title ?? connector}`}
          />
        </Field>

        {/* Dynamic spec fields (never the oauth field) */}
        {visibleFields.map((f) => (
          <SpecFieldInput
            key={f.key}
            field={f}
            values={values}
            checks={checks}
            setValues={setValues}
            setChecks={setChecks}
          />
        ))}

        {/* Scope */}
        <Field
          label="Scope"
          help="Which slice this instance covers. Management metadata; the source ACL still governs who can read its documents."
        >
          <Select value={scopeType} onChange={(e) => setScopeType(e.target.value)}>
            <option value="org">Organization (whole source)</option>
            <option value="group">Group</option>
            <option value="individual">Individual</option>
          </Select>
          {scopeType !== "org" && (
            <Input
              className="mt-2"
              value={scopeRef}
              onChange={(e) => setScopeRef(e.target.value)}
              placeholder={scopeType === "group" ? "group id, e.g. g1" : "user id, e.g. alice"}
            />
          )}
        </Field>

        {/* Schedule */}
        <Field label="Schedule">
          <Select
            value={cronMode ? "cron" : String(schedule)}
            onChange={(e) => {
              if (e.target.value === "cron") {
                setCronMode(true);
              } else {
                setCronMode(false);
                setSchedule(parseInt(e.target.value, 10));
              }
            }}
          >
            {SCHEDULES.map(([label, v]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
            <option value="cron">Custom (cron)...</option>
          </Select>
          {cronMode && (
            <>
              <Input
                className="mt-2 font-mono text-xs"
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                placeholder="0 */6 * * *"
              />
              <span className="mt-1 block text-xs text-muted">
                Standard 5-field cron (min hour day month weekday), evaluated in UTC.
              </span>
            </>
          )}
        </Field>

        {/* OAuth Connect (when the connector declares an oauth field) */}
        {oauthField && (
          <OAuthConnect
            provider={provider}
            connector={connector}
            instanceId={instanceId}
            tenant={tenant}
            editing={!!editing}
          />
        )}

        {error && (
          <Alert kind="err" title="Could not save connector">
            {error}
          </Alert>
        )}
      </div>
    </Modal>
  );
}

// The OAuth block: a divider, a brand-colored "Connect with X" button that navigates to
// the authorize endpoint, and a note about what pansoph stores. Falls back gracefully if
// the provider is unknown (still offers a generic connect that hits the authorize URL).
function OAuthConnect({
  provider,
  connector,
  instanceId,
  tenant,
  editing,
}: {
  provider: ReturnType<typeof oauthProvider>;
  connector: string;
  instanceId: string;
  tenant: string;
  editing: boolean;
}) {
  // The authorize endpoint is keyed by the OAuth provider (google, slack, atlassian,
  // github). Fall back to the connector name when the provider is not one we recognize.
  const authorizeSlug = provider?.key ?? connector;
  const href =
    `/api/oauth/${encodeURIComponent(authorizeSlug)}/authorize` +
    `?connector=${encodeURIComponent(connector)}` +
    `&instance=${encodeURIComponent(instanceId)}` +
    `&tenant=${encodeURIComponent(tenant)}`;
  const label = provider?.label ?? "provider";
  const Glyph = provider?.glyph;

  return (
    <div>
      <div className="my-1 flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">
        <span className="h-px flex-1 bg-border" />
        authenticate
        <span className="h-px flex-1 bg-border" />
      </div>
      <a
        href={href}
        className="flex w-full items-center justify-center gap-3 rounded-[var(--radius-md)] border px-4 py-3 text-sm font-semibold shadow-[0_6px_20px_-8px_rgba(0,0,0,0.5)] transition hover:brightness-105 focus-visible:outline-none active:translate-y-px"
        style={{
          background: provider?.bg ?? "var(--ob-elevated)",
          color: provider?.fg ?? "var(--ob-text)",
          borderColor: provider?.border ?? "var(--ob-border)",
        }}
      >
        {Glyph ? (
          <Glyph width={18} height={18} />
        ) : (
          <IconLock width={16} height={16} />
        )}
        Connect with {label}
      </a>
      <p className="mt-3 text-center text-[11.5px] text-muted">
        Redirects to {label}. pansoph stores a scoped refresh token only
        {editing ? "" : ", once you add the connector"}.
      </p>
    </div>
  );
}

// One spec-driven form control, chosen from the field's declared kind. String is text,
// int is number, bool is a styled checkbox row, list is a newline textarea, and
// secret / secret_file render a masked input with a reveal toggle.
function SpecFieldInput({
  field,
  values,
  checks,
  setValues,
  setChecks,
}: {
  field: SpecField;
  values: Record<string, string>;
  checks: Record<string, boolean>;
  setValues: (v: Record<string, string>) => void;
  setChecks: (v: Record<string, boolean>) => void;
}) {
  const { key, label, kind, required, help, placeholder } = field;

  if (isBoolField(kind)) {
    const on = !!checks[key];
    return (
      <button
        type="button"
        onClick={() => setChecks({ ...checks, [key]: !on })}
        className="flex w-full items-start gap-3 rounded-[var(--radius-md)] border border-border bg-bg px-3.5 py-3 text-left transition hover:border-border-hover"
      >
        <span
          className={
            "mt-px grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[5px] border transition " +
            (on ? "border-accent bg-accent" : "border-border bg-surface")
          }
        >
          <svg
            viewBox="0 0 24 24"
            className={"h-3 w-3 text-white transition-opacity " + (on ? "opacity-100" : "opacity-0")}
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </span>
        <span className="min-w-0">
          <span className="block text-[13px] font-medium text-text">{label}</span>
          {help && <span className="mt-0.5 block text-[11.5px] text-muted">{help}</span>}
        </span>
      </button>
    );
  }

  if (kind === "list") {
    return (
      <Field label={label} required={required} help={help}>
        <Textarea
          className="font-mono text-xs"
          rows={3}
          value={values[key] ?? ""}
          onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          placeholder={placeholder}
        />
      </Field>
    );
  }

  if (isSecretField(kind)) {
    return (
      <SecretInput
        field={field}
        value={values[key] ?? ""}
        onChange={(v) => setValues({ ...values, [key]: v })}
      />
    );
  }

  return (
    <Field label={label} required={required} help={help}>
      <Input
        type={kind === "int" ? "number" : "text"}
        value={values[key] ?? ""}
        onChange={(e) => setValues({ ...values, [key]: e.target.value })}
        placeholder={placeholder}
      />
    </Field>
  );
}

// A masked secret input with a lock affordance and a reveal toggle.
function SecretInput({
  field,
  value,
  onChange,
}: {
  field: SpecField;
  value: string;
  onChange: (v: string) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  return (
    <Field
      label={field.label}
      required={field.required}
      help={field.help ?? "Stored encrypted, never logged."}
    >
      <div className="relative">
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted">
          <IconLock width={15} height={15} />
        </span>
        <Input
          type={revealed ? "text" : "password"}
          autoComplete="off"
          className="pl-9 pr-11 font-mono tracking-wide"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
        />
        <button
          type="button"
          onClick={() => setRevealed((r) => !r)}
          aria-label={revealed ? "Hide secret" : "Reveal secret"}
          className="absolute right-1.5 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-[7px] text-muted transition hover:bg-surface hover:text-text"
        >
          {revealed ? <IconEyeOff width={15} height={15} /> : <IconEye width={15} height={15} />}
        </button>
      </div>
    </Field>
  );
}
