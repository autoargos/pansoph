import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  listAdapters,
  type Health,
  type Meta,
  systemHealth,
  systemMeta,
} from "../api.ts";
import {
  Badge,
  Button,
  Card,
  Chip,
  EmptyState,
  PageHeader,
  relativeTime,
  Spinner,
  StatusDot,
  type StatusKind,
  Tooltip,
} from "../ui.tsx";

// ------------------------------------------------------------
// System page. A live read of what pansoph has wired: which
// service ports answer, which adapter sits behind each role, the
// active model config, and where to find the run logs. Every role
// is a replaceable port, so this page reads the truth from the
// backend (health, meta, adapters) and labels anything the backend
// does not yet expose as a "spec" placeholder rather than faking it.
// ------------------------------------------------------------

const POLL_MS = 8000;

// The service ports we surface as health cards, in display order. The
// backend health check keys are matched case-insensitively against
// these aliases so a rename on either side degrades gracefully rather
// than dropping a card. `role` is the port this service backs.
interface ServiceDef {
  id: string;
  label: string;
  role: string;
  aliases: string[];
  icon: ReactNode;
}

const SERVICES: ServiceDef[] = [
  {
    id: "agora",
    label: "agora",
    role: "API core",
    aliases: ["agora", "api", "core", "self"],
    icon: <IconServer />,
  },
  {
    id: "postgres",
    label: "postgres",
    role: "Relational + vector",
    aliases: ["postgres", "postgresql", "pg", "db", "database"],
    icon: <IconDatabase />,
  },
  {
    id: "spicedb",
    label: "spicedb",
    role: "Authz",
    aliases: ["spicedb", "authz", "zed"],
    icon: <IconShield />,
  },
  {
    id: "valkey",
    label: "valkey",
    role: "Cache + queue",
    aliases: ["valkey", "redis", "cache", "queue"],
    icon: <IconBolt />,
  },
  {
    id: "object_store",
    label: "object store",
    role: "Blob storage",
    aliases: ["object_store", "objectstore", "s3", "garage", "seaweedfs", "blob", "storage"],
    icon: <IconBucket />,
  },
];

type Check = { ok: boolean; latency_ms?: number; error?: string };

// Resolve a service to its matching health check, tolerant of key casing
// and the alias set. Returns null when the backend reports no such check,
// which the card renders as an explicit "not reported" state.
function checkFor(health: Health | null, svc: ServiceDef): Check | null {
  if (!health) return null;
  const entries = Object.entries(health.checks);
  for (const alias of svc.aliases) {
    const hit = entries.find(([name]) => name.toLowerCase() === alias);
    if (hit) return hit[1];
  }
  // Fall back to a substring match so "postgres_primary" still lands on the pg card.
  const loose = entries.find(([name]) =>
    svc.aliases.some((a) => name.toLowerCase().includes(a)),
  );
  return loose ? loose[1] : null;
}

// ------------------------------------------------------------
// Model config. The backend surfaces the resolved adapter names in
// meta.adapters. Where a detail is not carried on the wire (dim count,
// serving runtime) we show it as a labeled spec so it is clearly the
// configured default, not a measured live value.
// ------------------------------------------------------------
interface ConfigRow {
  role: string;
  adapterKey: string;
  fallback: string;
  detail: string;
  accent: "accent" | "cyan";
}

const MODEL_ROWS: ConfigRow[] = [
  {
    role: "Embeddings",
    adapterKey: "embedder",
    fallback: "bge-m3",
    detail: "local via Ollama · 1024-dim (Matryoshka)",
    accent: "cyan",
  },
  {
    role: "LLM",
    adapterKey: "llm",
    fallback: "gemini",
    detail: "OpenAI-compatible endpoint · BYOM",
    accent: "accent",
  },
  {
    role: "Reranker",
    adapterKey: "reranker",
    fallback: "bge-reranker-v2-m3",
    detail: "config-gated · served via TEI",
    accent: "cyan",
  },
];

const INFRA_ROWS: ConfigRow[] = [
  {
    role: "Vector store",
    adapterKey: "vector",
    fallback: "pgvectorscale",
    detail: "hybrid dense + sparse · RRF k=60",
    accent: "accent",
  },
  {
    role: "Authz",
    adapterKey: "authz",
    fallback: "spicedb",
    detail: "Zanzibar tuples · bulk check at retrieval",
    accent: "accent",
  },
  {
    role: "Parser",
    adapterKey: "parser",
    fallback: "docling",
    detail: "layout + section aware chunking",
    accent: "cyan",
  },
];

export default function System() {
  const [health, setHealth] = useState<Health | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [ports, setPorts] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastPoll, setLastPoll] = useState<string | null>(null);

  const load = useCallback(async (initial: boolean) => {
    if (initial) setLoading(true);
    else setRefreshing(true);
    try {
      const [h, m, p] = await Promise.all([systemHealth(), systemMeta(), listAdapters()]);
      setHealth(h);
      setMeta(m);
      setPorts(p);
      setLastPoll(new Date().toISOString());
      setError(null);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
    const t = setInterval(() => void load(false), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const allOk = health ? Object.values(health.checks).every((c) => c.ok) : null;
  const reported = health ? Object.keys(health.checks).length : 0;
  const healthy = health ? Object.values(health.checks).filter((c) => c.ok).length : 0;

  return (
    <div className="mx-auto max-w-5xl pb-16">
      <PageHeader
        breadcrumb={["pansoph", "system"]}
        title="System"
        subtitle="Local-first by default, and every part is a swappable port. This is what is wired and answering right now."
        action={
          <div className="flex items-center gap-2.5">
            {lastPoll && !loading && (
              <span className="hidden items-center gap-1.5 font-mono text-[11px] text-muted sm:inline-flex">
                <StatusDot kind={refreshing ? "run" : "neutral"} size={6} pulse={refreshing} />
                <span className="tabular-nums">synced {relativeTime(lastPoll)}</span>
              </span>
            )}
            <Button variant="ghost" size="sm" onClick={() => void load(false)} disabled={refreshing}>
              {refreshing ? (
                <Spinner className="h-3.5 w-3.5" />
              ) : (
                <IconRefresh />
              )}
              Refresh
            </Button>
          </div>
        }
      />

      {/* Top status ribbon: overall verdict + build meta. */}
      <Card className="mb-7 overflow-hidden shadow-[var(--shadow-float)]" glow>
        <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-4">
            <div
              className="grid h-12 w-12 place-items-center rounded-[var(--radius-md)] border border-border bg-bg"
              aria-hidden
            >
              <StatusDot
                kind={loading ? "neutral" : allOk ? "ok" : allOk === false ? "err" : "warn"}
                size={11}
                pulse={!loading && allOk !== false}
              />
            </div>
            <div>
              <div className="font-display text-[17px] font-semibold tracking-[-0.01em] text-text">
                {loading
                  ? "Reading system state…"
                  : error
                    ? "Backend unreachable"
                    : allOk
                      ? "All services operational"
                      : "Degraded"}
              </div>
              <div className="mt-1 font-mono text-[11px] text-muted">
                {loading ? (
                  "contacting agora"
                ) : error ? (
                  "could not reach the health endpoint"
                ) : (
                  <span className="tabular-nums">
                    {healthy} of {reported} service {reported === 1 ? "port" : "ports"} healthy
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {meta ? (
              <>
                <Badge kind="neutral" className="font-mono">
                  v{meta.version}
                </Badge>
                <Badge kind="neutral">env: {meta.env}</Badge>
                {meta.cloud_model ? (
                  <Tooltip label="A cloud model is handling generation. Content leaves this host.">
                    <Chip kind="warn" dot>
                      cloud model
                    </Chip>
                  </Tooltip>
                ) : (
                  <Tooltip label="All models run locally. No content leaves this host.">
                    <Chip kind="ok" dot>
                      local models
                    </Chip>
                  </Tooltip>
                )}
              </>
            ) : (
              !loading && <span className="text-xs text-muted">build metadata unavailable</span>
            )}
          </div>
        </div>
      </Card>

      {error && !health && (
        <EmptyState
          className="mb-8"
          icon={<IconServer />}
          title="agora is not responding"
          description={
            <>
              The health endpoint returned an error. Start the backend, then refresh.
              <span className="mt-2 block font-mono text-[11px] text-err">{error}</span>
            </>
          }
          action={
            <Button variant="ghost" size="sm" onClick={() => void load(false)}>
              Retry
            </Button>
          }
        />
      )}

      {/* Service health cards. */}
      <SectionLabel
        title="Service health"
        hint={
          health ? (
            <span className="tabular-nums">
              polled every {POLL_MS / 1000}s
            </span>
          ) : undefined
        }
      />
      <div className="mb-9 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {loading && !health
          ? SERVICES.map((s) => <ServiceSkeleton key={s.id} svc={s} />)
          : SERVICES.map((s) => (
              <ServiceCard key={s.id} svc={s} check={checkFor(health, s)} />
            ))}
      </div>

      {/* Active model configuration. */}
      <SectionLabel
        title="Model configuration"
        hint={meta ? "resolved adapters, live" : "spec defaults"}
      />
      <div className="mb-9 grid grid-cols-1 gap-3 md:grid-cols-3">
        {MODEL_ROWS.map((row) => (
          <ConfigCard key={row.role} row={row} meta={meta} loading={loading && !meta} />
        ))}
      </div>

      {/* Retrieval + infra roles. */}
      <SectionLabel title="Retrieval + infrastructure" hint={meta ? "live" : "spec defaults"} />
      <div className="mb-9 grid grid-cols-1 gap-3 md:grid-cols-3">
        {INFRA_ROWS.map((row) => (
          <ConfigCard key={row.role} row={row} meta={meta} loading={loading && !meta} />
        ))}
      </div>

      {/* Ports + installed adapters. */}
      <SectionLabel
        title="Ports + installed adapters"
        hint="the accented chip is the active adapter behind each port"
      />
      <PortsGrid ports={ports} meta={meta} loading={loading && Object.keys(ports).length === 0} />

      {/* Logs and runs footer. */}
      <div className="mt-9">
        <Card className="flex flex-col items-start justify-between gap-4 p-6 shadow-[var(--shadow-float)] sm:flex-row sm:items-center">
          <div className="flex items-center gap-3.5">
            <div className="grid h-11 w-11 place-items-center rounded-[var(--radius-md)] border border-border bg-bg text-muted">
              <IconLogs />
            </div>
            <div>
              <div className="font-display text-[15px] font-semibold text-text">Logs and ingestion runs</div>
              <div className="mt-1 text-xs text-muted">
                Every connector sync and admin action is written to the immutable audit log.
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/connectors" className="inline-flex">
              <Button variant="ghost" size="sm">
                Connector runs
                <IconArrow />
              </Button>
            </Link>
            <Tooltip label="Structured logs are exported over OpenTelemetry when observability is enabled.">
              <span className="inline-flex">
                <Button variant="subtle" size="sm" disabled>
                  Trace explorer
                  <Badge kind="neutral" className="ml-0.5">
                    off
                  </Badge>
                </Button>
              </span>
            </Tooltip>
          </div>
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------
// Section label with an optional right-aligned hint.
// ------------------------------------------------------------
function SectionLabel({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-3">
      <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.16em] text-faint">
        {title}
      </h2>
      {hint && <span className="font-mono text-[10.5px] text-faint">{hint}</span>}
    </div>
  );
}

// ------------------------------------------------------------
// A single service health card. Live latency when up, the error
// string when down, and a clear "not reported" state when the
// backend does not carry a check for this port yet.
// ------------------------------------------------------------
function ServiceCard({ svc, check }: { svc: ServiceDef; check: Check | null }) {
  const state: "up" | "down" | "unknown" = check ? (check.ok ? "up" : "down") : "unknown";
  const dot: StatusKind = state === "up" ? "ok" : state === "down" ? "err" : "neutral";
  const label = state === "up" ? "healthy" : state === "down" ? "down" : "not reported";
  const labelKind = state === "up" ? "text-accent" : state === "down" ? "text-err" : "text-faint";

  return (
    <Card
      glow
      className="group flex flex-col gap-3.5 p-4 shadow-[var(--shadow-float)]"
      aria-label={`${svc.label} ${label}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-[var(--radius-sm)] border border-border bg-bg text-muted transition group-hover:text-accent">
            {svc.icon}
          </span>
          <div>
            <div className="font-mono text-[13px] font-semibold text-text">{svc.label}</div>
            <div className="text-[11px] text-muted">{svc.role}</div>
          </div>
        </div>
        <StatusDot kind={dot} size={9} pulse={state === "up"} />
      </div>

      <div className="flex items-center justify-between border-t border-border pt-3.5">
        <span className={`font-mono text-[11px] uppercase tracking-wide ${labelKind}`}>{label}</span>
        <span className="text-xs tabular-nums text-muted">
          {state === "up" ? (
            <span className="font-mono text-accent">{check?.latency_ms ?? "–"} ms</span>
          ) : state === "down" ? (
            <Tooltip label={check?.error ?? "unreachable"}>
              <span className="max-w-[8rem] cursor-help truncate font-mono text-err/90">
                {check?.error ?? "unreachable"}
              </span>
            </Tooltip>
          ) : (
            <span className="font-mono text-faint">no check</span>
          )}
        </span>
      </div>
    </Card>
  );
}

function ServiceSkeleton({ svc }: { svc: ServiceDef }) {
  return (
    <Card className="flex flex-col gap-3.5 p-4 shadow-[var(--shadow-float)]">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-[var(--radius-sm)] border border-border bg-bg text-muted/50">
            {svc.icon}
          </span>
          <div>
            <div className="font-mono text-[13px] font-semibold text-muted">{svc.label}</div>
            <div className="h-3 w-16 animate-pulse rounded bg-elevated" />
          </div>
        </div>
        <StatusDot kind="neutral" size={9} />
      </div>
      <div className="flex items-center justify-between border-t border-border pt-3.5">
        <span className="h-3 w-14 animate-pulse rounded bg-elevated" />
        <span className="h-3 w-10 animate-pulse rounded bg-elevated" />
      </div>
    </Card>
  );
}

// ------------------------------------------------------------
// A config card for one role. Shows the live adapter name when
// meta carries it, else the spec default flagged as such.
// ------------------------------------------------------------
function ConfigCard({
  row,
  meta,
  loading,
}: {
  row: ConfigRow;
  meta: Meta | null;
  loading: boolean;
}) {
  const live = meta?.adapters[row.adapterKey];
  const name = live ?? row.fallback;
  const isDefault = !live;

  return (
    <Card glow className="flex flex-col gap-3 p-4 shadow-[var(--shadow-float)]">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10.5px] font-medium uppercase tracking-[0.14em] text-faint">
          {row.role}
        </span>
        {loading ? (
          <span className="h-3.5 w-10 animate-pulse rounded-full bg-elevated" />
        ) : isDefault ? (
          <Tooltip label="Not reported by the backend; showing the configured default.">
            <Chip kind="neutral" className="cursor-help">
              spec
            </Chip>
          </Tooltip>
        ) : (
          <Chip kind={row.accent} dot>
            live
          </Chip>
        )}
      </div>
      <div className="font-mono text-[15px] font-semibold tracking-tight text-text">
        {loading ? <span className="inline-block h-4 w-24 animate-pulse rounded bg-elevated" /> : name}
      </div>
      <div className="text-xs leading-relaxed text-muted">{row.detail}</div>
    </Card>
  );
}

// ------------------------------------------------------------
// Ports grid. Reads the adapters map (port -> installed names) and
// highlights the active adapter for each port from meta.adapters.
// ------------------------------------------------------------
const ACTIVE_KEY: Record<string, string> = {
  embedders: "embedder",
  rerankers: "reranker",
  llms: "llm",
  vectorstores: "vector",
  parsers: "parser",
  connectors: "connector",
  authz: "authz",
};

function PortsGrid({
  ports,
  meta,
  loading,
}: {
  ports: Record<string, string[]>;
  meta: Meta | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <Card key={i} className="p-4 shadow-[var(--shadow-float)]">
            <div className="mb-3 h-3 w-20 animate-pulse rounded bg-elevated" />
            <div className="flex gap-1.5">
              <div className="h-5 w-16 animate-pulse rounded-full bg-elevated" />
              <div className="h-5 w-12 animate-pulse rounded-full bg-elevated" />
            </div>
          </Card>
        ))}
      </div>
    );
  }

  const entries = Object.entries(ports);
  if (entries.length === 0) {
    return (
      <EmptyState
        icon={<IconPlug />}
        title="No adapters discovered"
        description="Adapters register through Python entry points. Install a package that declares one and it appears here."
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {entries.map(([group, names]) => {
        const port = group.replace("pansoph.", "");
        const active = meta?.adapters[ACTIVE_KEY[port] ?? port];
        return (
          <Card key={group} glow className="p-4 shadow-[var(--shadow-float)]">
            <div className="mb-3 flex items-center justify-between">
              <span className="font-mono text-xs font-semibold uppercase tracking-[0.12em] text-faint">
                {port}
              </span>
              <span className="font-mono text-[10.5px] tabular-nums text-faint">
                {names.length} installed
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {names.length === 0 && (
                <span className="font-mono text-[10.5px] uppercase tracking-wide text-faint">none installed</span>
              )}
              {names.map((n) => {
                const on = n === active;
                return on ? (
                  <Chip key={n} kind="accent" dot>
                    {n}
                  </Chip>
                ) : (
                  <span
                    key={n}
                    className="inline-flex items-center rounded-full border border-border bg-bg px-2.5 py-0.5 text-xs text-muted transition hover:border-border-hover hover:text-text"
                  >
                    {n}
                  </span>
                );
              })}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------
// Inline icons. Stroke follows currentColor, 1.6px hairlines to
// sit right on the dark surfaces. Kept local to this page.
// ------------------------------------------------------------
function svgProps(size = 16) {
  return {
    viewBox: "0 0 24 24",
    width: size,
    height: size,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
}

function IconServer() {
  return (
    <svg {...svgProps()}>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </svg>
  );
}
function IconDatabase() {
  return (
    <svg {...svgProps()}>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </svg>
  );
}
function IconShield() {
  return (
    <svg {...svgProps()}>
      <path d="M12 3l8 3.5v5c0 4.6-3.2 7.2-8 8.5-4.8-1.3-8-3.9-8-8.5v-5z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}
function IconBolt() {
  return (
    <svg {...svgProps()}>
      <path d="M13 2 4 14h7l-1 8 9-12h-7z" />
    </svg>
  );
}
function IconBucket() {
  return (
    <svg {...svgProps()}>
      <path d="M4 7h16l-1.5 12.5a1 1 0 0 1-1 .5H6.5a1 1 0 0 1-1-.5z" />
      <path d="M4 7a8 3 0 1 0 16 0" />
    </svg>
  );
}
function IconRefresh() {
  return (
    <svg {...svgProps(14)}>
      <path d="M20 11a8 8 0 1 0-2.3 5.7" />
      <path d="M20 4v5h-5" />
    </svg>
  );
}
function IconLogs() {
  return (
    <svg {...svgProps()}>
      <path d="M8 6h11M8 12h11M8 18h11" />
      <path d="M4 6h.01M4 12h.01M4 18h.01" />
    </svg>
  );
}
function IconArrow() {
  return (
    <svg {...svgProps(13)}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
function IconPlug() {
  return (
    <svg {...svgProps(22)}>
      <path d="M9 2v6M15 2v6" />
      <path d="M7 8h10v3a5 5 0 0 1-10 0z" />
      <path d="M12 16v6" />
    </svg>
  );
}
