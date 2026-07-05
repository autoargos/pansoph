import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { chat, type Identity } from "../api.ts";
import { ConnectorLogo } from "../components/ConnectorLogo.tsx";
import { Avatar, Badge, Button, Card, StatusDot, Tooltip } from "../ui.tsx";

// ============================================================
// Chat / Ask
//
// A grounded question-and-answer experience. Every answer is
// synthesized only from sources the acting identity is permitted
// to read, and each claim carries an inline [n] citation that maps
// to a numbered source chip. Restricted sources wear a lock badge.
//
// Wire contract (see api.ts): chat() yields a stream of events.
//   { type: "source", id, doc_id, chunk_id }  -> a permitted source
//   { type: "text-delta", delta }             -> a token of the answer
// The doc_id looks like "github . org/acme" or "oncall-runbook.md",
// which is all the grounding metadata the stream gives us, so the
// connector + restriction cues below are derived from it.
// ============================================================

interface Source {
  id: number;
  docId: string;
  chunkId: string;
}

interface Msg {
  role: "user" | "assistant";
  text: string;
  sources: Source[];
  // Wall-clock the turn finished, for the "grounded in N sources" footer.
  done: boolean;
  error: boolean;
}

// A tiny catalog of source connector cues. The stream only hands us a
// doc_id string, so we read the connector prefix (before the first dot,
// e.g. "github . org/acme") and fall back to the file extension. This is
// presentation only; the permission decision already happened server side.
type ConnectorKey =
  | "github"
  | "notion"
  | "confluence"
  | "slack"
  | "drive"
  | "gmail"
  | "jira"
  | "web"
  | "doc";

interface ConnectorMeta {
  label: string;
  // The name handed to ConnectorLogo, which resolves it to a bundled brand
  // logo when one exists, or a neutral tile otherwise.
  logo: string;
}

const CONNECTORS: Record<ConnectorKey, ConnectorMeta> = {
  github: { label: "GitHub", logo: "github" },
  notion: { label: "Notion", logo: "notion" },
  confluence: { label: "Confluence", logo: "confluence" },
  slack: { label: "Slack", logo: "slack" },
  drive: { label: "Drive", logo: "google_drive" },
  gmail: { label: "Gmail", logo: "gmail" },
  jira: { label: "Jira", logo: "jira" },
  web: { label: "Web", logo: "weblink" },
  doc: { label: "Document", logo: "document" },
};

// Read the connector cue out of a doc_id. The ingestion side stamps most
// doc_ids as "<connector> . <ref>", so the token before the first dot is
// the most reliable signal; a file extension is the fallback.
function connectorFor(docId: string): ConnectorKey {
  const prefix = docId.split(/[.\s]/)[0]?.toLowerCase() ?? "";
  if (prefix in CONNECTORS && prefix !== "doc") return prefix as ConnectorKey;
  if (/\bgit(hub)?\b/i.test(docId)) return "github";
  if (/notion/i.test(docId)) return "notion";
  if (/conflu/i.test(docId)) return "confluence";
  if (/slack|#/.test(docId)) return "slack";
  if (/drive|gdoc/i.test(docId)) return "drive";
  if (/gmail|mail/i.test(docId)) return "gmail";
  if (/jira/i.test(docId)) return "jira";
  if (/^https?:|www\.|\.io\b|\.com\b/i.test(docId)) return "web";
  return "doc";
}

// A short human title for a source. Prefer the part after the connector
// prefix, strip a leading path, and keep it compact for the chip.
function titleFor(docId: string): string {
  const afterPrefix = docId.includes(" . ") ? docId.split(" . ").slice(1).join(" . ") : docId;
  const tail = afterPrefix.split("/").pop() ?? afterPrefix;
  return (tail || docId).trim();
}

// A source is treated as access-restricted for display when its doc_id
// carries a restriction hint. The real gate ran server side; this only
// surfaces the lock so the user can see which grounding was sensitive.
function isRestricted(docId: string): boolean {
  return /restrict|private|confidential|finance|leadership|legal|secret/i.test(docId);
}

// Render an assistant answer, turning [n] tokens into inline citation
// markers. Each marker is a button so it can scroll its source chip into
// view and flash it. onCite receives the 1-based citation number.
function AnswerBody({
  text,
  onCite,
  activeCite,
}: {
  text: string;
  onCite: (n: number) => void;
  activeCite: number | null;
}) {
  const out: ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(<span key={`t${key}`}>{text.slice(last, m.index)}</span>);
    const n = Number(m[1]);
    const on = activeCite === n;
    out.push(
      <button
        key={`c${key}`}
        type="button"
        onClick={() => onCite(n)}
        aria-label={`Jump to source ${n}`}
        className={`mx-0.5 -translate-y-1.5 rounded-[4px] px-1 align-super font-mono text-[10.5px] font-bold leading-none transition ${
          on
            ? "bg-accent text-white shadow-[0_0_10px_-1px_rgba(124,92,255,0.8)]"
            : "bg-accent-soft text-accent hover:bg-accent hover:text-white"
        }`}
      >
        {m[1]}
      </button>,
    );
    last = re.lastIndex;
    key += 1;
  }
  if (last < text.length) out.push(<span key={`t${key}`}>{text.slice(last)}</span>);
  return <p className="whitespace-pre-wrap text-[14.5px] leading-[1.65] text-text">{out}</p>;
}

// The pansoph owl-tile avatar for assistant turns, mirroring the mockup's
// gradient a-avatar with an accent glow ring.
function AssistantAvatar() {
  return (
    <span
      className="grid h-7 w-7 shrink-0 place-items-center rounded-[8px] text-white"
      style={{
        background: "linear-gradient(140deg, var(--ob-accent), #5b3ce0)",
        boxShadow: "0 0 0 1px var(--ob-accent-line)",
      }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2" className="h-[15px] w-[15px]">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 3v9l6.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

// A numbered, connector-aware source chip. Clicking it flashes the matching
// inline citation. Restricted sources wear a lock badge.
function SourceChip({
  source,
  active,
  flashed,
  onActivate,
  chipRef,
}: {
  source: Source;
  active: boolean;
  flashed: boolean;
  onActivate: (n: number) => void;
  chipRef: (el: HTMLButtonElement | null) => void;
}) {
  const conn = CONNECTORS[connectorFor(source.docId)];
  const restricted = isRestricted(source.docId);
  return (
    <button
      ref={chipRef}
      type="button"
      onClick={() => onActivate(source.id)}
      title={source.docId}
      className={`group inline-flex max-w-full items-center gap-2 rounded-[8px] border bg-bg px-2.5 py-1.5 text-left text-xs font-medium transition ${
        active || flashed
          ? "border-accent-line bg-accent-soft text-text shadow-[0_0_0_1px_var(--color-accent-line)]"
          : "border-border text-muted hover:border-accent-line hover:bg-accent-soft hover:text-text"
      }`}
    >
      <span className="grid h-4 min-w-[18px] place-items-center rounded-[4px] bg-accent-soft px-1 font-mono text-[10px] font-bold tabular-nums text-accent">
        {source.id}
      </span>
      <ConnectorLogo connector={conn.logo} size={18} />
      <span className="truncate">{titleFor(source.docId)}</span>
      {restricted ? (
        <Tooltip label="Access-restricted source. You can see it; not everyone can.">
          <span className="inline-flex items-center gap-1 rounded-[5px] border border-[rgba(245,158,11,0.3)] bg-warn-soft px-1 py-0.5 text-warn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-2.5 w-2.5">
              <rect x="4" y="10" width="16" height="11" rx="2" />
              <path d="M8 10V7a4 4 0 0 1 8 0v3" strokeLinecap="round" />
            </svg>
          </span>
        </Tooltip>
      ) : null}
    </button>
  );
}

const SUGGESTIONS = [
  "Summarize our on-call rotation and who owns incident escalation",
  "What is the vendor security review process before production access?",
  "Draft a reply to the latest customer escalation thread",
  "Which docs changed about the data retention policy this quarter?",
];

export default function Chat({ identity }: { identity: Identity }) {
  const [q, setQ] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  // The citation number currently flashing, keyed to the latest assistant turn.
  const [activeCite, setActiveCite] = useState<number | null>(null);

  const threadRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const chipRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  const flashTimer = useRef<number | null>(null);

  // Keep the newest turn in view as tokens stream in. Skip the empty state, otherwise
  // this scrolls the tall welcome hero up out of view on first paint.
  useEffect(() => {
    if (messages.length === 0) return;
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    return () => {
      if (flashTimer.current) window.clearTimeout(flashTimer.current);
    };
  }, []);

  // Grow the composer to fit up to a few lines, then let it scroll.
  function autosize() {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }

  function flashCite(n: number) {
    setActiveCite(n);
    const chip = chipRefs.current.get(n);
    if (chip) chip.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setActiveCite(null), 1600);
  }

  async function submit(query: string) {
    const trimmed = query.trim();
    if (!trimmed || busy) return;
    setQ("");
    setActiveCite(null);
    if (composerRef.current) composerRef.current.style.height = "auto";
    setBusy(true);
    setMessages((m) => [
      ...m,
      { role: "user", text: trimmed, sources: [], done: true, error: false },
      { role: "assistant", text: "", sources: [], done: false, error: false },
    ]);

    const update = (fn: (msg: Msg) => Msg) =>
      setMessages((m) => {
        const copy = m.slice();
        copy[copy.length - 1] = fn(copy[copy.length - 1]);
        return copy;
      });

    try {
      for await (const ev of chat(trimmed, identity)) {
        if (ev.type === "source" && ev.id != null) {
          update((msg) =>
            msg.sources.some((s) => s.id === ev.id)
              ? msg
              : {
                  ...msg,
                  sources: [
                    ...msg.sources,
                    { id: ev.id!, docId: ev.doc_id ?? "", chunkId: ev.chunk_id ?? "" },
                  ],
                },
          );
        } else if (ev.type === "text-delta" && ev.delta) {
          update((msg) => ({ ...msg, text: msg.text + ev.delta }));
        }
      }
      update((msg) => ({ ...msg, done: true }));
    } catch (err) {
      update((msg) => ({
        ...msg,
        done: true,
        error: true,
        text: msg.text || `Could not reach agora. ${String(err)}`,
      }));
    } finally {
      setBusy(false);
      composerRef.current?.focus();
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void submit(q);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit(q);
    }
  }

  const started = messages.length > 0;

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col">
      {/* ---- Header strip: what this surface is, and the grounding promise ---- */}
      <div className="mb-5 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-1.5 flex items-center gap-1.5 font-mono text-xs text-muted">
            <span>lens</span>
            <span className="opacity-50">/</span>
            <span>ask</span>
          </div>
          <h1 className="text-xl font-bold tracking-[-0.03em] text-text">Ask</h1>
        </div>
        <Badge kind="cyan" dot className="shrink-0">
          grounded in permitted sources
        </Badge>
      </div>

      {/* ---- Thread ---- */}
      <div
        ref={threadRef}
        className="min-h-0 flex-1 space-y-6 overflow-y-auto pb-2 pr-1"
        aria-live="polite"
      >
        {!started ? (
          <EmptyAsk user={identity.user} onPick={(s) => void submit(s)} busy={busy} />
        ) : (
          messages.map((msg, i) =>
            msg.role === "user" ? (
              <div key={i} className="flex items-start justify-end gap-3">
                <div className="max-w-[82%] rounded-[14px] rounded-br-[4px] border border-accent-line bg-accent-soft px-4 py-2.5 text-[14px] font-medium leading-relaxed text-text">
                  {msg.text}
                </div>
                <Avatar name={identity.user} size={28} className="mt-0.5" />
              </div>
            ) : (
              <div key={i} className="flex items-start gap-3">
                <AssistantAvatar />
                <div className="min-w-0 flex-1">
                  <Card className="bg-gradient-to-b from-surface to-[color-mix(in_srgb,var(--ob-surface)_55%,var(--ob-bg))] px-5 py-4">
                    {msg.text ? (
                      <AnswerBody
                        text={msg.text}
                        onCite={flashCite}
                        activeCite={i === messages.length - 1 ? activeCite : null}
                      />
                    ) : (
                      <ThinkingLine />
                    )}

                    {/* streaming caret while the last turn is still writing */}
                    {msg.text && !msg.done && (
                      <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-accent align-middle" />
                    )}

                    {msg.sources.length > 0 && (
                      <div className="mt-4 border-t border-border pt-3.5">
                        <div className="mb-2 flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5 text-cyan">
                            <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" strokeLinejoin="round" />
                            <path d="M14 3v6h6" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                          Grounded in {msg.sources.length}{" "}
                          {msg.sources.length === 1 ? "source" : "sources"}
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {msg.sources.map((s) => (
                            <SourceChip
                              key={s.chunkId || s.id}
                              source={s}
                              active={i === messages.length - 1 && activeCite === s.id}
                              flashed={i === messages.length - 1 && activeCite === s.id}
                              onActivate={flashCite}
                              chipRef={(el) => {
                                if (i === messages.length - 1) {
                                  if (el) chipRefs.current.set(s.id, el);
                                  else chipRefs.current.delete(s.id);
                                }
                              }}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                  </Card>

                  {msg.error && (
                    <div className="mt-2 flex items-center gap-1.5 px-1 text-xs text-err">
                      <StatusDot kind="err" size={6} />
                      The answer stream was interrupted. Your access was never widened.
                    </div>
                  )}
                </div>
              </div>
            ),
          )
        )}
      </div>

      {/* ---- Composer ---- */}
      <form onSubmit={onSubmit} className="sticky bottom-0 pt-3">
        <div className="flex items-end gap-2.5 rounded-[12px] border border-border bg-bg p-2 pl-3.5 transition focus-within:border-accent-line">
          <textarea
            ref={composerRef}
            value={q}
            rows={1}
            onChange={(e) => {
              setQ(e.target.value);
              autosize();
            }}
            onKeyDown={onKeyDown}
            placeholder="Ask across everything you can see..."
            className="max-h-[168px] min-h-[24px] flex-1 resize-none self-center bg-transparent py-1.5 text-[14px] leading-relaxed text-text outline-none placeholder:text-muted"
          />
          <Button
            type="submit"
            size="sm"
            disabled={busy || !q.trim()}
            aria-label="Send"
            className="h-9 w-9 rounded-[9px] p-0"
          >
            {busy ? (
              <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
                <path d="m22 2-7 20-4-9-9-4Z" strokeLinejoin="round" />
                <path d="M22 2 11 13" strokeLinecap="round" />
              </svg>
            )}
          </Button>
        </div>
        <div className="mt-2 flex items-center justify-between gap-3 px-1 text-[11px] text-muted">
          <span className="inline-flex items-center gap-1.5">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3 w-3 text-ok">
              <path d="M12 2 4 5v6c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V5z" strokeLinejoin="round" />
              <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Answers are drawn only from what{" "}
            <span className="font-medium text-text">{identity.user}</span> is permitted to read.
          </span>
          <span className="hidden shrink-0 items-center gap-1 sm:inline-flex">
            <kbd className="rounded-[5px] border border-border bg-surface px-1.5 py-0.5 font-mono text-[10px] leading-none text-muted">
              Enter
            </kbd>
            to send
          </span>
        </div>
      </form>
    </div>
  );
}

// The animated "reading sources" line shown before the first token lands.
function ThinkingLine() {
  return (
    <div className="flex items-center gap-2.5 text-sm text-muted">
      <span className="flex gap-1">
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent [animation-delay:-0.2s]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent [animation-delay:-0.1s]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent" />
      </span>
      Retrieving permitted sources and grounding the answer
    </div>
  );
}

// The empty state. A confident headline, the grounding promise, and a set
// of one-tap starter questions so the surface is never a dead blank.
function EmptyAsk({
  user,
  onPick,
  busy,
}: {
  user: string;
  onPick: (s: string) => void;
  busy: boolean;
}) {
  return (
    // items centered horizontally; my-auto on the inner block centers it vertically when
    // it fits and lets the top stay reachable (scroll) when it does not. Plain
    // justify-center clips the top of tall content in an overflow container.
    <div className="flex min-h-full flex-col items-center py-8 text-center">
      <div className="my-auto flex w-full flex-col items-center">
      <span
        className="mb-5 grid h-14 w-14 place-items-center rounded-[16px] text-white"
        style={{
          background: "linear-gradient(140deg, var(--ob-accent), #5b3ce0)",
          boxShadow: "0 0 0 1px var(--ob-accent-line), 0 16px 40px -14px rgba(124,92,255,0.7)",
        }}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8" className="h-7 w-7">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" strokeLinejoin="round" />
        </svg>
      </span>

      <h2 className="text-[22px] font-bold tracking-[-0.03em] text-text">
        Ask across everything you can see
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">
        One question, every connected source. Each answer cites where it came from, and it is drawn
        only from documents{" "}
        <span className="font-medium text-text">{user}</span> is already permitted to read. Nothing
        outside your access reaches the model.
      </p>

      <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
        <Badge kind="ok" dot>
          permission-aware
        </Badge>
        <Badge kind="accent" dot>
          cited answers
        </Badge>
        <Badge kind="cyan" dot>
          self-hosted
        </Badge>
      </div>

      <div className="mt-8 w-full max-w-lg space-y-2 text-left">
        <div className="px-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-muted">
          Try asking
        </div>
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={busy}
            onClick={() => onPick(s)}
            className="group flex w-full items-center gap-3 rounded-[10px] border border-border bg-surface px-3.5 py-3 text-left text-[13.5px] text-text transition hover:border-accent-line hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-[7px] border border-border bg-bg text-muted transition group-hover:border-accent-line group-hover:text-accent">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5">
                <path d="M12 3v18M3 12h18" strokeLinecap="round" />
              </svg>
            </span>
            <span className="flex-1">{s}</span>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="h-4 w-4 -translate-x-1 text-muted opacity-0 transition group-hover:translate-x-0 group-hover:text-accent group-hover:opacity-100"
            >
              <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        ))}
      </div>
      </div>
    </div>
  );
}
