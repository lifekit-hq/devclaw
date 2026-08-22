import { useEffect, useState } from "react";
import { fetchUsage, type InstanceUsage, type UsageBucket } from "../api";
import { relativeTime } from "../util/time";
import { EmptyState, ErrorNote, Loading, SectionLabel } from "../ui";

// Usage page — instance-wide token and cost aggregate, per-project breakdown,
// and cap-pressure history. Fetches /usage.json read-only; no write, no LLM.
// Cost figures are API-equivalent ESTIMATES, never a subscription balance.

function fmtK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.001) return `$${usd.toFixed(6)}`;
  return `$${usd.toFixed(4)}`;
}

function Tile({
  label,
  value,
  sub,
  muted,
}: {
  label: string;
  value: string;
  sub?: string;
  muted?: boolean;
}) {
  return (
    <div className="card" style={{ padding: "14px 18px", minWidth: 130, flex: "1 1 130px" }}>
      <div
        style={{
          fontSize: 24,
          fontWeight: 650,
          letterSpacing: "-0.02em",
          color: muted ? "var(--text-secondary)" : "var(--text)",
        }}
      >
        {value}
      </div>
      <div className="eyebrow" style={{ marginTop: 4 }}>
        {label}
      </div>
      {sub && (
        <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function TotalsGrid({ b }: { b: UsageBucket & { cost_is_estimate?: true } }) {
  const cTok = b.cognition_tokens_in + b.cognition_tokens_out;
  const wTok = b.worker_input_tokens + b.worker_output_tokens;
  const hasEstimated = b.cognition_rows_estimated > 0;
  const hasReal = b.cognition_rows_real > 0;
  const measuredLabel =
    hasEstimated && hasReal
      ? `${b.cognition_rows_real} real, ${b.cognition_rows_estimated} estimated`
      : hasEstimated
        ? `${b.cognition_rows_estimated} rows estimated (len/4)`
        : hasReal
          ? `${b.cognition_rows_real} rows measured`
          : undefined;

  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <Tile
        label="Cognition tokens"
        value={fmtK(cTok)}
        sub={`${fmtK(b.cognition_tokens_in)} in · ${fmtK(b.cognition_tokens_out)} out${measuredLabel ? ` · ${measuredLabel}` : ""}`}
      />
      <Tile
        label="Worker tokens"
        value={fmtK(wTok)}
        sub={`${fmtK(b.worker_input_tokens)} in · ${fmtK(b.worker_output_tokens)} out · ${fmtK(b.worker_cache_read_tokens)} cache`}
      />
      <Tile
        label="Cost estimate"
        value={fmtCost(b.cost_estimate_usd)}
        sub={b.cost_is_estimate ? "API-equivalent estimate — not a bill" : undefined}
        muted={b.cost_estimate_usd === 0}
      />
      {b.worker_tasks_with_usage > 0 && (
        <Tile label="Tasks reporting" value={String(b.worker_tasks_with_usage)} />
      )}
    </div>
  );
}

const CAP_LABEL: Record<string, string> = {
  rate_limit: "Rate limit",
  quota: "Quota",
  auth: "Auth pause",
};
const CAP_COLOR: Record<string, string> = {
  rate_limit: "var(--amber)",
  quota: "var(--red)",
  auth: "var(--violet)",
};

export function Usage() {
  const [data, setData] = useState<InstanceUsage | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchUsage()
        .then((r) => alive && setData(r))
        .catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const capEntries = data
    ? Object.entries(data.cap_pressure).sort((a, b) => b[1].last_seen_ms - a[1].last_seen_ms)
    : [];

  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 18px" }}>
        Usage
      </h1>

      {err && <ErrorNote>{err}</ErrorNote>}
      {!data && !err && <Loading />}

      {data && (
        <>
          <div style={{ marginBottom: 6 }}>
            <span className="eyebrow">Instance totals</span>
            <span className="mono muted" style={{ fontSize: 11, marginLeft: 8 }}>
              updated {relativeTime(data.computed_at_ms)}
            </span>
          </div>
          <div style={{ marginBottom: 22 }}>
            <TotalsGrid b={data.totals} />
          </div>

          <SectionLabel count={data.by_project.length}>Per project</SectionLabel>
          {data.by_project.length === 0 ? (
            <div className="card">
              <EmptyState title="No projects" hint="Register a project to see per-project attribution." />
            </div>
          ) : (
            <div className="card" style={{ padding: 0, overflowX: "auto", marginBottom: 22 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    {["Project", "Cognition in", "Cognition out", "Real rows", "Est. rows", "Worker in", "Worker out", "Cache read", "Cost est."].map(
                      (h) => (
                        <th
                          key={h}
                          className="secondary"
                          style={{ padding: "8px 14px", textAlign: "right", fontWeight: 500, whiteSpace: "nowrap" }}
                        >
                          {h}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {data.by_project.map((p) => (
                    <tr key={p.project_id} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td
                        style={{
                          padding: "8px 14px",
                          fontWeight: 500,
                          whiteSpace: "nowrap",
                          textAlign: "left",
                        }}
                      >
                        {p.project_name}
                      </td>
                      {[
                        p.cognition_tokens_in,
                        p.cognition_tokens_out,
                        p.cognition_rows_real,
                        p.cognition_rows_estimated,
                        p.worker_input_tokens,
                        p.worker_output_tokens,
                        p.worker_cache_read_tokens,
                      ].map((v, i) => (
                        <td
                          key={i}
                          className={v === 0 ? "muted" : undefined}
                          style={{ padding: "8px 14px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}
                        >
                          {fmtK(v)}
                        </td>
                      ))}
                      <td
                        className={p.cost_estimate_usd === 0 ? "muted" : undefined}
                        style={{ padding: "8px 14px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}
                      >
                        {fmtCost(p.cost_estimate_usd)}
                      </td>
                    </tr>
                  ))}
                  {/* Unattributed row — only shown when there is unattributed usage */}
                  {(data.unattributed.cognition_tokens_in +
                    data.unattributed.cognition_tokens_out +
                    data.unattributed.worker_input_tokens) > 0 && (
                    <tr style={{ borderTop: "2px solid var(--border)", opacity: 0.7 }}>
                      <td
                        className="secondary"
                        style={{ padding: "8px 14px", fontStyle: "italic", textAlign: "left" }}
                      >
                        (unattributed)
                      </td>
                      {[
                        data.unattributed.cognition_tokens_in,
                        data.unattributed.cognition_tokens_out,
                        data.unattributed.cognition_rows_real,
                        data.unattributed.cognition_rows_estimated,
                        data.unattributed.worker_input_tokens,
                        data.unattributed.worker_output_tokens,
                        data.unattributed.worker_cache_read_tokens,
                      ].map((v, i) => (
                        <td
                          key={i}
                          className={v === 0 ? "muted" : undefined}
                          style={{ padding: "8px 14px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}
                        >
                          {fmtK(v)}
                        </td>
                      ))}
                      <td
                        className={data.unattributed.cost_estimate_usd === 0 ? "muted" : undefined}
                        style={{ padding: "8px 14px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}
                      >
                        {fmtCost(data.unattributed.cost_estimate_usd)}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          <SectionLabel count={capEntries.length}>Cap pressure (last 30 days)</SectionLabel>
          {capEntries.length === 0 ? (
            <div className="card">
              <EmptyState title="No limit events" hint="Rate limits and quota hits would appear here." />
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 22 }}>
              {capEntries.map(([kind, entry]) => (
                <div
                  key={kind}
                  className="card"
                  style={{
                    padding: "12px 16px",
                    borderLeft: `2px solid ${CAP_COLOR[kind] ?? "var(--text-muted)"}`,
                    display: "flex",
                    alignItems: "center",
                    gap: 14,
                    flexWrap: "wrap",
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 13 }}>
                    {CAP_LABEL[kind] ?? kind}
                  </span>
                  <span className="mono muted" style={{ fontSize: 11 }}>
                    last seen {relativeTime(entry.last_seen_ms)}
                  </span>
                  {entry.reset_hint_s != null && (
                    <span className="mono muted" style={{ fontSize: 11 }}>
                      reset hint: {Math.round(entry.reset_hint_s / 60)}m
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            Cost figures are API-equivalent <b>estimates</b> — not a subscription balance or
            remaining quota. On OAuth (Pro/Max) the CLI reports no dollar cost, so the estimate is
            0. Cognition rows without real CLI-reported usage contribute a len/4 token estimate
            (shown as "estimated" rows).
          </div>
        </>
      )}
    </div>
  );
}
