import { type FormEvent, useState } from "react";

import { type Identity, search, type SearchResult } from "../api.ts";
import { Badge, Button, Card, PageHeader, Spinner } from "../ui.tsx";

export default function Search({ identity }: { identity: Identity }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [ran, setRan] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Search"
        subtitle="Permission-aware. Results are filtered by what the acting user may read, before you ever see them."
      />
      <form onSubmit={run} className="flex gap-2">
        <input
          className="flex-1 rounded-xl border border-line bg-surface px-4 py-3 text-sm outline-none focus:border-accent"
          placeholder="Search permitted documents..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Button type="submit" disabled={busy} className="px-5">
          {busy ? <Spinner className="border-white/40 border-t-white" /> : "Search"}
        </Button>
      </form>

      {error && <p className="mt-4 text-sm text-err">{error}</p>}

      <ul className="mt-6 space-y-3">
        {results.map((r) => (
          <Card key={r.chunk_id} className="px-5 py-4">
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <span className="truncate font-mono text-xs text-muted">{r.doc_id}</span>
              <Badge kind="neutral">score {r.score.toFixed(2)}</Badge>
            </div>
            <p className="text-sm leading-relaxed text-ink-soft">{r.text}</p>
          </Card>
        ))}
      </ul>

      {ran && results.length === 0 && !busy && (
        <Card className="mt-6 p-8 text-center text-sm text-muted">
          No permitted results for <span className="font-medium text-ink">{identity.user}</span>.
        </Card>
      )}
    </div>
  );
}
