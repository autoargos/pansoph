import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  type ConnectorSpec,
  connectorSpecs,
  createInstance,
  type CredentialStatus,
  deleteInstance,
  type IndexedDocument,
  type Instance,
  instanceCredential,
  instanceDocuments,
  instanceRuns,
  listInstances,
  oauthAuthorizeUrl,
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
import { ConnectorLogo } from "../components/ConnectorLogo.tsx";
import { ResourcePicker } from "../components/ResourcePicker.tsx";
import {
  IconAlert,
  IconCheck,
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

// A field bound to a Resource picker. When the field declares a resource_kind it is filled
// by picking discovered resources (org -> repo, drive -> folder) instead of typing. A list
// kind means many resources may be chosen; any other kind is a single pick.
function isResourceField(field: SpecField): boolean {
  return !!field.resource_kind;
}
function isMultiResource(field: SpecField): boolean {
  return field.kind === "list";
}

// Read the resource-field selections out of an existing config into the picker's shape
// (field key -> selected ids). A list field stores an array; a single field stores a scalar.
function configToResources(
  spec: ConnectorSpec | undefined,
  config: Record<string, unknown>,
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const f of spec?.fields ?? []) {
    if (!isResourceField(f)) continue;
    const v = config[f.key];
    if (Array.isArray(v)) out[f.key] = v.map(String);
    else if (v != null && v !== "") out[f.key] = [String(v)];
    else out[f.key] = [];
  }
  return out;
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
    // Resource-bound fields are filled by the picker, not the free-text map.
    if (isResourceField(f)) continue;
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

type Filter = "all" | "healthy" | "attention";

export default function Connectors({ canManage }: { canManage: boolean }) {
  const [params, setParams] = useSearchParams();
  const [instances, setInstances] = useState<Instance[]>([]);
  const [specs, setSpecs] = useState<ConnectorSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Instance | null>(null);
  // The draft instance the OAuth callback landed on (via ?configure={id}). Its credential is
  // already sealed; the form opens in configure mode so the user can pick resources and
  // promote the draft to active. Null when not configuring.
  const [configuring, setConfiguring] = useState<Instance | null>(null);
  // The instance whose indexed documents are being browsed, or null.
  const [viewingDocs, setViewingDocs] = useState<Instance | null>(null);
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

  // Query-param intents. ?add=1 opens a fresh Add form (used by the command palette).
  // ?configure={id} is where the OAuth callback lands: open the matching draft instance in
  // configure mode. We wait for the instance list to load so we can hand the form the real
  // draft (its connector, name, and any partial config). The param is cleared once handled.
  const addParam = params.get("add");
  const configureParam = params.get("configure");
  useEffect(() => {
    if (!addParam) return;
    setAdding(true);
    setEditing(null);
    setConfiguring(null);
    const next = new URLSearchParams(params);
    next.delete("add");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addParam]);

  useEffect(() => {
    if (!configureParam || loading) return;
    const draft = instances.find((i) => i.id === configureParam);
    if (!draft) {
      // The instance list may not carry it yet on a cold callback; a refresh will retry.
      return;
    }
    setConfiguring(draft);
    setAdding(false);
    setEditing(null);
    const next = new URLSearchParams(params);
    next.delete("configure");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configureParam, loading, instances]);

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
              onViewDocs={() => setViewingDocs(inst)}
            />
          ))}
        </div>
      )}

      {viewingDocs && (
        <DocumentsPanel inst={viewingDocs} onClose={() => setViewingDocs(null)} />
      )}

      {(adding || editing || configuring) && (
        <ConnectorForm
          specs={specs}
          editing={editing ?? configuring}
          configuring={!!configuring}
          onClose={() => {
            setAdding(false);
            setEditing(null);
            setConfiguring(null);
          }}
          onSaved={(name) => {
            const wasConfigure = !!configuring;
            const wasEdit = !!editing;
            setAdding(false);
            setEditing(null);
            setConfiguring(null);
            refresh();
            push(
              wasConfigure
                ? `Connected "${name}".`
                : wasEdit
                  ? `Saved "${name}".`
                  : `Added "${name}".`,
              "ok",
            );
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
// DocumentsPanel: browse what an instance actually indexed. Answers the "where are these
// documents" question by listing each source document with its id and a content snippet,
// so an operator can confirm a connector pulled real, meaningful content.
function DocumentsPanel({ inst, onClose }: { inst: Instance; onClose: () => void }) {
  const [docs, setDocs] = useState<IndexedDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    instanceDocuments(inst.id)
      .then((r) => {
        if (live) setDocs(r.documents);
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [inst.id]);

  return (
    <Modal
      open
      onClose={onClose}
      title={`${inst.name}: indexed documents`}
      subtitle="What this connector has actually pulled into the index. Retrieval still enforces each document's access control."
    >
      <div className="max-h-[60vh] overflow-y-auto">
        {error ? (
          <div className="px-2 py-8 text-center text-[13px] text-err">{error}</div>
        ) : docs === null ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : docs.length === 0 ? (
          <div className="px-2 py-10 text-center text-[13px] text-muted">
            Nothing indexed yet. Run a sync, then check back.
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {docs.map((d) => (
              <li key={d.doc_id} className="py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-text">
                    {d.source_id}
                  </span>
                  <Badge kind="neutral">{d.chunks} chunks</Badge>
                </div>
                {d.snippet && (
                  <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-muted">
                    {d.snippet}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {docs && docs.length > 0 && (
        <p className="mt-3 text-[11.5px] text-muted">
          {docs.length} document{docs.length === 1 ? "" : "s"} indexed.
        </p>
      )}
    </Modal>
  );
}

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
  onViewDocs,
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
  onViewDocs: () => void;
}) {
  const health = healthKind(inst);
  const last = inst.last_run;
  const errored = health === "err";
  // Show the total documents in the index, not the last run's delta (an incremental sync
  // with no changes indexes 0, which made a populated connector look empty). Fall back to
  // the last run count for a store that does not report totals.
  const docs = inst.indexed_documents ?? last?.docs_indexed ?? 0;

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
        {docs > 0 ? (
          <button
            type="button"
            onClick={onViewDocs}
            className="text-left transition hover:opacity-70"
            title="See the indexed documents"
          >
            <Stat value={fmtCount(docs)} label="documents" />
          </button>
        ) : (
          <Stat value={fmtCount(docs)} label="documents" />
        )}
        <Stat
          value={last ? relativeTime(last.started_at).replace(" ago", "") : "never"}
          label="synced ago"
          tone="cyan"
        />
        <Stat value={fmtCount(inst.indexed_chunks ?? chunkEstimate(inst))} label="chunks" mono />
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
  configuring = false,
  onClose,
  onSaved,
}: {
  specs: ConnectorSpec[];
  editing: Instance | null;
  // Configure mode: the (draft) instance already has a sealed credential (the OAuth callback
  // landed here). We show the live credential status, offer resource pickers, and submit
  // promotes the draft to active. Distinct from a plain edit of an already-active instance.
  configuring?: boolean;
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
  // Resource-picker selections, keyed by field key. Filled from an existing config on edit /
  // configure, then written back into the config on submit.
  const [resources, setResources] = useState<Record<string, string[]>>(() =>
    editing
      ? configToResources(specs.find((s) => s.name === editing.connector), editing.config)
      : {},
  );
  const [schedule, setSchedule] = useState(editing?.schedule_seconds ?? 0);
  const [cronMode, setCronMode] = useState(!!editing?.schedule_cron);
  const [cron, setCron] = useState(editing?.schedule_cron ?? "");
  const [scopeType, setScopeType] = useState(editing?.scope_type ?? "org");
  const [scopeRef, setScopeRef] = useState(editing?.scope_ref ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Live credential status for an existing instance (edit or configure). Drives whether the
  // prominent Connect button shows (no valid credential) or a subordinate Reconnect link
  // plus the resource pickers (already connected). Null until loaded; only fetched for a
  // persisted instance, since a brand-new Add has no instance to query yet.
  const [cred, setCred] = useState<CredentialStatus | null>(null);
  const [credLoading, setCredLoading] = useState(false);
  useEffect(() => {
    if (!editing) return;
    setCredLoading(true);
    instanceCredential(editing.id)
      .then(setCred)
      .catch(() => setCred(null))
      .finally(() => setCredLoading(false));
  }, [editing]);

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
    setResources({});
    setError(null);
  }

  function buildConfig(): Record<string, unknown> {
    const config: Record<string, unknown> = {};
    for (const f of def?.fields ?? []) {
      if (f.kind === "oauth") continue;
      // A resource-bound field is filled by the picker, not the text/checkbox maps. A multi
      // (list) field stores the array of ids; a single field stores the one id as a scalar.
      if (isResourceField(f)) {
        const ids = resources[f.key] ?? [];
        if (isMultiResource(f)) {
          if (ids.length) config[f.key] = ids;
        } else if (ids.length) {
          config[f.key] = ids[0];
        }
        continue;
      }
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
        // Configure submit promotes a draft (from the OAuth callback) to active. A plain
        // add / edit also persists 'active'; the backend defaults to it, we send it for
        // clarity and so a configure of a draft flips the status.
        status: "active",
      });
      onSaved(finalName);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const visibleFields = (def?.fields ?? []).filter((f) => f.kind !== "oauth");

  // Whether the instance already holds a usable credential. Only a persisted instance can be
  // connected (a fresh Add has none yet). When connected we suppress the prominent Connect
  // button and enable the resource pickers; when not, we keep offering Connect. While the
  // credential status is still loading we hold off on the pickers to avoid a flash.
  const connected = !!editing && !!cred?.connected;
  const needsReauth = !!editing && (cred?.needs_reauth ?? false);
  // Resource pickers need a live instance to query. Available in configure or edit once the
  // credential is confirmed present.
  const pickersReady = connected;

  const modalTitle = configuring
    ? "Finish connecting"
    : editing
      ? "Edit connector"
      : "Add connector";

  return (
    <Modal
      open
      onClose={onClose}
      title={modalTitle}
      subtitle={
        configuring
          ? "Your credential is connected. Choose what to sync, then finish."
          : (def?.description ??
            "Connect a source. Its documents inherit the source ACLs, enforced at retrieval.")
      }
      icon={
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[11px] bg-[linear-gradient(140deg,var(--ob-accent),#5b3ce0)] shadow-[0_0_0_1px_var(--ob-accent-line)]">
          <IconGrid width={20} height={20} className="text-white" />
        </span>
      }
      footer={
        <>
          <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted">
            <IconShield width={13} height={13} className="shrink-0 text-ok" />
            <span className="truncate">Credentials encrypted at rest, never logged</span>
          </span>
          <div className="flex-1" />
          <Button variant="ghost" className="shrink-0 whitespace-nowrap" onClick={onClose}>
            Cancel
          </Button>
          <Button className="shrink-0 whitespace-nowrap" onClick={submit} disabled={busy}>
            {busy ? (
              <>
                <Spinner className="border-white/40 border-t-white" />
                {configuring ? "Finishing" : editing ? "Saving" : "Adding"}
              </>
            ) : configuring ? (
              "Finish and activate"
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

        {/* Credential state for an OAuth connector. When a credential is already sealed
            (edit or the configure step after the callback), show a subordinate "Connected"
            banner with a Reconnect link, not another prominent Connect prompt. Otherwise the
            prominent "Connect with X" block is the primary, recommended path. */}
        {oauthField &&
          (connected ? (
            <CredentialBanner
              cred={cred}
              provider={provider}
              connector={connector}
              instanceId={instanceId}
              tenant={tenant}
              needsReauth={needsReauth}
            />
          ) : credLoading ? (
            <div className="flex items-center gap-2 rounded-[var(--radius-md)] border border-border bg-bg px-4 py-3 text-[12px] text-muted">
              <Spinner />
              Checking credential
            </div>
          ) : (
            <OAuthConnect
              provider={provider}
              connector={connector}
              instanceId={instanceId}
              tenant={tenant}
              editing={!!editing}
            />
          ))}

        {/* Dynamic spec fields (never the oauth field). For a non-oauth
            connector these are the primary inputs and render inline here. For
            an oauth connector they are the manual fallback and render lower,
            under a subtle divider, so keep them out of this spot. */}
        {!oauthField &&
          visibleFields.map((f) => (
            <SpecFieldInput
              key={f.key}
              field={f}
              values={values}
              checks={checks}
              resources={resources}
              instanceId={instanceId}
              pickersReady={pickersReady}
              setValues={setValues}
              setChecks={setChecks}
              setResources={setResources}
            />
          ))}

        {/* For an OAuth connector, once connected the resource pickers are the primary
            inputs (pick the repos / folders / drives to sync). Non-resource fields still
            render in the manual-fallback block below. */}
        {oauthField &&
          connected &&
          visibleFields
            .filter((f) => isResourceField(f))
            .map((f) => (
              <SpecFieldInput
                key={f.key}
                field={f}
                values={values}
                checks={checks}
                resources={resources}
                instanceId={instanceId}
                pickersReady={pickersReady}
                setValues={setValues}
                setChecks={setChecks}
                setResources={setResources}
              />
            ))}

        {/* Scope */}
        <Field
          label="Scope"
          help="Which slice this instance covers. An individual or group scope narrows the source ACL so only that user or group can retrieve these documents. Organization keeps the source ACL as is. Scope can only remove reach, never widen it."
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

        {/* Manual fallback for an oauth connector. When already connected the resource
            fields are pickers above, so this block holds only the remaining non-resource
            options (relabeled as "Additional options"). When not connected it is the full
            token / credential fallback, shown below a subtle divider at lower weight. */}
        {oauthField &&
          (() => {
            const manualFields = connected
              ? visibleFields.filter((f) => !isResourceField(f))
              : visibleFields;
            if (manualFields.length === 0) return null;
            return (
              <div className="space-y-4 rounded-[var(--radius-md)] border border-dashed border-border bg-bg/40 p-4">
                <div className="flex items-center gap-3 text-[11px] font-medium uppercase tracking-[0.09em] text-muted">
                  <span className="h-px flex-1 bg-border" />
                  {connected ? "Additional options" : "Or connect manually with a token"}
                  <span className="h-px flex-1 bg-border" />
                </div>
                {manualFields.map((f) => (
                  <SpecFieldInput
                    key={f.key}
                    field={f}
                    values={values}
                    checks={checks}
                    resources={resources}
                    instanceId={instanceId}
                    pickersReady={pickersReady}
                    setValues={setValues}
                    setChecks={setChecks}
                    setResources={setResources}
                  />
                ))}
              </div>
            );
          })()}

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
  const label = provider?.label ?? "provider";
  const Glyph = provider?.glyph;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // The authorize endpoint is bearer gated, so a plain link would 401. Fetch the consent
  // URL with the token, then navigate the browser to the provider. A 400 here means the
  // provider is not configured yet (its client id/secret env var is unset).
  async function connect() {
    setErr(null);
    setBusy(true);
    try {
      const url = await oauthAuthorizeUrl(authorizeSlug, connector, instanceId, tenant);
      window.location.href = url;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not start the connect flow");
      setBusy(false);
    }
  }

  return (
    <div className="rounded-[var(--radius-md)] border border-accent-line bg-accent-soft p-4 shadow-[0_10px_30px_-16px_rgba(124,92,255,0.7)]">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[12.5px] font-semibold text-text">
          Connect with {label}
        </span>
        <Badge kind="accent">Recommended</Badge>
      </div>
      <button
        type="button"
        onClick={connect}
        disabled={busy}
        className="flex w-full items-center justify-center gap-3 rounded-[var(--radius-md)] border px-4 py-3 text-sm font-semibold shadow-[0_6px_20px_-8px_rgba(0,0,0,0.5)] transition hover:brightness-105 focus-visible:outline-none active:translate-y-px disabled:opacity-60"
        style={{
          background: provider?.bg ?? "var(--ob-elevated)",
          color: provider?.fg ?? "var(--ob-text)",
          borderColor: provider?.border ?? "var(--ob-border)",
        }}
      >
        {busy ? (
          <Spinner className="border-white/40 border-t-white" />
        ) : Glyph ? (
          <Glyph width={18} height={18} />
        ) : (
          <IconLock width={16} height={16} />
        )}
        {busy ? "Connecting" : `Connect with ${label}`}
      </button>
      {err && <p className="mt-2 text-center text-[11.5px] text-err">{err}</p>}
      <p className="mt-3 text-center text-[11.5px] text-muted">
        Redirects to {label}. pansoph stores a scoped refresh token only
        {editing ? "" : ", once you add the connector"}.
      </p>
    </div>
  );
}

// The connected-state banner, shown when the instance already holds a usable credential.
// It replaces the prominent Connect prompt with a subordinate "Connected as X" line and a
// low-weight Reconnect link. When the credential needs re-auth (expired with no refresh) it
// switches to a warn tone and surfaces the Reconnect more prominently, but never re-shows a
// full Connect prompt while a credential exists.
function CredentialBanner({
  cred,
  provider,
  connector,
  instanceId,
  tenant,
  needsReauth,
}: {
  cred: CredentialStatus | null;
  provider: ReturnType<typeof oauthProvider>;
  connector: string;
  instanceId: string;
  tenant: string;
  needsReauth: boolean;
}) {
  const authorizeSlug = provider?.key ?? cred?.provider ?? connector;
  const label = provider?.label ?? cred?.provider ?? "provider";
  const account = cred?.account_label;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function reconnect() {
    setErr(null);
    setBusy(true);
    try {
      const url = await oauthAuthorizeUrl(authorizeSlug, connector, instanceId, tenant);
      window.location.href = url;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not start the reconnect flow");
      setBusy(false);
    }
  }

  const tone = needsReauth
    ? "border-warn-soft bg-warn-soft"
    : "border-[rgba(52,211,153,0.3)] bg-ok-soft";

  return (
    <div className={"rounded-[var(--radius-md)] border px-4 py-3 " + tone}>
      <div className="flex items-center gap-2.5">
        <span
          className={
            "grid h-7 w-7 shrink-0 place-items-center rounded-full " +
            (needsReauth ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok")
          }
        >
          {needsReauth ? <IconAlert width={15} height={15} /> : <IconCheck width={15} height={15} />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-text">
            {needsReauth ? "Reconnect needed" : "Connected"}
            <Badge kind={needsReauth ? "warn" : "ok"} dot>
              {label}
            </Badge>
          </div>
          <div className="mt-0.5 truncate text-[11.5px] text-muted">
            {account ? (
              <>
                Connected as <span className="font-medium text-text">{account}</span>
              </>
            ) : needsReauth ? (
              "The credential expired. Reconnect to keep syncing."
            ) : (
              "Credential sealed. Pick what to sync below."
            )}
            {cred?.has_refresh && !needsReauth && (
              <span className="opacity-70"> . refresh token stored</span>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={reconnect}
          disabled={busy}
          className={
            "shrink-0 rounded-[var(--radius-sm)] px-2.5 py-1 text-[11.5px] font-semibold transition disabled:opacity-60 " +
            (needsReauth
              ? "border border-[rgba(245,158,11,0.4)] bg-warn-soft text-warn hover:brightness-105"
              : "text-muted hover:text-text underline underline-offset-2")
          }
        >
          {busy ? "Reconnecting" : "Reconnect"}
        </button>
      </div>
      {err && <p className="mt-2 text-[11.5px] text-err">{err}</p>}
    </div>
  );
}

// One spec-driven form control, chosen from the field's declared kind. String is text,
// int is number, bool is a styled checkbox row, list is a newline textarea, and
// secret / secret_file render a masked input with a reveal toggle. A field that declares a
// resource_kind renders a ResourcePicker once the instance is connected (pickersReady),
// otherwise it falls back to the free-text control so air-gapped / PAT users still work.
function SpecFieldInput({
  field,
  values,
  checks,
  resources,
  instanceId,
  pickersReady,
  setValues,
  setChecks,
  setResources,
}: {
  field: SpecField;
  values: Record<string, string>;
  checks: Record<string, boolean>;
  resources: Record<string, string[]>;
  instanceId: string;
  pickersReady: boolean;
  setValues: (v: Record<string, string>) => void;
  setChecks: (v: Record<string, boolean>) => void;
  setResources: (v: Record<string, string[]>) => void;
}) {
  const { key, label, kind, required, help, placeholder } = field;

  // Resource-bound field: pick from the discovered tree once connected. Before then (a fresh
  // Add with no live credential) it degrades to the normal free-text control below.
  if (isResourceField(field) && field.resource_kind && pickersReady) {
    const multi = isMultiResource(field);
    return (
      <Field
        label={label}
        required={required}
        help={
          help ??
          (multi ? "Pick one or more to sync. Drill in to narrow the scope." : "Pick one to sync.")
        }
      >
        <ResourcePicker
          instanceId={instanceId}
          resourceKind={field.resource_kind}
          multi={multi}
          value={resources[key] ?? []}
          onChange={(ids) => setResources({ ...resources, [key]: ids })}
        />
      </Field>
    );
  }

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
