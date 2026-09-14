import { useState } from "react";
import { Link } from "react-router-dom";
import { tokenQueryString, type VerdictRow } from "../api";
import { relativeTime } from "../util/time";

function word(v: VerdictRow): { text: string; color: string } {
  if (v.unreadable) return { text: "unreadable", color: "var(--amber)" };
  if (v.achieved) return { text: "achieved", color: "var(--green)" };
  return { text: "not achieved", color: "var(--red)" };
}

// The gate's rows, expandable to clause by clause with evidence verbatim.
// Used on the verdicts page (across goals) and on the goal page (its own).
export function VerdictList({ rows, showGoal }: { rows: VerdictRow[]; showGoal?: boolean }) {
  const qs = tokenQueryString();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (id: string) => setOpen((o) => ({ ...o, [id]: !o[id] }));

  return (
    <div>
      {rows.map((v) => {
        const w = word(v);
        const isOpen = !!open[v.taskId];
        return (
          <div key={v.taskId} style={{ borderBottom: "1px solid var(--border)", padding: "10px 0" }}>
            <div className="rowlink" onClick={() => toggle(v.taskId)}
              style={{ display: "grid", gridTemplateColumns: `${showGoal ? "minmax(0,1.4fr) " : ""}110px 90px 80px minmax(0,1fr) 100px`, gap: 12, alignItems: "center", fontSize: 12.5 }}>
              {showGoal && (
                <span className="truncate">
                  {v.goalId ? <Link to={`/goals/${encodeURIComponent(v.goalId)}${qs}`} onClick={(e) => e.stopPropagation()}>{v.goalId}</Link> : "—"}
                </span>
              )}
              <span className="mono" style={{ color: w.color, fontWeight: 600 }}>{w.text}</span>
              <span className="mono secondary">{v.total > 0 ? `${v.satisfied}/${v.total}` : "—"}</span>
              <span className="mono muted">{v.head ? v.head.slice(0, 7) : "—"}</span>
              <span className="truncate secondary">{v.unreadable ? v.rawError : v.summary}</span>
              <span className="mono secondary" style={{ textAlign: "right" }}>{relativeTime(v.completedAt ?? v.createdAt)}</span>
            </div>
            {isOpen && (
              <div style={{ marginTop: 10, paddingLeft: 4, fontSize: 12.5 }}>
                {v.unreadable && <div style={{ color: "var(--amber)" }}>The review produced no readable verdict{v.rawError ? `: ${v.rawError}` : ""}.</div>}
                {v.clauses.map((c, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "18px minmax(0,1fr)", gap: 8, padding: "4px 0" }}>
                    <span className="mono" style={{ color: c.satisfied && c.evidence ? "var(--green)" : "var(--red)" }}>{c.satisfied && c.evidence ? "✓" : "✗"}</span>
                    <span>
                      <div>{c.clause}</div>
                      <div className="mono muted" style={{ fontSize: 11.5, whiteSpace: "pre-wrap" }}>{c.evidence || "no evidence"}</div>
                    </span>
                  </div>
                ))}
                {v.question && <div style={{ marginTop: 8 }}><span className="eyebrow">Question for the owner</span><div>{v.question}</div></div>}
                {(v.structuralHealth || v.concerns.length > 0) && (
                  <div style={{ marginTop: 8 }}>
                    <span className="eyebrow">Structural health</span>
                    <div className="mono">{v.structuralHealth || "—"}</div>
                    {v.concerns.map((c, i) => <div key={i} className="secondary" style={{ fontSize: 12 }}>· {c}</div>)}
                  </div>
                )}
                <div className="mono muted" style={{ fontSize: 11, marginTop: 8, display: "flex", gap: 10 }}>
                  <Link to={`/sessions/${encodeURIComponent(v.taskId)}${qs}`}>review session</Link>
                  {v.prUrl && <a href={v.prUrl} target="_blank" rel="noreferrer">the PR</a>}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
