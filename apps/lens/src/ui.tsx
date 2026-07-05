import {
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  useEffect,
  useId,
  useState,
} from "react";

// ============================================================
// pansoph . lens . Obsidian component kit
//
// One consistent voice for the whole app. Dark-first, sharp,
// 1px borders, accent glow on active/hover, focus-visible rings.
// These names and prop shapes are the CONTRACT that page agents
// build against, so keep them stable. Each export carries a short
// doc comment describing its API.
// ============================================================

// Small class-name joiner. Filters out falsy values so callers can
// pass conditional classes inline.
function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------
// Button
// variant: primary | ghost | danger | subtle. size: sm | md | lg.
// Renders a native <button>, forwards all button attrs. `block`
// stretches to full width. Use for every clickable action.
// ------------------------------------------------------------
export type ButtonVariant = "primary" | "ghost" | "danger" | "subtle";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
}

const BTN_VARIANT: Record<ButtonVariant, string> = {
  primary:
    "border border-accent-line bg-accent text-white shadow-[0_8px_24px_-10px_rgba(124,92,255,0.8)] hover:brightness-110",
  ghost: "border border-border bg-surface text-text hover:bg-elevated hover:border-border-hover",
  danger: "border border-[rgba(244,63,94,0.35)] bg-err-soft text-err hover:brightness-110",
  subtle: "border border-transparent bg-transparent text-muted hover:bg-elevated hover:text-text",
};

const BTN_SIZE: Record<ButtonSize, string> = {
  sm: "px-3 py-1.5 text-xs gap-1.5",
  md: "px-3.5 py-2 text-sm gap-2",
  lg: "px-5 py-2.5 text-sm gap-2",
};

export function Button({
  variant = "primary",
  size = "md",
  block = false,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center rounded-[var(--radius-md)] font-semibold transition active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50",
        BTN_VARIANT[variant],
        BTN_SIZE[size],
        block && "w-full",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

// ------------------------------------------------------------
// Card
// Elevated surface with 1px border and rounded corners. Forwards
// div attrs (onClick, etc). `glow` adds an accent hover glow.
// ------------------------------------------------------------
interface CardProps extends HTMLAttributes<HTMLDivElement> {
  glow?: boolean;
}

export function Card({ glow = false, className = "", children, ...rest }: CardProps) {
  return (
    <div
      className={cx(
        "rounded-[var(--radius-lg)] border border-border bg-surface",
        glow && "transition hover:border-border-hover hover:shadow-[var(--shadow-float)]",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

// ------------------------------------------------------------
// Input
// Styled text input. Forwards all input attrs. Focus shows an
// accent border + soft ring. Pass className to extend.
// ------------------------------------------------------------
export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx(
        "w-full rounded-[var(--radius-md)] border border-border bg-bg px-3 py-2 text-sm text-text outline-none transition placeholder:text-muted focus:border-accent-line",
        className,
      )}
    />
  );
}

// ------------------------------------------------------------
// Textarea
// Multi-line counterpart to Input. Same focus treatment.
// ------------------------------------------------------------
export function Textarea({
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(
        "w-full rounded-[var(--radius-md)] border border-border bg-bg px-3 py-2 text-sm text-text outline-none transition placeholder:text-muted focus:border-accent-line",
        className,
      )}
    />
  );
}

// ------------------------------------------------------------
// Select
// Styled native <select> with a custom chevron. Forwards select
// attrs; pass <option> children.
// ------------------------------------------------------------
export function Select({
  className = "",
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        {...props}
        className={cx(
          "w-full appearance-none rounded-[var(--radius-md)] border border-border bg-bg px-3 py-2 pr-9 text-sm text-text outline-none transition focus:border-accent-line disabled:opacity-60",
          className,
        )}
      >
        {children}
      </select>
      <svg
        viewBox="0 0 24 24"
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

// ------------------------------------------------------------
// Field
// Label + optional help + optional error wrapper around any control.
// BACKWARD COMPATIBLE: `<Field label="X">{control}</Field>` still works.
// `help` renders muted below; `error` renders in danger and takes
// precedence over help. `required` shows an accent asterisk.
// ------------------------------------------------------------
export function Field({
  label,
  help,
  error,
  required = false,
  children,
}: {
  label: string;
  help?: string;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-text">
        {label}
        {required && <span className="font-mono text-accent">*</span>}
      </span>
      {children}
      {error ? (
        <span className="mt-1 block text-xs text-err">{error}</span>
      ) : (
        help && <span className="mt-1 block text-xs text-muted">{help}</span>
      )}
    </label>
  );
}

// ------------------------------------------------------------
// Badge / Chip
// Small pill label. kind: ok | warn | err | neutral | accent | cyan.
// `dot` prepends a status dot in the matching color. Chip is an alias
// of Badge for semantic clarity at call sites.
// ------------------------------------------------------------
export type BadgeKind = "ok" | "warn" | "err" | "neutral" | "accent" | "cyan";

const BADGE_KIND: Record<BadgeKind, string> = {
  ok: "text-ok bg-ok-soft border-[rgba(52,211,153,0.3)]",
  warn: "text-warn bg-warn-soft border-[rgba(245,158,11,0.3)]",
  err: "text-err bg-err-soft border-[rgba(244,63,94,0.32)]",
  neutral: "text-muted bg-surface border-border",
  accent: "text-accent bg-accent-soft border-accent-line",
  cyan: "text-cyan bg-cyan-soft border-[rgba(34,211,238,0.3)]",
};

export function Badge({
  kind = "neutral",
  dot = false,
  className = "",
  children,
}: {
  kind?: BadgeKind;
  dot?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        BADGE_KIND[kind],
        className,
      )}
    >
      {dot && <StatusDot kind={kind === "cyan" || kind === "accent" ? "run" : kind} size={6} />}
      {children}
    </span>
  );
}

export const Chip = Badge;

// ------------------------------------------------------------
// StatusDot
// A colored status dot with an optional glow ring and pulse.
// kind: ok | warn | err | run (cyan) | neutral. `pulse` animates
// the glow ring, `size` is the diameter in px.
// ------------------------------------------------------------
export type StatusKind = "ok" | "warn" | "err" | "run" | "neutral";

const DOT_COLOR: Record<StatusKind, string> = {
  ok: "var(--ob-ok)",
  warn: "var(--ob-warn)",
  err: "var(--ob-err)",
  run: "var(--ob-cyan)",
  neutral: "var(--ob-muted)",
};
const DOT_GLOW: Record<StatusKind, string> = {
  ok: "rgba(52,211,153,0.7)",
  warn: "rgba(245,158,11,0.7)",
  err: "rgba(244,63,94,0.8)",
  run: "rgba(34,211,238,0.7)",
  neutral: "rgba(154,154,166,0.4)",
};

export function StatusDot({
  kind = "ok",
  size = 7,
  pulse = false,
  className = "",
}: {
  kind?: StatusKind;
  size?: number;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cx("inline-block shrink-0 rounded-full", pulse && "status-pulse", className)}
      style={{
        width: size,
        height: size,
        background: DOT_COLOR[kind],
        boxShadow: `0 0 6px 0 ${DOT_GLOW[kind]}`,
        // consumed by the status-pulse keyframes
        ["--ring-glow" as string]: DOT_GLOW[kind],
      }}
    />
  );
}

// Legacy alias: the page bodies call <Dot kind="ok|warn|err" />. Kept so
// existing markup compiles and renders. Prefer StatusDot in new code.
export function Dot({ kind }: { kind: "ok" | "warn" | "err" }) {
  return <StatusDot kind={kind} size={8} />;
}

// ------------------------------------------------------------
// Avatar
// Circular identity chip. Renders the first initial of `name` on a
// cyan-to-violet gradient. `size` in px. Optional `title` tooltip.
// ------------------------------------------------------------
export function Avatar({
  name,
  size = 28,
  title,
  className = "",
}: {
  name: string;
  size?: number;
  title?: string;
  className?: string;
}) {
  const initial = (name.trim().slice(0, 1) || "?").toUpperCase();
  return (
    <span
      title={title ?? name}
      className={cx(
        "inline-grid shrink-0 place-items-center rounded-full font-bold text-white",
        className,
      )}
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.42),
        background: "linear-gradient(140deg, var(--ob-cyan), var(--ob-accent))",
      }}
    >
      {initial}
    </span>
  );
}

// ------------------------------------------------------------
// Spinner
// Indeterminate loading ring. `className` can override border colors
// (e.g. on a colored button: border-white/40 border-t-white).
// ------------------------------------------------------------
export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={cx(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-border border-t-accent",
        className,
      )}
    />
  );
}

// ------------------------------------------------------------
// Modal / Dialog
// Centered dialog with a blurred backdrop. `open` controls visibility,
// `onClose` fires on backdrop click and Escape. `title` + `subtitle`
// render a header with an optional `icon`; `footer` renders a sticky
// footer row. Dialog is an alias of Modal.
// ------------------------------------------------------------
export function Modal({
  open,
  onClose,
  title,
  subtitle,
  icon,
  footer,
  children,
  className = "",
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="animate-fade fixed inset-0 z-50 grid place-items-center bg-[rgba(4,4,8,0.72)] p-6 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        className={cx(
          "animate-rise max-h-[88vh] w-full max-w-lg overflow-y-auto rounded-[var(--radius-xl)] border border-border bg-elevated shadow-[var(--shadow-float)]",
          className,
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {(title || icon) && (
          <div className="flex items-start gap-3.5 border-b border-border px-5 pb-4 pt-5">
            {icon}
            <div className="min-w-0 flex-1">
              {title && (
                <div className="text-[17px] font-bold tracking-[-0.025em] text-text">{title}</div>
              )}
              {subtitle && <div className="mt-0.5 text-xs text-muted">{subtitle}</div>}
            </div>
            <button
              onClick={onClose}
              aria-label="Close"
              className="grid h-8 w-8 place-items-center rounded-[var(--radius-sm)] border border-border bg-surface text-muted transition hover:border-border-hover hover:text-text"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 6 6 18M6 6l12 12" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        )}
        <div className="p-5">{children}</div>
        {footer && (
          <div className="flex items-center gap-3 border-t border-border bg-[color-mix(in_srgb,var(--ob-surface)_50%,transparent)] px-5 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export const Dialog = Modal;

// ------------------------------------------------------------
// Table
// Thin wrappers giving the app's table styling. Compose as:
// <Table><THead><TR><TH/>..</TR></THead><TBody><TR><TD/>..</TR></TBody></Table>
// All forward native attrs so callers can add onClick, colSpan, etc.
// ------------------------------------------------------------
export function Table({ className = "", children, ...rest }: HTMLAttributes<HTMLTableElement>) {
  return (
    <table className={cx("w-full border-collapse text-sm tabular-nums", className)} {...rest}>
      {children}
    </table>
  );
}
export function THead({ className = "", children, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead className={className} {...rest}>
      {children}
    </thead>
  );
}
export function TBody({ className = "", children, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <tbody className={className} {...rest}>
      {children}
    </tbody>
  );
}
export function TR({ className = "", children, ...rest }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr className={cx("border-b border-border last:border-0", className)} {...rest}>
      {children}
    </tr>
  );
}
export function TH({ className = "", children, ...rest }: HTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cx(
        "px-3 py-2 text-left text-[10.5px] font-semibold uppercase tracking-wide text-muted",
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  );
}
export function TD({ className = "", children, ...rest }: HTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cx("px-3 py-2.5 text-text", className)} {...rest}>
      {children}
    </td>
  );
}

// ------------------------------------------------------------
// EmptyState
// Centered placeholder for empty lists. `icon` renders above the
// title, `title` is the headline, `description` the muted subline,
// `action` an optional call to action (e.g. a Button).
// ------------------------------------------------------------
export function EmptyState({
  icon,
  title,
  description,
  action,
  className = "",
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex flex-col items-center justify-center gap-2 rounded-[var(--radius-lg)] border border-dashed border-border px-6 py-14 text-center",
        className,
      )}
    >
      {icon && <div className="mb-1 text-muted">{icon}</div>}
      <div className="text-sm font-semibold text-text">{title}</div>
      {description && <div className="max-w-sm text-xs text-muted">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

// ------------------------------------------------------------
// Alert (inline)
// Inline message banner. kind: ok | warn | err | info (accent).
// `title` optional bold headline; children are the body. Use for
// non-transient inline feedback (form errors, notices).
// ------------------------------------------------------------
export type AlertKind = "ok" | "warn" | "err" | "info";

const ALERT_KIND: Record<AlertKind, string> = {
  ok: "border-[rgba(52,211,153,0.3)] bg-ok-soft text-ok",
  warn: "border-[rgba(245,158,11,0.3)] bg-warn-soft text-warn",
  err: "border-[rgba(244,63,94,0.32)] bg-err-soft text-err",
  info: "border-accent-line bg-accent-soft text-accent",
};

export function Alert({
  kind = "info",
  title,
  className = "",
  children,
}: {
  kind?: AlertKind;
  title?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className={cx(
        "rounded-[var(--radius-md)] border px-4 py-3 text-sm",
        ALERT_KIND[kind],
        className,
      )}
    >
      {title && <div className="font-semibold">{title}</div>}
      {children && <div className={cx(title && "mt-0.5", "text-current/85")}>{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------
// Toast
// Transient floating notification (bottom-right). kind mirrors Alert.
// `onDismiss` (optional) auto-fires after `duration` ms (default 4000)
// and on click. Render at most a few; caller owns the queue/state.
// ------------------------------------------------------------
export function Toast({
  kind = "info",
  message,
  duration = 4000,
  onDismiss,
}: {
  kind?: AlertKind;
  message: ReactNode;
  duration?: number;
  onDismiss?: () => void;
}) {
  useEffect(() => {
    if (!onDismiss) return;
    const t = setTimeout(onDismiss, duration);
    return () => clearTimeout(t);
  }, [onDismiss, duration]);

  return (
    <div
      className="animate-rise fixed bottom-5 right-5 z-[60] max-w-sm cursor-pointer rounded-[var(--radius-md)] border border-border bg-elevated px-4 py-3 text-sm text-text shadow-[var(--shadow-float)]"
      onClick={onDismiss}
      role="status"
    >
      <div className="flex items-start gap-2.5">
        <StatusDot kind={kind === "info" ? "run" : kind} size={7} className="mt-1.5" />
        <span>{message}</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------
// Tabs
// Controlled segmented tabs. `items` is a list of { value, label,
// count? }; `value` is the active value; `onChange` fires on click.
// Purely presentational, caller renders the panel for the active value.
// ------------------------------------------------------------
export interface TabItem {
  value: string;
  label: ReactNode;
  count?: number;
}

export function Tabs({
  items,
  value,
  onChange,
  className = "",
}: {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "inline-flex gap-0.5 rounded-[var(--radius-md)] border border-border bg-surface p-0.5",
        className,
      )}
      role="tablist"
    >
      {items.map((it) => {
        const on = it.value === value;
        return (
          <button
            key={it.value}
            role="tab"
            aria-selected={on}
            onClick={() => onChange(it.value)}
            className={cx(
              "rounded-[7px] px-3 py-1.5 text-xs font-medium transition",
              on
                ? "bg-elevated text-text shadow-[inset_0_0_0_1px_var(--color-border)]"
                : "text-muted hover:text-text",
            )}
          >
            {it.label}
            {it.count != null && <span className="ml-1.5 opacity-60 tabular-nums">{it.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------
// Tooltip
// Hover / focus tooltip. Wraps `children` (the trigger) and shows
// `label` in a floating chip above it. CSS-only reveal, no portal;
// keep labels short. `side` reserved for future placement (top only).
// ------------------------------------------------------------
export function Tooltip({
  label,
  children,
  className = "",
}: {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <span className={cx("group/tt relative inline-flex", className)}>
      <span aria-describedby={id}>{children}</span>
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 whitespace-nowrap rounded-[var(--radius-sm)] border border-border bg-elevated px-2 py-1 text-xs text-text opacity-0 shadow-[var(--shadow-float)] transition-opacity duration-150 group-hover/tt:opacity-100 group-focus-within/tt:opacity-100"
      >
        {label}
      </span>
    </span>
  );
}

// ------------------------------------------------------------
// Kbd
// Keyboard key hint chip (e.g. Cmd K). Monospace, inset shadow.
// ------------------------------------------------------------
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-[5px] border border-border bg-bg px-1.5 py-0.5 font-mono text-[10.5px] font-medium leading-none text-muted shadow-[0_1px_0_rgba(0,0,0,0.4)]">
      {children}
    </span>
  );
}

// ------------------------------------------------------------
// PageHeader
// Standard page title block: `title`, optional `subtitle`, optional
// right-aligned `action`, and an optional monospace `breadcrumb` trail.
// Kept backward compatible with existing pages (title/subtitle/action).
// ------------------------------------------------------------
export function PageHeader({
  title,
  subtitle,
  action,
  breadcrumb,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  breadcrumb?: string[];
}) {
  return (
    <div className="mb-6 flex items-end justify-between gap-4">
      <div className="min-w-0">
        {breadcrumb && breadcrumb.length > 0 && (
          <div className="mb-2 flex items-center gap-1.5 font-mono text-xs text-muted">
            {breadcrumb.map((b, i) => (
              <span key={i} className="flex items-center gap-1.5">
                {i > 0 && <span className="opacity-50">/</span>}
                {b}
              </span>
            ))}
          </div>
        )}
        <h1 className="text-xl font-bold tracking-[-0.03em] text-text">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

// ------------------------------------------------------------
// relativeTime
// Formats an ISO timestamp as a compact "Ns/Nm/Nh/Nd ago" string.
// Returns "never" for null. Pure helper, safe on the render path.
// ------------------------------------------------------------
export function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

// useToasts
// Tiny toast queue hook for pages that want ephemeral notifications.
// Returns { toasts, push, dismiss } and a ready-to-render <ToastStack/>.
export interface ToastItem {
  id: number;
  kind: AlertKind;
  message: ReactNode;
}

let _toastSeq = 0;

export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));
  const push = (message: ReactNode, kind: AlertKind = "info") =>
    setToasts((t) => [...t, { id: ++_toastSeq, kind, message }]);
  const ToastStack = () => (
    <>
      {toasts.map((t, i) => (
        <div key={t.id} style={{ transform: `translateY(-${i * 60}px)` }}>
          <Toast kind={t.kind} message={t.message} onDismiss={() => dismiss(t.id)} />
        </div>
      ))}
    </>
  );
  return { toasts, push, dismiss, ToastStack };
}
