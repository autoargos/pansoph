// Typed client for the agora API. The SSE chat is consumed with fetch and a stream
// reader, since the chat endpoint is a POST and the browser EventSource is GET only.

// The bearer token from the OIDC login. agora validates it and derives identity.
let _authToken: string | null = null;
export function setAuthToken(token: string | null): void {
  _authToken = token;
}
function authHeader(): Record<string, string> {
  return _authToken ? { authorization: `Bearer ${_authToken}` } : {};
}

export interface Identity {
  user: string;
  groups: string[];
  roles: string[];
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
    headers: { "content-type": "application/json", ...authHeader() },
    body: body(query, id),
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  const data = (await res.json()) as { results: SearchResult[] };
  return data.results;
}

export async function* chat(query: string, id: Identity): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeader() },
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

// --- connectors + health ---

export interface RunInfo {
  started_at: string | null;
  finished_at: string | null;
  status: string;
  docs_indexed: number;
  error: string | null;
  duration_seconds: number | null;
}

export interface Instance {
  id: string;
  connector: string;
  name: string;
  tenant_id: string;
  config: Record<string, unknown>;
  schedule_seconds: number;
  schedule_cron: string | null;
  scope_type: string;
  scope_ref: string | null;
  enabled: boolean;
  cursor: string | null;
  created_at: string | null;
  last_run: RunInfo | null;
}

async function jget<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: authHeader() });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return (await res.json()) as T;
}

export async function connectorTypes(): Promise<string[]> {
  return (await jget<{ available: string[] }>("/api/connectors")).available;
}

// A connector's declared config schema, surfaced by the backend so the Add form can be
// rendered from the source of truth instead of a hardcoded map. See the ConnectorSpec /
// FieldKind dataclasses in pansoph-connector-sdk.
export type SpecFieldKind =
  | "string"
  | "int"
  | "bool"
  | "list"
  | "secret"
  | "secret_file"
  | "oauth";

export interface SpecField {
  key: string;
  label: string;
  kind: SpecFieldKind;
  required: boolean;
  help?: string;
  placeholder?: string;
}

export interface ConnectorSpec {
  name: string;
  title: string;
  description: string;
  fields: SpecField[];
}

export async function connectorSpecs(): Promise<ConnectorSpec[]> {
  return (await jget<{ connectors: ConnectorSpec[] }>("/api/connectors/specs")).connectors;
}

export async function listInstances(): Promise<Instance[]> {
  return (await jget<{ instances: Instance[] }>("/api/connectors/instances")).instances;
}

export async function instanceRuns(id: string): Promise<RunInfo[]> {
  return (await jget<{ runs: RunInfo[] }>(`/api/connectors/instances/${id}/runs`)).runs;
}

export async function createInstance(payload: {
  connector: string;
  name: string;
  instance_id: string;
  tenant_id: string;
  config: Record<string, unknown>;
  schedule_seconds: number;
  schedule_cron?: string;
  scope_type?: string;
  scope_ref?: string;
}): Promise<Instance> {
  const res = await fetch("/api/connectors/instances", {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeader() },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `create failed: ${res.status}`);
  }
  return (await res.json()) as Instance;
}

export async function deleteInstance(id: string): Promise<void> {
  const res = await fetch(`/api/connectors/instances/${id}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export async function syncInstance(id: string): Promise<{ status: string; docs_indexed: number; error?: string }> {
  const res = await fetch(`/api/connectors/instances/${id}/sync`, {
    method: "POST",
    headers: authHeader(),
  });
  return (await res.json()) as { status: string; docs_indexed: number; error?: string };
}

// Start an OAuth connect flow. The authorize endpoint is bearer gated (connecting a
// source provisions a credential), so the app fetches the provider consent URL with the
// token and then navigates the browser to it. A plain link would 401 because a top level
// navigation cannot carry the token.
export async function oauthAuthorizeUrl(
  provider: string,
  connector: string,
  instance: string,
  tenant: string,
): Promise<string> {
  const query =
    `?connector=${encodeURIComponent(connector)}` +
    `&instance=${encodeURIComponent(instance)}` +
    `&tenant=${encodeURIComponent(tenant)}`;
  const res = await fetch(`/api/oauth/${encodeURIComponent(provider)}/authorize${query}`, {
    headers: authHeader(),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `could not start connect (${res.status})`);
  }
  const data = (await res.json()) as { authorize_url: string };
  return data.authorize_url;
}

export interface Health {
  ok: boolean;
  checks: Record<string, { ok: boolean; latency_ms?: number; error?: string }>;
}

export async function health(): Promise<Health> {
  return jget<Health>("/api/health");
}

// ------------------------------------------------------------
// Stable aliases. The page layer refers to these connector/system
// functions by intent-revealing names. They forward to the wire
// functions above so pages never have to edit this file. Keep both
// the alias and the underlying name exported.
// ------------------------------------------------------------

/** List every configured connector instance. Alias of listInstances. */
export const listConnectorInstances = listInstances;

/** Fetch each connector's declared config spec. Alias of connectorSpecs. */
export const getConnectorSpecs = connectorSpecs;

/** Service health snapshot for the System page. Alias of health. */
export const systemHealth = health;

/** Available adapters per port group. Alias of adapters. */
export const listAdapters = adapters;

/** Backend build/runtime metadata. Alias of meta. */
export const systemMeta = meta;

/**
 * The signed-in user's identity, as derived from the OIDC token by auth.ts.
 * Identity is owned by the auth layer (auth.ts builds it from the token claims),
 * so this re-exports the Identity type and a small helper to shape one from the
 * primitive claims. Pages that already receive `identity` as a prop should keep
 * using that; this exists so a page can reconstruct an Identity without importing
 * auth.ts directly.
 */
export function makeIdentity(
  user: string,
  groups: string[],
  roles: string[],
  tenant_id = "default",
): Identity {
  return { user, groups, roles, tenant_id };
}
