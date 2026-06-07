// Typed client for the agora API. The SSE chat is consumed with fetch and a stream
// reader, since the chat endpoint is a POST and the browser EventSource is GET only.

export interface Identity {
  user: string;
  groups: string[];
  tenant_id: string;
}

export interface SearchResult {
  doc_id: string;
  chunk_id: string;
  score: number;
  text: string | null;
}

export interface ChatEvent {
  type: string;
  id?: number;
  doc_id?: string;
  chunk_id?: string;
  delta?: string;
}

function body(query: string, id: Identity): string {
  return JSON.stringify({
    query,
    user: id.user,
    groups: id.groups,
    tenant_id: id.tenant_id,
  });
}

export async function search(query: string, id: Identity): Promise<SearchResult[]> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body(query, id),
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  const data = (await res.json()) as { results: SearchResult[] };
  return data.results;
}

export async function* chat(query: string, id: Identity): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body(query, id),
  });
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; agora emits CRLF, so accept both.
    const frames = buf.split(/\r?\n\r?\n/);
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split(/\r?\n/).find((l) => l.startsWith("data:"));
      if (!line) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try {
        yield JSON.parse(payload) as ChatEvent;
      } catch {
        // ignore keep-alive or malformed frames
      }
    }
  }
}

export async function adapters(): Promise<Record<string, string[]>> {
  const res = await fetch("/api/adapters");
  if (!res.ok) throw new Error(`adapters failed: ${res.status}`);
  return (await res.json()) as Record<string, string[]>;
}

export interface Meta {
  version: string;
  env: string;
  cloud_model: boolean;
  adapters: Record<string, string>;
}

export async function meta(): Promise<Meta> {
  const res = await fetch("/api/meta");
  if (!res.ok) throw new Error(`meta failed: ${res.status}`);
  return (await res.json()) as Meta;
}
