import type { SVGProps } from "react";

// The pansoph brand mark: an abstract owl / eye-of-wisdom monogram. The two big
// eyes double as apertures, the right pupil and the beak carry the cyan accent
// (seeing / access-aware). Strokes use currentColor so the mark inherits its
// color from context; the accent pupil and beak stay cyan.

const ACCENT = "#22D3EE"; // Obsidian secondary cyan, the fixed accent in the mark.

/**
 * OwlMark
 * The square owl monogram. Strokes and the ink pupil follow `currentColor`, so set
 * text color on a parent (e.g. text-text) to recolor the mark. The right pupil and
 * beak are always cyan. Pass width/height or className to size it.
 */
export function OwlMark({
  title = "pansoph",
  ...props
}: SVGProps<SVGSVGElement> & { title?: string }) {
  return (
    <svg
      viewBox="0 0 128 128"
      role="img"
      aria-label={title}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      <title>{title}</title>
      {/* head silhouette: a rounded shield */}
      <path
        d="M64 16 C92 16 108 34 108 62 C108 92 90 112 64 112 C38 112 20 92 20 62 C20 34 36 16 64 16 Z"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinejoin="round"
      />
      {/* brow / ear-tufts: continuous stroke sweeping over both eyes */}
      <path
        d="M30 52 C34 34 48 30 56 44 M98 52 C94 34 80 30 72 44"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* eyes: outer rings (the two apertures) */}
      <circle cx="47" cy="58" r="16" stroke="currentColor" strokeWidth="5" />
      <circle cx="81" cy="58" r="16" stroke="currentColor" strokeWidth="5" />
      {/* pupils: one ink, one accent = seeing / access-aware */}
      <circle cx="47" cy="58" r="6.5" fill="currentColor" />
      <circle cx="81" cy="58" r="6.5" fill={ACCENT} />
      {/* beak: downward triangle */}
      <path d="M58 74 L70 74 L64 88 Z" fill={ACCENT} />
    </svg>
  );
}

/**
 * BrandMark
 * The mark inside the app's signature gradient tile (violet, with accent glow).
 * Used at the top of the sidebar and in modal headers. `size` in px.
 */
export function BrandMark({ size = 34, className = "" }: { size?: number; className?: string }) {
  const inner = Math.round(size * 0.62);
  return (
    <span
      className={`inline-grid place-items-center rounded-[9px] text-white ${className}`}
      style={{
        width: size,
        height: size,
        background: "linear-gradient(140deg, var(--ob-accent), #5b3ce0)",
        boxShadow: "var(--ob-glow)",
      }}
    >
      <OwlMark style={{ width: inner, height: inner }} />
    </span>
  );
}

/**
 * Wordmark
 * The "pansoph" wordmark rendered as styled text (Inter, tight tracking). Kept as
 * text rather than the SVG path so it inherits currentColor and stays crisp.
 */
export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span
      className={`font-sans font-bold tracking-[-0.03em] ${className}`}
      style={{ letterSpacing: "-0.03em" }}
    >
      pansoph
    </span>
  );
}

/**
 * Lockup
 * The full brand lockup: BrandMark tile + wordmark + optional tagline, laid out
 * horizontally. This is the sidebar header. `tagline` renders muted beneath the name.
 */
export function Lockup({
  tagline,
  size = 34,
  className = "",
}: {
  tagline?: string;
  size?: number;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <BrandMark size={size} />
      <span className="flex flex-col leading-[1.1]">
        <Wordmark className="text-[16px] text-text" />
        {tagline && <span className="mt-0.5 text-[11px] font-medium text-muted">{tagline}</span>}
      </span>
    </div>
  );
}

export default OwlMark;
