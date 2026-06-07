import { useEffect, useState } from "react";

import { adapters, health, type Health, meta, type Meta } from "../api.ts";
import { Badge, Card, Dot, PageHeader } from "../ui.tsx";

const ACTIVE_KEY: Record<string, string> = {
  embedders: "embedder",
  rerankers: "reranker",
  llms: "llm",
  vectorstores: "vector",
  parsers: "parser",
};

export default function System() {
  const [ports, setPorts] = useState<Record<string, string[]>>({});
  const [info, setInfo] = useState<Meta | null>(null);
  const [hp, setHp] = useState<Health | null>(null);

  useEffect(() => {
    Promise.all([adapters(), meta(), health()])
      .then(([p, m, h]) => {
        setPorts(p);
        setInfo(m);
        setHp(h);
      })
      .catch(() => {});
  }, []);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="System" subtitle="Every role is a replaceable port. This is what is wired right now." />

      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm">
        {info && (
          <>
            <Badge kind="neutral">pansoph {info.version}</Badge>
            <Badge kind="neutral">env: {info.env}</Badge>
            {info.cloud_model ? (
              <Badge kind="warn">cloud model in use</Badge>
            ) : (
              <Badge kind="ok">local model</Badge>
            )}
          </>
        )}
      </div>

      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-faint">Service health</h2>
      <Card className="mb-7 divide-y divide-line">
        {hp ? (
          Object.entries(hp.checks).map(([name, c]) => (
            <div key={name} className="flex items-center justify-between px-5 py-3 text-sm">
              <span className="flex items-center gap-2">
                <Dot kind={c.ok ? "ok" : "err"} />
                {name}
              </span>
              <span className="text-xs text-muted">
                {c.ok ? `${c.latency_ms ?? "?"} ms` : (c.error ?? "unreachable")}
              </span>
            </div>
          ))
        ) : (
          <div className="px-5 py-3 text-sm text-muted">health unavailable</div>
        )}
      </Card>

      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-faint">Ports and adapters</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {Object.entries(ports).map(([group, names]) => {
          const port = group.replace("pansoph.", "");
          const active = info?.adapters[ACTIVE_KEY[port] ?? port];
          return (
            <Card key={group} className="p-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{port}</div>
              <div className="flex flex-wrap gap-1.5">
                {names.length === 0 && <span className="text-xs text-faint">none installed</span>}
                {names.map((n) => (
                  <span
                    key={n}
                    className={`rounded-md px-2 py-0.5 text-xs ${
                      n === active
                        ? "bg-accent text-white"
                        : "bg-canvas text-muted ring-1 ring-line"
                    }`}
                  >
                    {n}
                  </span>
                ))}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
