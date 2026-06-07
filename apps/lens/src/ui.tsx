import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

// A small hand-built component kit so the app has one consistent voice.

export function Card({
  children,
  className = "",
  ...rest
}: { children: ReactNode; className?: string } & HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-[var(--radius-card)] border border-line bg-surface ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}

type BadgeKind = "ok" | "warn" | "err" | "neutral" | "accent";

export function Badge({ kind, children }: { kind: BadgeKind; children: ReactNode }) {
  const map: Record<BadgeKind, string> = {
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    err: "bg-err-soft text-err",
    neutral: "bg-canvas text-muted",
    accent: "bg-accent-soft text-accent-ink",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${map[kind]}`}
    >
      {children}
    </span>
  );
}

export function Dot({ kind }: { kind: "ok" | "warn" | "err" }) {
  const c = { ok: "bg-ok", warn: "bg-warn", err: "bg-err" }[kind];
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${c}`} />;
}

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "subtle";
}

export function Button({ variant = "primary", className = "", children, ...rest }: BtnProps) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed";
  const map = {
    primary: "bg-accent text-white hover:bg-accent-ink",
    ghost: "border border-line bg-surface text-ink hover:bg-canvas",
    subtle: "text-accent-ink hover:bg-accent-soft",
  };
  return (
    <button className={`${base} ${map[variant]} ${className}`} {...rest}>
      {children}
    </button>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent ${className}`}
    />
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
    </label>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent ${props.className ?? ""}`}
    />
  );
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="mb-6 flex items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}
