import type { FormEvent } from "react";
import { useState } from "react";

import { chat, type Identity } from "../api.ts";

interface Source {
  id: number;
  doc_id: string;
  chunk_id: string;
}

// Verification first: the answer streams in, citation markers [n] become pills that
// link to the sources rail, and a user only ever sees sources they are permitted to
// retrieve (the backend enforces that).
export default function Chat({ identity }: { identity: Identity }) {
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(e: FormEvent) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    setAnswer("");
    setSources([]);
    setError(null);
    try {
      for await (const ev of chat(q, identity)) {
        if (ev.type === "source" && ev.id != null) {
          const id = ev.id;
          setSources((s) => [...s, { id, doc_id: ev.doc_id ?? "", chunk_id: ev.chunk_id ?? "" }]);
        } else if (ev.type === "text-delta" && ev.delta) {
          setAnswer((a) => a + ev.delta);
        }
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_18rem]">
      <div>
        <form onSubmit={ask} className="flex gap-2">
          <input
            className="flex-1 rounded border px-3 py-2"
            placeholder="Ask across your connected knowledge..."
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button
            className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
            disabled={busy}
          >
            {busy ? "..." : "Ask"}
          </button>
        </form>

        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

        {answer && (
          <div className="mt-6 whitespace-pre-wrap rounded border bg-white p-4 leading-relaxed">
            {renderWithCitations(answer)}
          </div>
        )}
        {!answer && !busy && !error && (
          <p className="mt-6 text-sm text-neutral-500">
            Answers cite their sources. When the answer comes from world knowledge, no
            citation is shown.
          </p>
        )}
      </div>

      <aside>
        <h2 className="mb-2 text-sm font-semibold text-neutral-700">Sources</h2>
        {sources.length === 0 ? (
          <p className="text-xs text-neutral-500">Permitted sources appear here.</p>
        ) : (
          <ul className="space-y-2">
            {sources.map((s) => (
              <li key={s.id} className="rounded border bg-white p-2 text-xs">
                <span className="mr-1 inline-flex h-4 w-4 items-center justify-center rounded-full bg-blue-100 text-blue-700">
                  {s.id}
                </span>
                <span className="font-mono">{s.doc_id}</span>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}

function renderWithCitations(text: string) {
  // Turn [n] markers into small pills.
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    if (m) {
      return (
        <sup
          key={i}
          className="mx-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-blue-100 text-[10px] text-blue-700"
        >
          {m[1]}
        </sup>
      );
    }
    return <span key={i}>{part}</span>;
  });
}
