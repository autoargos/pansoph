import { type FormEvent, type ReactNode, useState } from "react";

import { chat, type Identity } from "../api.ts";
import { Button, Card, Spinner } from "../ui.tsx";

interface Source {
  id: number;
  doc_id: string;
  chunk_id: string;
}

interface Msg {
  role: "user" | "assistant";
  text: string;
  sources: Source[];
}

function renderWithCitations(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <sup
        key={`c${key++}`}
        className="mx-0.5 rounded bg-accent-soft px-1 py-0.5 text-[10px] font-semibold text-accent-ink"
      >
        {m[1]}
      </sup>,
    );
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Chat({ identity }: { identity: Identity }) {
  const [q, setQ] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);

  async function ask(e: FormEvent) {
    e.preventDefault();
    const query = q.trim();
    if (!query || busy) return;
    setQ("");
    setBusy(true);
    setMessages((m) => [
      ...m,
      { role: "user", text: query, sources: [] },
      { role: "assistant", text: "", sources: [] },
    ]);
    const update = (fn: (msg: Msg) => Msg) =>
      setMessages((m) => {
        const copy = m.slice();
        copy[copy.length - 1] = fn(copy[copy.length - 1]);
        return copy;
      });
    try {
      for await (const ev of chat(query, identity)) {
        if (ev.type === "source" && ev.id != null) {
          update((msg) => ({
            ...msg,
            sources: [
              ...msg.sources,
              { id: ev.id!, doc_id: ev.doc_id ?? "", chunk_id: ev.chunk_id ?? "" },
            ],
          }));
        } else if (ev.type === "text-delta" && ev.delta) {
          update((msg) => ({ ...msg, text: msg.text + ev.delta }));
        }
      }
    } catch (err) {
      update((msg) => ({ ...msg, text: `Could not reach agora: ${String(err)}` }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col">
      <div className="flex-1 space-y-5 pb-4">
        {messages.length === 0 && (
          <Card className="p-8 text-center">
            <p className="text-sm text-muted">
              Ask about your connected knowledge. Answers cite their sources, and you only ever see
              what <span className="font-medium text-ink">{identity.user}</span> is permitted to.
            </p>
          </Card>
        )}
        {messages.map((msg, i) =>
          msg.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-ink px-4 py-2.5 text-sm text-white">
                {msg.text}
              </div>
            </div>
          ) : (
            <div key={i} className="space-y-2">
              <Card className="px-5 py-4">
                <div className="text-[15px] leading-relaxed text-ink-soft">
                  {msg.text ? renderWithCitations(msg.text) : <Spinner />}
                </div>
              </Card>
              {msg.sources.length > 0 && (
                <div className="flex flex-wrap gap-2 px-1">
                  {msg.sources.map((s) => (
                    <span
                      key={s.chunk_id}
                      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-muted"
                    >
                      <span className="rounded bg-accent-soft px-1 text-[10px] font-semibold text-accent-ink">
                        {s.id}
                      </span>
                      <span className="font-mono text-[11px]">{s.doc_id}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          ),
        )}
      </div>

      <form onSubmit={ask} className="sticky bottom-0 flex gap-2 bg-canvas py-3">
        <input
          className="flex-1 rounded-xl border border-line bg-surface px-4 py-3 text-sm outline-none focus:border-accent"
          placeholder="Ask a question..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Button type="submit" disabled={busy} className="px-5">
          {busy ? <Spinner className="border-white/40 border-t-white" /> : "Ask"}
        </Button>
      </form>
    </div>
  );
}
