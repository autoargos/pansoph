import type { ReactNode, SVGProps } from "react";

// Brand and semantic glyphs for the Connectors page. Kept in their own module so the
// page body stays focused on behavior. Each connector name maps to a tinted logo tile;
// unknown connectors fall back to a neutral plug mark. OAuth providers carry their own
// brand mark and brand color for the "Connect with X" button.

type Glyph = (p: SVGProps<SVGSVGElement>) => ReactNode;

// --- generic UI glyphs (stroke, inherit currentColor) ---

const s = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function IconRuns(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M3 3v18h18" />
      <path d="m7 14 3-3 3 3 4-5" />
    </svg>
  );
}

export function IconEdit(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

export function IconSync(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
      <path d="M21 3v5h-5" />
    </svg>
  );
}

export function IconClock(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

export function IconShield(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M12 2 4 5v6c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V5z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

export function IconLock(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

export function IconTrash(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}

export function IconPlus(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} strokeWidth={2} {...p}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function IconAlert(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M12 9v4M12 17h.01" />
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    </svg>
  );
}

export function IconGrid(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <path d="M17.5 14v7M14 17.5h7" />
    </svg>
  );
}

export function IconEye(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function IconEyeOff(p: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" {...s} {...p}>
      <path d="M9.9 4.2A9.5 9.5 0 0 1 12 4c6.5 0 10 7 10 7a17 17 0 0 1-3 3.8M6 6.3A17 17 0 0 0 2 11s3.5 7 10 7a9.5 9.5 0 0 0 4-.9" />
      <path d="m2 2 20 20" />
      <path d="M9.5 9.5a3 3 0 0 0 4.2 4.2" />
    </svg>
  );
}

// --- connector brand glyphs (may use their own fills) ---

const GH: Glyph = (p) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
    <path d="M12 .5A11.5 11.5 0 0 0 .5 12a11.5 11.5 0 0 0 7.86 10.92c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.53-1.35-1.3-1.7-1.3-1.7-1.06-.72.08-.71.08-.71 1.17.08 1.78 1.2 1.78 1.2 1.04 1.78 2.73 1.27 3.4.97.1-.75.4-1.27.73-1.56-2.55-.29-5.24-1.28-5.24-5.68 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.04 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.58.23 2.75.11 3.04.74.81 1.19 1.83 1.19 3.09 0 4.41-2.69 5.38-5.25 5.67.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12 11.5 11.5 0 0 0 12 .5Z" />
  </svg>
);

const NOTION: Glyph = (p) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
    <path d="M4.5 3.7 15 3c1.3-.1 1.6 0 2.4.6l2.1 1.5c.5.4.7.5.7 1v13.2c0 .9-.3 1.4-1.4 1.5l-11.3.7c-.8 0-1.2-.1-1.6-.6l-1.6-2.1c-.4-.6-.6-1-.6-1.6V5c0-.7.3-1.2 1.4-1.3ZM15.3 6.3c.1.5 0 .7-.5.7l-8.9.5c-.4 0-.5-.2-.4-.5l.5-.6 8.4-.5c.4 0 .7.1.9.4Zm.4 2.2v9.6c0 .5-.2.7-.9.6l-.9-.2V9.7l-1.9-.1v8.2l-.9-.1c-.5 0-.7-.3-.7-.8V8.6c0-.4.1-.7.7-.7l3.8-.2c.9-.1 1.7.2 1.7 1.3Z" />
  </svg>
);

const SLACK: Glyph = (p) => (
  <svg viewBox="0 0 24 24" {...p}>
    <path fill="#E01E5A" d="M6 15.2a2.1 2.1 0 1 1-2.1-2.1H6zM7.1 15.2a2.1 2.1 0 0 1 4.2 0v5.2a2.1 2.1 0 1 1-4.2 0z" />
    <path fill="#36C5F0" d="M8.8 6a2.1 2.1 0 1 1 2.1-2.1V6zM8.8 7.1a2.1 2.1 0 0 1 0 4.2H3.6a2.1 2.1 0 1 1 0-4.2z" />
    <path fill="#2EB67D" d="M18 8.8a2.1 2.1 0 1 1 2.1 2.1H18zM16.9 8.8a2.1 2.1 0 0 1-4.2 0V3.6a2.1 2.1 0 1 1 4.2 0z" />
    <path fill="#ECB22E" d="M15.2 18a2.1 2.1 0 1 1-2.1 2.1V18zM15.2 16.9a2.1 2.1 0 0 1 0-4.2h5.2a2.1 2.1 0 1 1 0 4.2z" />
  </svg>
);

const ATLASSIAN: Glyph = (p) => (
  <svg viewBox="0 0 24 24" {...p}>
    <defs>
      <linearGradient id="atl-g" x1="1" y1="0.4" x2="0.4" y2="1">
        <stop offset="0" stopColor="#0052CC" />
        <stop offset="0.9" stopColor="#2684FF" />
      </linearGradient>
    </defs>
    <path fill="url(#atl-g)" d="M7 11.2a.7.7 0 0 0-1.2.2L1.1 21a.7.7 0 0 0 .6 1h6.4a.7.7 0 0 0 .6-.4c1.4-2.9.5-7.2-1.7-10.4Z" />
    <path fill="#2684FF" d="M11.4 2.3a15.5 15.5 0 0 0-.9 15.3l3 6a.7.7 0 0 0 .6.4h6.4a.7.7 0 0 0 .6-1S12.6 3.5 12.6 2.3a.7.7 0 0 0-1.2 0Z" />
  </svg>
);

const CONFLUENCE: Glyph = ATLASSIAN;
const JIRA: Glyph = ATLASSIAN;

const GDRIVE: Glyph = (p) => (
  <svg viewBox="0 0 24 24" {...p}>
    <path fill="#0F9D58" d="M9 3.5 4.5 11l2.2 3.8L11.2 7z" />
    <path fill="#FBBC05" d="M15 3.5H9l4.5 7.5h6z" />
    <path fill="#4285F4" d="M16.7 18.6 20.7 11h-6l-3 5.1 1.4 2.5z" />
    <path fill="#EA4335" d="M6.7 14.8 4.5 11l-1.2 2 3.3 5.6 2.6-1.3z" />
  </svg>
);

const GMAIL: Glyph = (p) => (
  <svg viewBox="0 0 24 24" {...p}>
    <path fill="#EA4335" d="M3 6.5 12 13l9-6.5V6a1.5 1.5 0 0 0-1.5-1.5H4.5A1.5 1.5 0 0 0 3 6z" />
    <path fill="#FBBC05" d="M3 6.5V18a1.5 1.5 0 0 0 1.5 1.5H7V10z" />
    <path fill="#34A853" d="M21 6.5V18a1.5 1.5 0 0 1-1.5 1.5H17V10z" />
    <path fill="#4285F4" d="M7 19.5V10l5 3.5L17 10v9.5z" opacity=".9" />
  </svg>
);

const GOOGLE: Glyph = (p) => (
  <svg viewBox="0 0 24 24" {...p}>
    <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8Z" />
    <path fill="#34A853" d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.9-3c-1 .7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9H1.4v3.1A12 12 0 0 0 12 24Z" />
    <path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.8V6.5H1.4a12 12 0 0 0 0 11z" />
    <path fill="#EA4335" d="M12 4.8c1.8 0 3.4.6 4.6 1.8l3.5-3.5A12 12 0 0 0 1.4 6.5l4 3.1C6.3 6.9 8.9 4.8 12 4.8Z" />
  </svg>
);

const WEB: Glyph = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.5 2.6 3.9 5.7 4 9-.1 3.3-1.5 6.4-4 9-2.5-2.6-3.9-5.7-4-9 .1-3.3 1.5-6.4 4-9Z" />
  </svg>
);

const MCP: Glyph = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M4 12 12 4l8 8-8 8z" />
    <path d="M8 12h8" />
  </svg>
);

const PLUG: Glyph = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M9 2v6M15 2v6" />
    <path d="M7 8h10v3a5 5 0 0 1-10 0z" />
    <path d="M12 16v6" />
  </svg>
);

// Registry name (or fuzzy match) to brand glyph and tint. Tint colors the mono glyphs;
// full-color brand marks ignore it.
interface Brand {
  glyph: Glyph;
  tint?: string;
}

const BRANDS: Array<[RegExp, Brand]> = [
  [/github/, { glyph: GH, tint: "var(--ob-text)" }],
  [/notion/, { glyph: NOTION, tint: "var(--ob-text)" }],
  [/slack/, { glyph: SLACK }],
  [/confluence/, { glyph: CONFLUENCE }],
  [/jira/, { glyph: JIRA }],
  [/atlassian/, { glyph: ATLASSIAN }],
  [/(gdrive|google.?drive|drive)/, { glyph: GDRIVE }],
  [/(gmail|mail)/, { glyph: GMAIL }],
  [/google/, { glyph: GOOGLE }],
  [/(mcp|relay)/, { glyph: MCP, tint: "var(--ob-cyan)" }],
  [/(public|web|link|url|fetch|http)/, { glyph: WEB, tint: "var(--ob-text)" }],
];

export function connectorBrand(name: string): Brand {
  const n = name.toLowerCase();
  for (const [re, brand] of BRANDS) if (re.test(n)) return brand;
  return { glyph: PLUG, tint: "var(--ob-muted)" };
}

// --- OAuth provider brand marks + colors for the Connect button ---

export interface OAuthProvider {
  // The stable provider key used in the authorize URL (/api/oauth/{key}/authorize).
  key: string;
  label: string;
  glyph: Glyph;
  fg: string;
  bg: string;
  border: string;
}

export const OAUTH_PROVIDERS: Record<string, OAuthProvider> = {
  google: { key: "google", label: "Google", glyph: GOOGLE, fg: "#1f2328", bg: "#ffffff", border: "rgba(0,0,0,0.12)" },
  slack: { key: "slack", label: "Slack", glyph: SLACK, fg: "#1f2328", bg: "#ffffff", border: "rgba(0,0,0,0.12)" },
  atlassian: { key: "atlassian", label: "Atlassian", glyph: ATLASSIAN, fg: "#ffffff", bg: "#0052CC", border: "rgba(255,255,255,0.16)" },
  github: { key: "github", label: "GitHub", glyph: GH, fg: "#ffffff", bg: "#1f2328", border: "rgba(255,255,255,0.14)" },
};

// Resolve an OAuth provider from a spec's oauth_provider (or a fuzzy connector name).
// Returns null for an unrecognized key, so the caller can fall back to a generic connect.
export function oauthProvider(key: string | undefined): OAuthProvider | null {
  if (!key) return null;
  const k = key.toLowerCase();
  if (OAUTH_PROVIDERS[k]) return OAUTH_PROVIDERS[k];
  for (const name of Object.keys(OAUTH_PROVIDERS)) {
    if (k.includes(name)) return OAUTH_PROVIDERS[name];
  }
  return null;
}
