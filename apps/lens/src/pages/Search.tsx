import type { FormEvent } from "react";
import { useState } from "react";

import { search, type Identity, type SearchResult } from "../api.ts";

// Search is distinct from chat: an answer-first list of permitted chunks with their
// scores. The results are already filtered by the authz bulk check.
export default function Search({ identity }: { identity: Identity }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ran, setRan] = useState(false);

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setResults(await search(q, identity));
      setRan(true);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <form onSubmit={run} className="flex gap-2">
        <input
          className="flex-1 rounded border px-3 py-2"
          placeholder="Search permitted documents..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button
          className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
          disabled={busy}
        >
          {busy ? "..." : "Search"}
        </button>
      </form>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <ul className="mt-6 space-y-3">
        {results.map((r) => (
          <li key={r.chunk_id} className="rounded border bg-white p-4">
            <div className="mb-1 flex items-center justify-between">
              <span className="font-mono text-xs text-neutral-500">{r.doc_id}</span>
              <span className="text-xs text-neutral-400">score {r.score.toFixed(3)}</span>
            </div>
            <p className="text-sm">{r.text}</p>
          </li>
        ))}
      </ul>

      {ran && results.length === 0 && !busy && (
        <p className="mt-6 text-sm text-neutral-500">No permitted results.</p>
      )}
    </div>
  );
}
