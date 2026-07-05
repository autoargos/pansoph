import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { listResources, type Resource } from "../api.ts";
import { Badge, Button, Input, Spinner } from "../ui.tsx";
import {
  IconChevronRight,
  IconCheck,
  IconFolder,
  IconSearch,
} from "../pages/connectorGlyphs.tsx";

// ============================================================
// ResourcePicker
//
// Connect-then-pick. Given a live connector instance (its sealed credential can already
// reach a tree of resources) and a resource_kind, this lists the resources the credential
// can see, with server-side search and cursor pagination, and lets the user drill down by
// parent (org -> repo, drive -> folder) and select one or many. The chosen ids fill the
// bound config field back in the Add / configure form.
//
// The picker never invents a resource. It only surfaces what GET
// /instances/{id}/resources returns, which is exactly what the credential can reach.
// ============================================================

// One level of the drill-down: which kind we are listing and, once the user descends into a
// container, which parent scopes the list. `label` is the container's display label for the
// breadcrumb (null at the root).
interface Level {
  kind: string;
  parent: string | null;
  label: string | null;
}

export function ResourcePicker({
  instanceId,
  resourceKind,
  multi,
  value,
  onChange,
  disabled = false,
}: {
  instanceId: string;
  // The kind the bound field wants (e.g. "repo"). This is the leaf: a selection of this
  // kind fills the field. Containers of a different kind are drilled into, not selected.
  resourceKind: string;
  multi: boolean;
  // Selected resource ids. Single-select still uses an array of length <= 1 for a uniform
  // shape; the form flattens it when writing the config.
  value: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  // The drill-down stack. The last entry is the level currently shown. The root lists the
  // top level (kind undefined on the wire), so its `kind` is only a label hint; we pass the
  // parent, not the kind, to scope descent.
  const [stack, setStack] = useState<Level[]>([{ kind: resourceKind, parent: null, label: null }]);
  const level = stack[stack.length - 1];

  const [items, setItems] = useState<Resource[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");

  // Reset the drill-down whenever the bound instance or kind changes (e.g. the form swaps
  // connector type). Keeps a stale tree from leaking across configs.
  useEffect(() => {
    setStack([{ kind: resourceKind, parent: null, label: null }]);
    setQuery("");
    setDebounced("");
  }, [instanceId, resourceKind]);

  // Debounce the search box so we do not fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  // The kind to request at the current level. The root lists the top level (no kind on the
  // wire, so the source returns its entry points: github orgs, drive shared drives). Once
  // descended into a container we ask for the leaf kind under that parent (kind=repo under an
  // org, kind=folder under a drive), because the source keys the child listing on the kind,
  // not the parent alone. Correct for every current connector; a deeper heterogeneous tree
  // would carry the child kind on the container resource.
  const wireKind = level.parent == null ? undefined : resourceKind;

  // Load the first page for the current level and search term. A monotonic request id guards
  // against an out-of-order response from a superseded level or query overwriting the view.
  const reqSeq = useRef(0);
  const load = useCallback(async () => {
    const mine = ++reqSeq.current;
    setLoading(true);
    setError(null);
    setItems([]);
    setCursor(null);
    try {
      const page = await listResources(instanceId, {
        kind: wireKind,
        parent: level.parent ?? undefined,
        q: debounced || undefined,
      });
      if (mine !== reqSeq.current) return;
      setItems(page.items);
      setCursor(page.next_cursor);
    } catch (e) {
      if (mine !== reqSeq.current) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mine === reqSeq.current) setLoading(false);
    }
  }, [instanceId, level.parent, wireKind, debounced]);

  useEffect(() => {
    load();
  }, [load]);

  async function loadMore() {
    if (!cursor || loadingMore) return;
    const mine = reqSeq.current;
    setLoadingMore(true);
    try {
      const page = await listResources(instanceId, {
        kind: wireKind,
        parent: level.parent ?? undefined,
        q: debounced || undefined,
        cursor,
      });
      if (mine !== reqSeq.current) return;
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
    } catch (e) {
      if (mine !== reqSeq.current) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mine === reqSeq.current) setLoadingMore(false);
    }
  }

  const selected = useMemo(() => new Set(value), [value]);

  function toggle(r: Resource) {
    if (!r.selectable) return;
    if (multi) {
      const next = new Set(selected);
      if (next.has(r.id)) next.delete(r.id);
      else next.add(r.id);
      onChange([...next]);
    } else {
      onChange(selected.has(r.id) ? [] : [r.id]);
    }
  }

  // Descend into a container. A resource is a container when it is not the leaf kind we are
  // picking (an org while picking repos), so drilling in lists its children. The leaf kind
  // is selected, not entered.
  function isContainer(r: Resource): boolean {
    return r.kind !== resourceKind;
  }

  function enter(r: Resource) {
    setStack((s) => [...s, { kind: r.kind, parent: r.id, label: r.label }]);
    setQuery("");
    setDebounced("");
  }

  function goTo(depth: number) {
    setStack((s) => s.slice(0, depth + 1));
    setQuery("");
    setDebounced("");
  }

  return (
    <div className="rounded-[var(--radius-md)] border border-border bg-bg">
      {/* breadcrumb + search */}
      <div className="border-b border-border p-2.5">
        {stack.length > 1 && (
          <div className="mb-2 flex flex-wrap items-center gap-1 text-[11.5px] text-muted">
            {stack.map((lv, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <IconChevronRight width={12} height={12} className="opacity-50" />}
                <button
                  type="button"
                  onClick={() => goTo(i)}
                  disabled={i === stack.length - 1}
                  className={
                    i === stack.length - 1
                      ? "font-medium text-text"
                      : "text-muted transition hover:text-text"
                  }
                >
                  {i === 0 ? "All" : (lv.label ?? lv.parent)}
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="relative">
          <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted">
            <IconSearch width={14} height={14} />
          </span>
          <Input
            className="pl-8 text-[13px]"
            value={query}
            disabled={disabled}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${level.label ? `${level.label} ` : ""}resources`}
          />
        </div>
      </div>

      {/* list */}
      <div className="max-h-64 overflow-y-auto">
        {loading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : error ? (
          <div className="px-3.5 py-6 text-center text-[12px] text-err">{error}</div>
        ) : items.length === 0 ? (
          <div className="px-3.5 py-6 text-center text-[12px] text-muted">
            {debounced ? "No resources match your search." : "No resources here."}
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {items.map((r) => {
              const on = selected.has(r.id);
              const container = isContainer(r);
              return (
                <li key={r.id}>
                  <div
                    className={
                      "flex items-center gap-2.5 px-3 py-2 transition " +
                      (on ? "bg-accent-soft" : "hover:bg-surface")
                    }
                  >
                    {/* select control for a pickable leaf; a container has no checkbox */}
                    {r.selectable ? (
                      <button
                        type="button"
                        onClick={() => toggle(r)}
                        disabled={disabled}
                        aria-pressed={on}
                        aria-label={on ? `Deselect ${r.label}` : `Select ${r.label}`}
                        className={
                          "grid h-[18px] w-[18px] shrink-0 place-items-center border transition " +
                          (multi ? "rounded-[5px] " : "rounded-full ") +
                          (on ? "border-accent bg-accent" : "border-border bg-surface hover:border-border-hover")
                        }
                      >
                        <IconCheck
                          width={11}
                          height={11}
                          className={"text-white transition-opacity " + (on ? "opacity-100" : "opacity-0")}
                        />
                      </button>
                    ) : (
                      <span className="grid h-[18px] w-[18px] shrink-0 place-items-center text-muted">
                        <IconFolder width={15} height={15} />
                      </span>
                    )}

                    <button
                      type="button"
                      onClick={() => (container ? enter(r) : toggle(r))}
                      disabled={disabled || (!container && !r.selectable)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] font-medium text-text">
                          {r.label}
                        </span>
                        <span className="block truncate font-mono text-[10.5px] text-muted">
                          {r.kind}
                          <span className="opacity-40"> . </span>
                          {r.id}
                        </span>
                      </span>
                    </button>

                    {container ? (
                      <button
                        type="button"
                        onClick={() => enter(r)}
                        disabled={disabled}
                        aria-label={`Open ${r.label}`}
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-[7px] text-muted transition hover:bg-elevated hover:text-text"
                      >
                        <IconChevronRight width={15} height={15} />
                      </button>
                    ) : (
                      on && <Badge kind="accent">selected</Badge>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* pagination + selection summary */}
      {(cursor || value.length > 0) && (
        <div className="flex items-center gap-2 border-t border-border px-3 py-2">
          {value.length > 0 && (
            <span className="text-[11.5px] text-muted">
              {multi ? `${value.length} selected` : "1 selected"}
            </span>
          )}
          <div className="flex-1" />
          {cursor && (
            <Button variant="ghost" size="sm" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? (
                <>
                  <Spinner />
                  Loading
                </>
              ) : (
                "Load more"
              )}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
