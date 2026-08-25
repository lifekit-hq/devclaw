import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  fetchEvalOutcomes,
  fetchCycleReports,
  tokenQueryString,
  type EvalOutcome,
  type CycleReport,
} from "../api";
import { KIND_LABEL, taskStatusColor } from "../status";
import { relativeTime } from "../util/time";
import { EmptyState, ErrorNote, Loading, SectionLabel, StatusDot } from "../ui";

// The Evals tab is a read-only projection of the eval_outcomes table (every
// settled task + ingested basket runs) plus the cycle_reports table (the
// per-cycle window-close report). Two headline metrics per ADR 0006:
//   * pass_rate — fraction of settled outcomes that are done AND verify-passed;
//   * clean-cycle rate — fraction of cycles with zero mechanism-wedges.
// Both feature-detect empty/missing tables and render an empty state, not a crash.
// Every row is clickable — expands an inline detail panel showing every stored
// field (issue #682: universal row drill-ins, increment 1).

type SourceFilter = "all" | "live" | "basket";

export function Evals() {
  const [outcomes, setOutcomes] = useState<EvalOutcome[] | null>(null);
  const [cycles, setCycles] = useState<CycleReport[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<SourceFilter>("all");

  useEffect(() => {
    let alive = true;
    const load = () =>
      Promise.all([fetchEvalOutcomes({ limit: 200 }), fetchCycleReports(60)])
        .then(([o, n]) => {
          if (!alive) return;
          setOutcomes(o);
          setCycles(n);
        })
        .catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 20000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const rows = outcomes ?? [];
  const settled = rows.length;
  const passed = rows.filter((o) => o.status === "done" && o.verify_passed === 1).length;
  const passRate = settled ? passed / settled : null;

  const cycleRows = cycles ?? [];
  // Idle cycles (the loop did no work — off/held/all-cancelled) are excluded
  // from BOTH numerator and denominator, so empty nights can't drift the rate.
  const scoredCycles = cycleRows.filter((n) => n.idle !== 1);
  const cleanCycles = scoredCycles.filter((n) => n.clean === 1).length;
  const cleanRate = scoredCycles.length ? cleanCycles / scoredCycles.length : null;
  const idleCycles = cycleRows.length - scoredCycles.length;

  const shown =
    filter === "all" ? rows : rows.filter((o) => o.source === filter);

  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 18px" }}>
        Evals
      </h1>

      {err && <ErrorNote>{err}</ErrorNote>}
      {!outcomes && !err && <Loading />}

      {outcomes && (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 30 }}>
            <Metric
              label="Pass rate"
              value={pct(passRate)}
              sub={settled ? `${passed} / ${settled} settled` : "no outcomes yet"}
              color={rateColor(passRate)}
            />
            <Metric
              label="Clean cycles"
              value={pct(cleanRate)}
              sub={
                scoredCycles.length
                  ? `${cleanCycles} / ${scoredCycles.length} cycles${idleCycles ? ` · ${idleCycles} idle` : ""}`
                  : cycleRows.length
                    ? `${idleCycles} idle (no runs)`
                    : "no cycle reports yet"
              }
              color={rateColor(cleanRate)}
            />
            <Metric label="Outcomes" value={String(settled)} sub="recent settles" />
          </div>

          <FailureClasses rows={rows} />

          <section style={{ marginBottom: 34 }}>
            <SectionLabel
              count={shown.length}
              right={
                <div style={{ display: "flex", gap: 6 }}>
                  {(["all", "live", "basket"] as SourceFilter[]).map((f) => (
                    <button
                      key={f}
                      className={`btn ghost sm${filter === f ? " active" : ""}`}
                      onClick={() => setFilter(f)}
                      style={filter === f ? { color: "var(--accent)" } : undefined}
                    >
                      {f}
                    </button>
                  ))}
                </div>
              }
            >
              Outcomes
            </SectionLabel>
            {shown.length === 0 ? (
              <div className="card">
                <EmptyState
                  title="No outcomes"
                  hint="Settled tasks and ingested basket runs appear here."
                />
              </div>
            ) : (
              <OutcomesTable rows={shown} />
            )}
          </section>

          <section>
            <SectionLabel count={cycleRows.length}>Cycle reports</SectionLabel>
            {cycleRows.length === 0 ? (
              <div className="card">
                <EmptyState
                  title="No cycle reports yet"
                  hint="The per-cycle window-close report lands here once the cycle-report tranche ships."
                />
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {cycleRows.map((n) => (
                  <CycleRow key={n.cycle_key} n={n} />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

// Error-class breakdown (ADR 0009 P2) — which mechanical failure classes
// dominate the settled outcomes, ranked. Client-side over failure_class, which
// is already on every eval_outcomes row; the reliability-legibility payoff is
// "where is the loop failing", so effort lands where the bars are longest.
function FailureClasses({ rows }: { rows: EvalOutcome[] }) {
  const counts = new Map<string, number>();
  for (const o of rows) {
    if (o.failure_class) counts.set(o.failure_class, (counts.get(o.failure_class) ?? 0) + 1);
  }
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const total = ranked.reduce((s, [, n]) => s + n, 0);
  const max = ranked.length ? ranked[0][1] : 0;

  return (
    <section style={{ marginBottom: 34 }}>
      <SectionLabel count={ranked.length}>Failure classes</SectionLabel>
      {ranked.length === 0 ? (
        <div className="card">
          <EmptyState title="No failures in view" hint="Every settled outcome here either passed or carries no mechanical class." />
        </div>
      ) : (
        <div className="card" style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
          {ranked.map(([cls, n]) => (
            <div key={cls} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 44px", gap: 12, alignItems: "center" }}>
              <div>
                <div className="mono" style={{ fontSize: 12, color: "var(--red)", marginBottom: 4 }}>{cls}</div>
                <div style={{ height: 6, background: "var(--red-soft)", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${max ? (n / max) * 100 : 0}%`, background: "var(--red)" }} />
                </div>
              </div>
              <span className="mono secondary" style={{ fontSize: 12.5, textAlign: "right" }}>{n}</span>
            </div>
          ))}
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            {total} failed outcome{total === 1 ? "" : "s"} across {ranked.length} class{ranked.length === 1 ? "" : "es"} · token spend is per-goal (see a goal's Usage badge); a node-wide rollup is not surfaced here.
          </div>
        </div>
      )}
    </section>
  );
}

function pct(r: number | null): string {
  return r === null ? "—" : `${Math.round(r * 100)}%`;
}

function rateColor(r: number | null): string {
  if (r === null) return "var(--text-muted)";
  if (r >= 0.8) return "var(--green)";
  if (r >= 0.5) return "var(--amber)";
  return "var(--red)";
}

function Metric({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub: string;
  color?: string;
}) {
  return (
    <div className="card" style={{ padding: "14px 18px", minWidth: 150, flex: "1 1 150px" }}>
      <div
        style={{
          fontSize: 26,
          fontWeight: 650,
          letterSpacing: "-0.02em",
          color: color ?? "var(--text)",
        }}
      >
        {value}
      </div>
      <div className="eyebrow" style={{ marginTop: 4 }}>
        {label}
      </div>
      <div className="secondary" style={{ fontSize: 12, marginTop: 2 }}>
        {sub}
      </div>
    </div>
  );
}

function verifyGlyph(v: number | null): { text: string; color: string } {
  if (v === 1) return { text: "pass", color: "var(--green)" };
  if (v === 0) return { text: "fail", color: "var(--red)" };
  return { text: "—", color: "var(--text-muted)" };
}

// ---- Outcome detail panel (all stored fields) --------------------------------

function OutcomeDetail({ o }: { o: EvalOutcome }) {
  const qs = tokenQueryString();
  const v = verifyGlyph(o.verify_passed);
  return (
    <div
      style={{
        padding: "14px 16px",
        background: "var(--surface-raised, var(--bg-alt))",
        borderTop: "1px solid var(--border)",
        fontSize: 12.5,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Field label="id" value={String(o.id)} mono />
        <Field label="source" value={o.source} />
        <Field label="kind" value={o.kind ? (KIND_LABEL[o.kind] ?? o.kind) : "—"} />
        <Field label="status" value={o.status} />
        <Field
          label="verify"
          value={v.text}
          valueStyle={{ color: v.color }}
        />
        <Field
          label="attempts"
          value={o.attempts != null ? String(o.attempts) : "—"}
          mono
        />
        <Field
          label="wall"
          value={o.wall_ms != null ? `${(o.wall_ms / 1000).toFixed(1)}s` : "—"}
          mono
        />
        <Field label="settled" value={relativeTime(o.settled_at)} mono />
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {o.task_id && (
          <span className="secondary" style={{ fontSize: 12 }}>
            Task:{" "}
            <Link to={`/tasks/${o.task_id}${qs}`} className="mono" style={{ fontSize: 12 }}>
              {o.task_id.slice(0, 12)}
            </Link>
          </span>
        )}
        {o.goal_id && (
          <span className="secondary" style={{ fontSize: 12 }}>
            Goal:{" "}
            <Link to={`/goals/${o.goal_id}${qs}`} className="mono" style={{ fontSize: 12 }}>
              {o.goal_id.slice(0, 12)}
            </Link>
          </span>
        )}
        {o.program_id && (
          <Field label="program_id" value={o.program_id} mono />
        )}
        {o.ticket && <Field label="ticket" value={o.ticket} mono />}
        {o.report_ref && <Field label="report_ref" value={o.report_ref} mono />}
      </div>

      {o.workspace_dir && (
        <Field label="workspace" value={o.workspace_dir} mono />
      )}

      {o.pr_url && (
        <span className="secondary" style={{ fontSize: 12 }}>
          PR:{" "}
          <a href={o.pr_url} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 12 }}>
            {o.pr_url.replace("https://github.com/", "")}
          </a>
        </span>
      )}

      {o.failure_class && (
        <Field label="failure_class" value={o.failure_class} mono valueStyle={{ color: "var(--red)" }} />
      )}

      {o.error && (
        <div>
          <span className="eyebrow" style={{ fontSize: 10 }}>error</span>
          <pre
            className="mono"
            style={{
              fontSize: 11.5,
              whiteSpace: "pre-wrap",
              margin: "4px 0 0",
              color: "var(--red)",
              opacity: 0.85,
              maxHeight: 120,
              overflow: "auto",
            }}
          >
            {o.error}
          </pre>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  mono,
  valueStyle,
}: {
  label: string;
  value: string;
  mono?: boolean;
  valueStyle?: React.CSSProperties;
}) {
  return (
    <span style={{ whiteSpace: "nowrap" }}>
      <span className="muted" style={{ fontSize: 11 }}>{label}: </span>
      <span className={mono ? "mono" : undefined} style={{ fontSize: 12, ...valueStyle }}>
        {value}
      </span>
    </span>
  );
}

// ---- Outcomes table with click-to-expand ------------------------------------

function OutcomesTable({ rows }: { rows: EvalOutcome[] }) {
  const [expanded, setExpanded] = useState<number | null>(null);

  const toggle = (id: number) => setExpanded((prev) => (prev === id ? null : id));

  return (
    <div className="card" style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--text-muted)" }}>
            <Th>Source</Th>
            <Th>Ticket / task</Th>
            <Th>Kind</Th>
            <Th>Status</Th>
            <Th>Verify</Th>
            <Th>PR</Th>
            <Th>Failure</Th>
            <Th>Settled</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => {
            const v = verifyGlyph(o.verify_passed);
            const ref = o.ticket ?? o.task_id ?? "—";
            const isOpen = expanded === o.id;
            return (
              <>
                <tr
                  key={o.id}
                  style={{
                    borderTop: "1px solid var(--border)",
                    cursor: "pointer",
                    background: isOpen ? "var(--surface-raised, var(--bg-alt))" : undefined,
                  }}
                  onClick={() => toggle(o.id)}
                  title="Click to expand detail"
                >
                  <Td>
                    <span className="badge">{o.source}</span>
                  </Td>
                  <Td>
                    <span className="mono truncate" style={{ maxWidth: 180, display: "inline-block" }}>
                      {ref}
                    </span>
                  </Td>
                  <Td>{o.kind ? KIND_LABEL[o.kind] ?? o.kind : "—"}</Td>
                  <Td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <StatusDot color={taskStatusColor(o.status)} />
                      {o.status}
                    </span>
                  </Td>
                  <Td>
                    <span style={{ color: v.color }}>{v.text}</span>
                  </Td>
                  <Td>
                    {o.pr_url ? (
                      <a
                        href={o.pr_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mono"
                        onClick={(e) => e.stopPropagation()}
                      >
                        PR
                      </a>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </Td>
                  <Td>
                    {o.failure_class ? (
                      <span className="mono" style={{ color: "var(--red)" }}>
                        {o.failure_class}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </Td>
                  <Td>
                    <span className="mono muted">{relativeTime(o.settled_at)}</span>
                  </Td>
                </tr>
                {isOpen && (
                  <tr key={`${o.id}-detail`}>
                    <td colSpan={8} style={{ padding: 0 }}>
                      <OutcomeDetail o={o} />
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children }: { children: ReactNode }) {
  return (
    <th className="eyebrow" style={{ padding: "10px 12px", fontWeight: 500, whiteSpace: "nowrap" }}>
      {children}
    </th>
  );
}

function Td({ children }: { children: ReactNode }) {
  return <td style={{ padding: "10px 12px", whiteSpace: "nowrap" }}>{children}</td>;
}

// ---- Cycle report row with click-to-expand ----------------------------------

function CycleRow({ n }: { n: CycleReport }) {
  const [expanded, setExpanded] = useState(false);
  const wedges = safeLen(n.wedges_json);
  const pauses = safeLen(n.pauses_json);
  const color = n.clean === 1 ? "var(--green)" : "var(--red)";
  return (
    <div
      className="card"
      style={{ padding: 0, borderLeft: `2px solid ${color}`, cursor: "pointer" }}
      onClick={() => setExpanded((e) => !e)}
      title="Click to expand detail"
    >
      <div style={{ padding: "12px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
          <StatusDot color={color} />
          <span className="mono" style={{ fontWeight: 550, fontSize: 13.5 }}>
            {n.cycle_key}
          </span>
          <span className="badge" style={{ marginLeft: "auto" }}>
            {n.clean === 1 ? "clean" : `${wedges} wedge${wedges === 1 ? "" : "s"}`}
          </span>
          {pauses > 0 && (
            <span className="badge" title="self-healed pauses (not wedges)">
              {pauses} pause{pauses === 1 ? "" : "s"}
            </span>
          )}
          <span className="mono muted" style={{ fontSize: 11 }}>{expanded ? "▲" : "▼"}</span>
        </div>
        <div className="secondary" style={{ fontSize: 12.5, paddingLeft: 17, whiteSpace: "pre-wrap" }}>
          {n.summary || "—"}
        </div>
      </div>

      {expanded && (
        <CycleDetail n={n} onClick={(e) => e.stopPropagation()} />
      )}
    </div>
  );
}

// ---- Cycle detail panel (all stored fields) ----------------------------------

function CycleDetail({ n, onClick }: { n: CycleReport; onClick?: React.MouseEventHandler }) {
  const parsedWedges = safeParse(n.wedges_json);
  const parsedPauses = safeParse(n.pauses_json);

  return (
    <div
      style={{
        padding: "14px 16px",
        borderTop: "1px solid var(--border)",
        fontSize: 12.5,
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
      onClick={onClick}
    >
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <Field label="window_start" value={new Date(n.window_start_ms).toISOString()} mono />
        <Field label="window_end" value={new Date(n.window_end_ms).toISOString()} mono />
        <Field label="clean" value={n.clean === 1 ? "yes" : "no"} />
        <Field label="idle" value={n.idle === 1 ? "yes" : "no"} />
        {n.sent_at != null && (
          <Field label="sent_at" value={relativeTime(n.sent_at)} mono />
        )}
        <Field label="created_at" value={relativeTime(n.created_at)} mono />
      </div>

      {n.summary && (
        <div>
          <span className="eyebrow" style={{ fontSize: 10 }}>summary</span>
          <div className="secondary" style={{ fontSize: 12.5, marginTop: 4, whiteSpace: "pre-wrap" }}>
            {n.summary}
          </div>
        </div>
      )}

      {parsedWedges !== null && parsedWedges.length > 0 && (
        <div>
          <span className="eyebrow" style={{ fontSize: 10 }}>wedges ({parsedWedges.length})</span>
          <pre
            className="mono"
            style={{
              fontSize: 11.5,
              whiteSpace: "pre-wrap",
              margin: "4px 0 0",
              color: "var(--red)",
              maxHeight: 200,
              overflow: "auto",
            }}
          >
            {JSON.stringify(parsedWedges, null, 2)}
          </pre>
        </div>
      )}

      {parsedPauses !== null && parsedPauses.length > 0 && (
        <div>
          <span className="eyebrow" style={{ fontSize: 10 }}>pauses ({parsedPauses.length})</span>
          <pre
            className="mono"
            style={{
              fontSize: 11.5,
              whiteSpace: "pre-wrap",
              margin: "4px 0 0",
              color: "var(--amber)",
              maxHeight: 200,
              overflow: "auto",
            }}
          >
            {JSON.stringify(parsedPauses, null, 2)}
          </pre>
        </div>
      )}

      {(parsedWedges === null || parsedWedges.length === 0) &&
        (parsedPauses === null || parsedPauses.length === 0) && (
          <span className="muted" style={{ fontSize: 12 }}>No wedges or pauses recorded.</span>
        )}
    </div>
  );
}

function safeLen(jsonStr: string): number {
  try {
    const v = JSON.parse(jsonStr);
    return Array.isArray(v) ? v.length : 0;
  } catch {
    return 0;
  }
}

function safeParse(jsonStr: string): unknown[] | null {
  try {
    const v = JSON.parse(jsonStr);
    return Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}
