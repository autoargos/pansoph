import { useState } from "react";

// ============================================================
// ConnectorLogo
//
// One place that turns a connector name into a recognizable brand tile.
// Official brand SVGs live in /public/logos/<name>.svg and are served
// at /logos/<name>.svg (kept off the /connectors route path so the SPA route
// is not shadowed by a real directory). When a logo exists we render it on a
// light tile so brand colors (and near-black marks like GitHub or Notion)
// stay legible in both the dark and light themes. When no logo exists we
// fall back to a tasteful neutral tile: a globe for web links, otherwise a
// simple plug glyph or the connector's first initial.
//
// Usage: <ConnectorLogo connector="github" size={42} />
// ============================================================

// Connectors that have a bundled official brand SVG in /public/connectors.
// Exported so callers (and tests) can reason about which names are covered.
export const LOGO_CONNECTORS = [
  "github",
  "slack",
  "notion",
  "confluence",
  "jira",
  "google_drive",
  "gmail",
  "dropbox",
  "salesforce",
  "sharepoint",
  "teams",
  "linear",
  "zendesk",
  "mcp",
] as const;

export type LogoConnector = (typeof LOGO_CONNECTORS)[number];

const LOGO_SET = new Set<string>(LOGO_CONNECTORS);

// Map the many names a connector can arrive under (spec name, doc_id prefix,
// fuzzy label) to the canonical logo file name. Anything not resolved here
// falls through to a neutral tile.
const ALIASES: Array<[RegExp, LogoConnector]> = [
  [/github|gh\b/, "github"],
  [/slack/, "slack"],
  [/notion/, "notion"],
  [/conflu/, "confluence"],
  [/jira/, "jira"],
  [/(google.?drive|gdrive|gdoc|^drive$|\bdrive\b)/, "google_drive"],
  [/(gmail|google.?mail)/, "gmail"],
  [/dropbox/, "dropbox"],
  [/salesforce|\bsfdc\b/, "salesforce"],
  [/sharepoint/, "sharepoint"],
  [/(msteams|ms.?teams|\bteams\b)/, "teams"],
  [/linear/, "linear"],
  [/zendesk/, "zendesk"],
  [/(mcp|relay)/, "mcp"],
];

// Resolve a connector name to the logo file it should use, or null when we
// have no bundled logo for it. Exact matches win before fuzzy aliases.
export function resolveLogo(connector: string): LogoConnector | null {
  const n = connector.trim().toLowerCase();
  if (LOGO_SET.has(n)) return n as LogoConnector;
  for (const [re, name] of ALIASES) if (re.test(n)) return name;
  return null;
}

// Whether a name looks like a web link / public URL source, which gets a
// globe rather than a generic document tile.
function isWebLink(connector: string): boolean {
  return /(weblink|web|public|link|url|fetch|http)/.test(connector.trim().toLowerCase());
}

function GlobeGlyph({ size }: { size: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.6 3.9 5.7 4 9-.1 3.3-1.5 6.4-4 9-2.5-2.6-3.9-5.7-4-9 .1-3.3 1.5-6.4 4-9Z" />
    </svg>
  );
}

function PlugGlyph({ size }: { size: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M9 2v6M15 2v6" />
      <path d="M7 8h10v3a5 5 0 0 1-10 0z" />
      <path d="M12 16v6" />
    </svg>
  );
}

export function ConnectorLogo({
  connector,
  size = 42,
}: {
  connector: string;
  size?: number;
}) {
  const logo = resolveLogo(connector);
  // If the file 404s at runtime, fall back to the neutral tile rather than a
  // broken image.
  const [failed, setFailed] = useState(false);

  // The bundled brand logo, on a white tile so every brand color (including
  // near-black marks) reads in both themes.
  if (logo && !failed) {
    return (
      <span
        className="grid shrink-0 place-items-center overflow-hidden rounded-[var(--radius-md)] border border-border"
        style={{ width: size, height: size, background: "#ffffff" }}
      >
        <img
          src={`/logos/${logo}.svg`}
          alt={connector}
          width={Math.round(size * 0.55)}
          height={Math.round(size * 0.55)}
          onError={() => setFailed(true)}
          style={{ width: size * 0.55, height: size * 0.55, objectFit: "contain" }}
        />
      </span>
    );
  }

  // Neutral fallback tile for connectors without a bundled logo.
  const glyphSize = size * 0.5;
  const initial = (connector.trim().slice(0, 1) || "?").toUpperCase();
  const web = isWebLink(connector);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-[var(--radius-md)] border border-border bg-elevated text-muted"
      style={{ width: size, height: size }}
    >
      {web ? (
        <GlobeGlyph size={glyphSize} />
      ) : /^[a-z0-9]/i.test(connector.trim()) ? (
        <span
          className="font-semibold leading-none text-text"
          style={{ fontSize: Math.round(size * 0.42) }}
        >
          {initial}
        </span>
      ) : (
        <PlugGlyph size={glyphSize} />
      )}
    </span>
  );
}

export default ConnectorLogo;
