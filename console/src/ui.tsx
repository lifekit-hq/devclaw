// Thin primitives over the index.css design system. Components stay tiny — the
// styling lives in CSS classes + variables, not inline objects.
import { useEffect, useState, type ReactNode } from "react";

export function StatusDot({ color, live }: { color: string; live?: boolean }) {
  return (
    <span
      className={`dc-dot${live ? " live" : ""}`}
      style={{ background: color, ...(live ? { ["--pulse" as string]: color } : {}) }}
    />
  );
}

export function Badge({
  k,
  children,
  dot,
  title,
}: {
  k?: string;
  children: ReactNode;
  dot?: string;
  title?: string;
}) {
  return (
    <span className="badge" title={title}>
      {dot && <StatusDot color={dot} />}
      {k && <span className="k">{k}</span>}
      <span>{children}</span>
    </span>
  );
}

export function SectionLabel({
  children,
  count,
  right,
}: {
  children: ReactNode;
  count?: number;
  right?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="eyebrow">{children}</span>
        {count !== undefined && (
          <span className="mono muted" style={{ fontSize: 11 }}>
            {count}
          </span>
        )}
      </div>
      {right}
    </div>
  );
}

// TieredDisclosure — the P1 spine's "settled, folded, openable" section (ADR
// 0008). Every tier shows its active items in full and folds the settled ones
// behind a Show/Hide toggle that keeps the count visible. Extracted from the
// ad-hoc archived-goals block in ProjectDetail so every drill-down tier reuses
// one disclosure, not a hand-rolled useState each time. The settled content is
// dimmed to read as secondary. Uncontrolled by default; pass defaultOpen to
// start expanded.
export function TieredDisclosure({
  label,
  count,
  defaultOpen = false,
  children,
}: {
  label: ReactNode;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <>
      <SectionLabel
        count={count}
        right={
          <button className="btn ghost sm" onClick={() => setOpen((o) => !o)}>
            {open ? "Hide" : "Show"}
          </button>
        }
      >
        {label}
      </SectionLabel>
      {open && <div style={{ opacity: 0.75 }}>{children}</div>}
    </>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: T; label: string; count?: number }[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={t.id === active}
          className={`tab${t.id === active ? " active" : ""}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count !== undefined && <span className="count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid var(--border)",
            fontSize: 14,
            fontWeight: 600,
          }}
        >
          {title}
        </div>
        <div style={{ padding: 20 }}>{children}</div>
        {footer && (
          <div
            style={{
              padding: "14px 20px",
              borderTop: "1px solid var(--border)",
              display: "flex",
              justifyContent: "flex-end",
              gap: 10,
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div
      style={{
        padding: "48px 24px",
        textAlign: "center",
        color: "var(--text-muted)",
      }}
    >
      <div style={{ fontSize: 13.5, color: "var(--text-secondary)", marginBottom: 4 }}>
        {title}
      </div>
      {hint && <div style={{ fontSize: 12.5 }}>{hint}</div>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div style={{ padding: "40px 6px", fontSize: 13, color: "var(--text-muted)" }}>
      {label}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div style={{ padding: "16px 0", fontSize: 13, color: "var(--red)" }}>{children}</div>
  );
}

// ---- Trend (spec 039 US5) ---------------------------------------------------
// The console carries no chart library on purpose (React + router only), and
// the existing idiom is a hand-rolled bar (Evals' failure classes). This is that
// idiom generalised to a time series, in inline SVG so it scales and prints.
//
// The one rule it enforces structurally: a bucket whose value is `null` is a
// GAP — no bar, a dotted baseline tick — never a zero-height bar (spec 039
// FR-011, absent is never zero). A caller cannot accidentally draw silence as
// free, because null is a distinct branch, not a falsy number.

export interface TrendPoint {
  /** x-axis label (a month, a day, a cycle). */
  label: string;
  /** null = no data for this bucket. Rendered as a gap, never as 0. */
  value: number | null;
  /** Optional hover text; the label + formatted value when omitted. */
  title?: string;
}

export function Trend({
  points,
  color = "var(--accent)",
  height = 56,
  format = (v: number) => String(v),
  ariaLabel,
}: {
  points: TrendPoint[];
  color?: string;
  height?: number;
  format?: (v: number) => string;
  ariaLabel: string;
}) {
  const known = points.filter((p) => p.value !== null) as { value: number }[];
  const max = known.length ? Math.max(...known.map((p) => p.value)) : 0;
  const gap = 2;
  const w = 100 / Math.max(points.length, 1);

  if (points.length === 0) {
    return <EmptyState title="No data yet" hint="Buckets appear here once there is history to show." />;
  }

  return (
    <div style={{ width: "100%", overflowX: "auto" }}>
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
        style={{ width: "100%", height, display: "block" }}
      >
        {/* baseline — also what a gap bucket shows instead of a bar */}
        <line x1="0" y1={height - 0.5} x2="100" y2={height - 0.5} stroke="var(--border)" strokeWidth="1" />
        {points.map((p, i) => {
          const x = i * w;
          if (p.value === null) {
            return (
              <g key={i}>
                <title>{p.title ?? `${p.label}: no data`}</title>
                <rect x={x} y={0} width={w} height={height} fill="transparent" />
                <line
                  x1={x + gap / 2}
                  y1={height - 2}
                  x2={x + w - gap / 2}
                  y2={height - 2}
                  stroke="var(--text-muted)"
                  strokeWidth="1"
                  strokeDasharray="1 1"
                />
              </g>
            );
          }
          // A real zero still draws a hairline, so "measured zero" and "no data"
          // stay visually distinct.
          const h = max > 0 ? Math.max((p.value / max) * (height - 4), 0.75) : 0.75;
          return (
            <g key={i}>
              <title>{p.title ?? `${p.label}: ${format(p.value)}`}</title>
              <rect x={x} y={0} width={w} height={height} fill="transparent" />
              <rect
                x={x + gap / 2}
                y={height - h}
                width={Math.max(w - gap, 0.5)}
                height={h}
                fill={color}
                rx="0.5"
              />
            </g>
          );
        })}
      </svg>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: 4,
          fontSize: 10.5,
        }}
        className="mono muted"
      >
        <span>{points[0].label}</span>
        {points.length > 1 && <span>{points[points.length - 1].label}</span>}
      </div>
    </div>
  );
}
