import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type Identity, search, type SearchResult } from "../api.ts";
import { ConnectorLogo } from "../components/ConnectorLogo.tsx";
import { Button, Card, EmptyState, Spinner, Tooltip } from "../ui.tsx";

// ============================================================
// Search . permission-filtered retrieval
//
// The query bar drives the same permission-enforced /search path
// agora serves for chat and everyone else: results are filtered by
// what the acting identity may read BEFORE they reach this screen.
// The API returns a thin SearchResult (doc_id, chunk_id, score,
// text). Everything else on a result card, the human title, the
// connector source, and the access-path badge, is derived from that
// shape defensively so the page degrades gracefully on odd ids.
// ============================================================

// A result enriched with everything the card needs, computed once per
// search so the render path stays cheap and deterministic.
interface Enriched {
  raw: SearchResult;
  rank: number;
  title: string;
  connector: string; // machine name, e.g. "github", or "unknown"
  path: string; // remainder of the doc id after the connector
  snippet: ReactNode; // text with query matches wrapped in <mark>
  accessGroup: string; // which group / grant makes this visible
  relevance: number; // 0..1, this result's score relative to the top result
  scoreClass: "high" | "mid" | "low";
}

// doc ids arrive in a handful of shapes across connectors. Peel the
// connector prefix off the front (accepting "::", ":", or "/" as the
// separator the ingestion side happens to use) and keep the rest as a
// path. Fall back cleanly when there is no separator at all.
function splitDocId(docId: string): { connector: string; path: string } {
  const id = docId.trim();
  if (!id) return { connector: "unknown", path: "" };
  // The ingestion side stamps ids as connector:instance:source_doc_id. The source doc id
  // may itself contain colons (a URL like https://...), so take everything after the
  // second colon as the path and drop the noisy instance id. This yields clean titles
  // ("Package Lock", "Wiki") instead of the whole instance-qualified id.
  const colon = id.split(":");
  if (colon.length >= 3) {
    const connector = colon[0].trim().toLowerCase() || "unknown";
    const path = colon.slice(2).join(":").trim();
    return { connector, path: path || id };
  }
  const sep = id.includes("::") ? "::" : id.includes(":") ? ":" : id.includes("/") ? "/" : null;
  if (!sep) return { connector: "unknown", path: id };
  const idx = id.indexOf(sep);
  const connector = id.slice(0, idx).trim().toLowerCase() || "unknown";
  const path = id.slice(idx + sep.length).trim();
  return { connector, path: path || id };
}

// Turn the tail of a doc id into a readable title: take the last path
// segment, drop a file extension, and titleize separators.
function humanTitle(path: string, fallback: string): string {
  const base = (path.split(/[/\\]/).pop() || path).trim();
  const noExt = base.replace(/\.[a-z0-9]{1,6}$/i, "");
  const words = noExt
    .replace(/[_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!words) return fallback || "Untitled document";
  return words.replace(/\b\w/g, (c) => c.toUpperCase());
}

// Tokenize a query into distinct lowercase terms >= 2 chars, longest
// first so overlapping matches prefer the more specific term.
function queryTerms(q: string): string[] {
  const seen = new Set<string>();
  for (const t of q.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []) {
    if (t.length >= 2) seen.add(t);
  }
  return [...seen].sort((a, b) => b.length - a.length);
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Build a highlighted, length-bounded snippet. Center the window on the
// first match when there is one, otherwise show the head of the chunk.
function makeSnippet(text: string | null, terms: string[]): ReactNode {
  const clean = (text ?? "").replace(/\s+/g, " ").trim();
  if (!clean) return <span className="italic text-faint">No preview text for this chunk.</span>;

  const WINDOW = 260;
  let start = 0;
  if (terms.length) {
    const re = new RegExp(terms.map(escapeRegExp).join("|"), "i");
    const hit = clean.search(re);
    if (hit > 90) start = hit - 70;
  }
  let slice = clean.slice(start, start + WINDOW);
  const prefix = start > 0 ? "… " : "";
  const suffix = start + WINDOW < clean.length ? " …" : "";
  slice = prefix + slice + suffix;

  if (!terms.length) return slice;

  const re = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  const parts = slice.split(re);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark
        key={i}
        className="rounded-[3px] bg-accent-soft px-0.5 text-text [box-shadow:0_0_0_1px_var(--color-accent-line)]"
      >
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

// The access path is the reason this result is visible to the acting
// user. When exactly one of the user's groups is named in the doc id,
// attribute visibility to that group; otherwise attribute it to the
// user's direct grant. This is presentation only, agora already did
// the real enforcement.
function accessPathFor(docId: string, id: Identity): string {
  const hay = docId.toLowerCase();
  const named = id.groups.filter((g) => g && hay.includes(g.toLowerCase()));
  if (named.length === 1) return named[0];
  if (id.groups.length === 1) return id.groups[0];
  return `${id.user} (direct)`;
}

function scoreBucket(score: number): Enriched["scoreClass"] {
  if (score >= 0.75) return "high";
  if (score >= 0.5) return "mid";
  return "low";
}

// Relevance bar fill per bucket. The raw retrieval score is not a 0..1 value and means
// little to a person, so results show a bar relative to the top hit and keep the raw
// number in a tooltip.
const BAR_FILL: Record<Enriched["scoreClass"], string> = {
  high: "bg-cyan",
  mid: "bg-accent",
  low: "bg-muted",
};

// Small connector mark. Renders the official brand logo when one is bundled,
// otherwise a neutral tile, via the shared ConnectorLogo.
function ConnectorMark({ connector }: { connector: string }) {
  return <ConnectorLogo connector={connector} size={32} />;
}

// A small shield-check glyph used by the access-enforced meta line.
function ShieldCheck({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M12 2 4 5v6c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V5z" strokeLinejoin="round" />
      <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// The owl-iris aperture glyph: a ring with a filled center. Teal reads as
// "permitted". Used on the per-result permission tag so each hit carries the
// same calm signal the chat provenance strip uses.
function Aperture({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

const ALL = "__all__";

export default function Search({ identity }: { identity: Identity }) {
  const [q, setQ] = useState("");
  const [ranQuery, setRanQuery] = useState(""); // the query behind the current results
  const [results, setResults] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [ran, setRan] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [connectorFilter, setConnectorFilter] = useState<string>(ALL);
  const [groupFilter, setGroupFilter] = useState<string>(ALL);
  const inputRef = useRef<HTMLInputElement>(null);

  // Live search: runs on a debounce as the query changes, and immediately on submit /
  // Enter. A sequence guard makes sure a slow older response never overwrites a newer one.
  const seq = useRef(0);
  const runSearch = useCallback(
    async (raw: string) => {
      const query = raw.trim();
      if (query.length < 2) {
        setRan(false);
        setResults([]);
        setError(null);
        setElapsed(null);
        return;
      }
      const mine = ++seq.current;
      setBusy(true);
      setError(null);
      const t0 = performance.now();
      try {
        const res = await search(query, identity);
        if (mine !== seq.current) return;
        setResults(res);
        setRanQuery(query);
        setRan(true);
        setConnectorFilter(ALL);
        setGroupFilter(ALL);
        setElapsed(performance.now() - t0);
      } catch (err) {
        if (mine !== seq.current) return;
        setError(err instanceof Error ? err.message : String(err));
        setResults([]);
        setRan(true);
        setElapsed(null);
      } finally {
        if (mine === seq.current) setBusy(false);
      }
    },
    [identity],
  );

  useEffect(() => {
    const t = setTimeout(() => void runSearch(q), 240);
    return () => clearTimeout(t);
  }, [q, runSearch]);

  function run(e?: FormEvent) {
    e?.preventDefault();
    void runSearch(q);
  }

  // Enrich every result once, keyed on the query that produced them.
  const enriched = useMemo<Enriched[]>(() => {
    const terms = queryTerms(ranQuery);
    const maxScore = results.reduce((m, r) => Math.max(m, r.score), 0) || 1;
    return results.map((raw, i) => {
      const { connector, path } = splitDocId(raw.doc_id);
      const relevance = Math.max(0.08, Math.min(1, raw.score / maxScore));
      return {
        raw,
        rank: i + 1,
        title: humanTitle(path, raw.doc_id),
        connector,
        path,
        snippet: makeSnippet(raw.text, terms),
        accessGroup: accessPathFor(raw.doc_id, identity),
        relevance,
        scoreClass: scoreBucket(relevance),
      };
    });
  }, [results, ranQuery, identity]);

  // Facets for the filter row, each with a live count.
  const connectorFacets = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of enriched) counts.set(e.connector, (counts.get(e.connector) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [enriched]);

  const groupFacets = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of enriched) counts.set(e.accessGroup, (counts.get(e.accessGroup) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [enriched]);

  const visible = useMemo(
    () =>
      enriched.filter(
        (e) =>
          (connectorFilter === ALL || e.connector === connectorFilter) &&
          (groupFilter === ALL || e.accessGroup === groupFilter),
      ),
    [enriched, connectorFilter, groupFilter],
  );

  const hasFilters = connectorFilter !== ALL || groupFilter !== ALL;
  const showFilterRow = ran && !error && enriched.length > 0;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <div className="mb-2 flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.15em] text-faint">
          <span>lens</span>
          <span className="opacity-50">/</span>
          <span>search</span>
        </div>
        <h1 className="font-display text-[30px] font-semibold leading-[1.05] tracking-[-0.02em] text-text">
          Search what you can <span className="text-accent">reach.</span>
        </h1>
        <p className="mt-2 max-w-xl text-[14px] leading-relaxed text-muted">
          Permission-filtered retrieval. Results are scoped to what you may read before they reach
          you, never after.
        </p>
      </div>

      {/* ---- Query bar (mono) ---- */}
      <form onSubmit={run} className="group/q">
        <div className="flex items-center gap-2.5 rounded-[var(--radius-lg)] border border-border bg-bg px-3.5 py-2.5 transition focus-within:border-accent-line">
          {busy ? (
            <Spinner className="h-[18px] w-[18px]" />
          ) : (
            <svg
              viewBox="0 0 24 24"
              className="h-[18px] w-[18px] shrink-0 text-muted transition group-focus-within/q:text-accent"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" strokeLinecap="round" />
            </svg>
          )}
          <input
            ref={inputRef}
            className="min-w-0 flex-1 bg-transparent font-mono text-[13.5px] text-text outline-none placeholder:font-mono placeholder:text-muted"
            placeholder="search permitted documents…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            autoFocus
            aria-label="Search query"
          />
          {q && !busy && (
            <button
              type="button"
              onClick={() => {
                setQ("");
                inputRef.current?.focus();
              }}
              aria-label="Clear query"
              className="grid h-6 w-6 shrink-0 place-items-center rounded-[var(--radius-sm)] text-muted transition hover:bg-elevated hover:text-text"
            >
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="M18 6 6 18M6 6l12 12" strokeLinecap="round" />
              </svg>
            </button>
          )}
          <Button type="submit" size="sm" disabled={busy || !q.trim()} className="shrink-0">
            {busy ? "Searching" : "Search"}
          </Button>
        </div>
      </form>

      {/* ---- How retrieval stays permitted: the one-line provenance of the list ---- */}
      <p className="mt-3 flex items-start gap-2 px-1 font-mono text-[11px] leading-relaxed text-faint">
        <Aperture className="mt-0.5 h-[13px] w-[13px] shrink-0 text-accent" />
        <span>
          Over-fetch candidates, run one bulk authz check, keep only the allowed subset, then rerank.
        </span>
      </p>

      {/* ---- Result meta strip: count, timing, active query ---- */}
      {ran && !error && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted">
          <span className="tabular-nums">
            <span className="font-semibold text-text">{visible.length}</span>
            {hasFilters && (
              <>
                {" "}
                of <span className="font-semibold text-text">{enriched.length}</span>
              </>
            )}{" "}
            {enriched.length === 1 && !hasFilters ? "result" : "results"}
          </span>
          {ranQuery && (
            <>
              <span className="opacity-40">.</span>
              <span className="truncate font-mono text-[11px] text-muted">
                for &ldquo;<span className="text-text">{ranQuery}</span>&rdquo;
              </span>
            </>
          )}
          {elapsed != null && (
            <>
              <span className="opacity-40">.</span>
              <span className="tabular-nums font-mono text-[11px]">{elapsed.toFixed(0)} ms</span>
            </>
          )}
          <span className="opacity-40">.</span>
          <Tooltip label="Filtered by your group ACLs at retrieval time">
            <span className="inline-flex items-center gap-1 text-ok">
              <ShieldCheck className="h-3 w-3" />
              access-enforced
            </span>
          </Tooltip>
        </div>
      )}

      {/* ---- Filter row: by connector, by group ---- */}
      {showFilterRow && (
        <div className="mt-4 space-y-2.5">
          <FilterGroup
            label="Source"
            all={enriched.length}
            active={connectorFilter}
            facets={connectorFacets}
            onSelect={setConnectorFilter}
          />
          {groupFacets.length > 1 && (
            <FilterGroup
              label="Access"
              all={enriched.length}
              active={groupFilter}
              facets={groupFacets}
              onSelect={setGroupFilter}
              accent
            />
          )}
        </div>
      )}

      {/* ---- Error state ---- */}
      {error && (
        <Card className="mt-6 border-[rgba(244,63,94,0.32)] bg-err-soft p-6">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full border border-[rgba(244,63,94,0.32)] bg-err-soft text-err">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="M12 9v4M12 17h.01" strokeLinecap="round" />
                <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" strokeLinejoin="round" />
              </svg>
            </span>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-err">Search could not complete</div>
              <div className="mt-1 break-words font-mono text-xs text-err/85">{error}</div>
              <div className="mt-3">
                <Button variant="danger" size="sm" onClick={() => void run()}>
                  Retry
                </Button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* ---- Results ---- */}
      {!error && (
        <ul className="mt-4 space-y-3">
          {visible.map((e) => (
            <ResultCard key={e.raw.chunk_id} e={e} />
          ))}
        </ul>
      )}

      {/* ---- Empty (initial) state ---- */}
      {!ran && !busy && !error && (
        <div className="mt-6">
          <EmptyState
            icon={
              <svg viewBox="0 0 24 24" className="h-8 w-8" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
                <circle cx="11" cy="11" r="7" />
                <path d="m21 21-4.3-4.3" strokeLinecap="round" />
              </svg>
            }
            title="Search your permitted knowledge"
            description="Every source you can reach, in one ranked, access-aware index. Try a topic, a project, or a person."
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">Try</span>
            {["wiki", "documentation", "readme"].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setQ(s);
                  inputRef.current?.focus();
                }}
                className="rounded-full border border-border bg-surface px-3 py-1 font-mono text-[11px] text-muted transition hover:border-accent-line hover:bg-accent-soft hover:text-text"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ---- No results for the query ---- */}
      {ran && !error && enriched.length === 0 && !busy && (
        <div className="mt-6">
          <EmptyState
            icon={
              <svg viewBox="0 0 24 24" className="h-8 w-8" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
                <rect x="4" y="10" width="16" height="11" rx="2" />
                <path d="M8 10V7a4 4 0 0 1 8 0v3" />
              </svg>
            }
            title="Nothing you can see matches"
            description={
              <>
                No permitted results for{" "}
                <span className="font-mono text-text">&ldquo;{ranQuery}&rdquo;</span>. There may be
                matching documents that <span className="font-medium text-text">{identity.user}</span>{" "}
                is not permitted to retrieve, they are never shown here.
              </>
            }
          />
        </div>
      )}

      {/* ---- No results after filtering ---- */}
      {ran && !error && enriched.length > 0 && visible.length === 0 && (
        <Card className="mt-4 flex items-center justify-between gap-4 px-5 py-4">
          <div className="text-sm text-muted">
            No results match the active filter.
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setConnectorFilter(ALL);
              setGroupFilter(ALL);
            }}
          >
            Clear filters
          </Button>
        </Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------
// One horizontal facet strip. Renders an "All" pill plus a pill per
// facet value, each with a tabular count. The active pill glows.
// ------------------------------------------------------------
function FilterGroup({
  label,
  all,
  active,
  facets,
  onSelect,
  accent = false,
}: {
  label: string;
  all: number;
  active: string;
  facets: Array<[string, number]>;
  onSelect: (value: string) => void;
  accent?: boolean;
}) {
  if (facets.length <= 1 && !accent) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-12 shrink-0 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <FacetPill label="All" count={all} on={active === ALL} onClick={() => onSelect(ALL)} accent={accent} />
      {facets.map(([value, count]) => (
        <FacetPill
          key={value}
          label={value}
          count={count}
          on={active === value}
          onClick={() => onSelect(value)}
          accent={accent}
        />
      ))}
    </div>
  );
}

function FacetPill({
  label,
  count,
  on,
  onClick,
  accent,
}: {
  label: string;
  count: number;
  on: boolean;
  onClick: () => void;
  accent: boolean;
}) {
  const activeCls = accent
    ? "border-accent-line bg-accent-soft text-text"
    : "border-cyan/40 bg-cyan-soft text-text";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition ${
        on ? activeCls : "border-border bg-surface text-muted hover:border-border-hover hover:text-text"
      }`}
    >
      <span className="max-w-[13rem] truncate font-mono text-[11px]">{label}</span>
      <span className="tabular-nums text-[10.5px] opacity-70">{count}</span>
    </button>
  );
}

// ------------------------------------------------------------
// A single ranked result card.
// ------------------------------------------------------------
function ResultCard({ e }: { e: Enriched }) {
  return (
    <Card glow className="group/r overflow-hidden shadow-[var(--shadow-float)]">
      <div className="flex gap-4 p-5">
        {/* rank + connector rail */}
        <div className="flex shrink-0 flex-col items-center gap-2.5">
          <span className="font-mono text-[11px] tabular-nums text-faint">
            {String(e.rank).padStart(2, "0")}
          </span>
          <ConnectorMark connector={e.connector} />
        </div>

        {/* body */}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <h3 className="font-display truncate text-[16px] font-semibold leading-snug tracking-[-0.01em] text-text transition group-hover/r:text-accent">
              {e.title}
            </h3>
            <Tooltip label={`relevance ${Math.round(e.relevance * 100)}% (raw score ${e.raw.score.toFixed(2)})`}>
              <span className="mt-0.5 flex shrink-0 items-center gap-2">
                <span className="font-mono text-[11px] tabular-nums text-faint">
                  {e.raw.score.toFixed(2)}
                </span>
                <span className="h-1.5 w-16 overflow-hidden rounded-full bg-border">
                  <span
                    className={`block h-full rounded-full ${BAR_FILL[e.scoreClass]}`}
                    style={{ width: `${Math.round(e.relevance * 100)}%` }}
                  />
                </span>
              </span>
            </Tooltip>
          </div>

          <p className="mt-2 text-[13px] leading-relaxed text-muted">{e.snippet}</p>

          {/* meta: permitted tag . connector source . chunk path */}
          <div className="mt-3.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 font-mono text-[11px] text-muted">
            <Tooltip label={`the grant that makes this result visible to you: ${e.accessGroup}`}>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-accent-line bg-accent-soft px-2 py-0.5 text-accent">
                <Aperture className="h-3 w-3" />
                permitted
              </span>
            </Tooltip>
            <span className="opacity-40">.</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-faint">source</span>
              <span className="text-muted">{e.connector}</span>
            </span>
            {e.path && (
              <>
                <span className="opacity-40">.</span>
                <Tooltip label={e.raw.doc_id}>
                  <span className="max-w-[16rem] truncate text-faint">{e.path}</span>
                </Tooltip>
              </>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
