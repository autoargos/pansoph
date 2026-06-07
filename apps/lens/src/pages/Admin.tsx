import { useEffect, useState } from "react";

import { adapters, meta, type Meta } from "../api.ts";

// The "everything replaceable" view: which adapter is active for each port, and what
// other adapters are installed and could be swapped in by configuration.
export default function Admin() {
  const [ports, setPorts] = useState<Record<string, string[]>>({});
  const [info, setInfo] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([adapters(), meta()])
      .then(([p, m]) => {
        setPorts(p);
        setInfo(m);
      })
      .catch((err) => setError(String(err)));
  }, []);

  return (
    <div>
      {info && (
        <div className="mb-6 flex items-center gap-3 text-sm">
          <span className="rounded bg-neutral-200 px-2 py-1">pansoph {info.version}</span>
          <span className="rounded bg-neutral-200 px-2 py-1">env: {info.env}</span>
          {info.cloud_model && (
            <span className="rounded bg-amber-100 px-2 py-1 text-amber-800">
              cloud model in use
            </span>
          )}
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      <h2 className="mb-3 text-sm font-semibold text-neutral-700">Ports and installed adapters</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {Object.entries(ports).map(([group, names]) => {
          const port = group.replace("pansoph.", "");
          const active = info?.adapters[portKey(port)];
          return (
            <div key={group} className="rounded border bg-white p-3">
              <div className="mb-1 text-xs font-semibold uppercase text-neutral-500">{port}</div>
              <div className="flex flex-wrap gap-1">
                {names.length === 0 && <span className="text-xs text-neutral-400">none</span>}
                {names.map((n) => (
                  <span
                    key={n}
                    className={`rounded px-2 py-0.5 text-xs ${
                      n === active ? "bg-blue-600 text-white" : "bg-neutral-100 text-neutral-700"
                    }`}
                  >
                    {n}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function portKey(port: string): string {
  // meta() reports adapter keys without the trailing 's' for some ports.
  const map: Record<string, string> = {
    embedders: "embedder",
    rerankers: "reranker",
    llms: "llm",
    vectorstores: "vector",
    parsers: "parser",
  };
  return map[port] ?? port;
}
