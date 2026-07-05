import { useEffect, useMemo, useRef, useState } from "react";

import { Kbd } from "../ui.tsx";

// A command the palette can run. Each entry belongs to a group, has a label the user
// searches against, an optional keyboard shortcut hint, an optional inline icon, and a
// run() side effect fired on Enter or click.
export interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon?: React.ReactNode;
  run: () => void;
}

// Substring plus loose fuzzy match: a query matches if its characters appear in order
// somewhere in the label (case insensitive). An empty query matches everything. This is
// intentionally forgiving so "adcon" finds "Add connector".
function matches(label: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (q === "") return true;
  const l = label.toLowerCase();
  if (l.includes(q)) return true;
  let i = 0;
  for (const ch of l) {
    if (ch === q[i]) i += 1;
    if (i === q.length) return true;
  }
  return i === q.length;
}

// Split a hint like "Cmd K" into individual keycaps so each renders as its own Kbd.
function hintKeys(hint: string): string[] {
  return hint.split(/\s+/).filter((k) => k.length > 0);
}

// A Raycast / VS Code style command palette. It searches the APP ITSELF (pages and
// actions), never documents. Filtering, grouping, and keyboard navigation live here;
// the caller owns the command list and open state.
export function CommandPalette({
  open,
  onClose,
  commands,
}: {
  open: boolean;
  onClose: () => void;
  commands: Command[];
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // The flat, filtered, in-order list. Selection indexes into this. Grouping for display
  // is derived from it below so the two never drift.
  const filtered = useMemo(
    () => commands.filter((c) => matches(c.label, query)),
    [commands, query],
  );

  // Reset selection whenever the query changes (first item selected by default).
  useEffect(() => {
    setActive(0);
  }, [query]);

  // Reset query and focus the input each time the palette opens.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  // Keep the active row scrolled into view as the selection moves.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  // Escape closes the palette from anywhere, not only when the input has focus.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const run = (cmd: Command | undefined) => {
    if (!cmd) return;
    onClose();
    cmd.run();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (filtered.length === 0 ? 0 : (i + 1) % filtered.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (filtered.length === 0 ? 0 : (i - 1 + filtered.length) % filtered.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(filtered[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  // Build display groups in first-seen order, tracking each item's flat index so hover
  // and click map back to the same selection model the keyboard uses.
  const groups: { name: string; items: { cmd: Command; index: number }[] }[] = [];
  filtered.forEach((cmd, index) => {
    let g = groups.find((x) => x.name === cmd.group);
    if (!g) {
      g = { name: cmd.group, items: [] };
      groups.push(g);
    }
    g.items.push({ cmd, index });
  });

  return (
    <div
      className="animate-fade fixed inset-0 z-50 grid place-items-start justify-items-center bg-[rgba(4,4,8,0.72)] p-6 pt-[12vh] backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="animate-rise w-full max-w-xl overflow-hidden rounded-[var(--radius-xl)] border border-border bg-elevated shadow-[var(--shadow-float)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-border px-4 py-3.5">
          <svg
            viewBox="0 0 24 24"
            className="h-[17px] w-[17px] shrink-0 text-muted"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.3-4.3" strokeLinecap="round" />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search actions and pages"
            aria-label="Search actions and pages"
            className="min-w-0 flex-1 bg-transparent text-[14px] text-text placeholder:text-muted focus:outline-none"
          />
          <Kbd>Esc</Kbd>
        </div>

        <div ref={listRef} className="max-h-[52vh] overflow-y-auto py-2">
          {filtered.length === 0 ? (
            <div className="px-4 py-8 text-center text-[13px] text-muted">No matches</div>
          ) : (
            groups.map((group) => (
              <div key={group.name} className="mb-1 last:mb-0">
                <div className="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">
                  {group.name}
                </div>
                {group.items.map(({ cmd, index }) => {
                  const selected = index === active;
                  return (
                    <button
                      key={cmd.id}
                      type="button"
                      data-index={index}
                      onMouseMove={() => setActive(index)}
                      onClick={() => run(cmd)}
                      aria-selected={selected}
                      className={`flex w-full items-center gap-3 px-4 py-2 text-left text-[13.5px] transition ${
                        selected ? "bg-accent-soft text-text" : "text-muted hover:text-text"
                      }`}
                    >
                      <span
                        className={`grid h-[17px] w-[17px] shrink-0 place-items-center ${
                          selected ? "text-accent" : "opacity-85"
                        }`}
                      >
                        {cmd.icon}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-medium">{cmd.label}</span>
                      {cmd.hint && (
                        <span className="flex shrink-0 gap-1">
                          {hintKeys(cmd.hint).map((k, i) => (
                            <Kbd key={i}>{k}</Kbd>
                          ))}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;
