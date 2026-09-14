import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchGoals, tokenQueryString, type GoalRow } from "../api";
import { exitColor, stateColor, stateIsLive } from "../status";
import { relativeTime } from "../util/time";
import { EmptyState, ErrorNote, Loading, StatusDot } from "../ui";

type Filter = "open" | "blocked" | "closed" | "all";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "blocked", label: "Blocked" },
  { id: "closed", label: "Closed" },
  { id: "all", label: "All" },
];

function match(g: GoalRow, f: Filter): boolean {
  if (f === "all") return true;
  if (f === "closed") return !!g.outcome;
  if (f === "blocked") return g.state === "blocked";
  return !g.outcome;
}

const COLS = "minmax(0,1.6fr) 130px 130px minmax(0,1fr) 110px";

export function Goals() {
  const nav = useNavigate();
  const qs = tokenQueryString();
  const [rows, setRows] = useState<GoalRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("open");

  useEffect(() => {
    let alive = true;
    const load = () => fetchGoals().then((r) => alive && setRows(r)).catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { open: 0, blocked: 0, closed: 0, all: 0 };
    for (const g of rows ?? []) for (const f of FILTERS) if (match(g, f.id)) c[f.id]++;
    return c;
  }, [rows]);

  const shown = (rows ?? []).filter((g) => match(g, filter));

  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 4px" }}>Goals</h1>
      <p className="secondary" style={{ margin: "0 0 20px", fontSize: 13.5 }}>
        One issue, one branch, one PR. The thread on GitHub is the record; this is the index.
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        {FILTERS.map((f) => (
          <button key={f.id} className={`btn sm${filter === f.id ? " primary" : ""}`} onClick={() => setFilter(f.id)}>
            {f.label}
            <span className="mono" style={{ opacity: 0.7, fontSize: 11 }}>{counts[f.id]}</span>
          </button>
        ))}
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}
      {!rows && !err && <Loading />}
      {rows && shown.length === 0 && <EmptyState title="Nothing here" hint="No goals match this filter." />}
      {shown.length > 0 && (
        <div className="card" style={{ overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: COLS, gap: 16, padding: "10px 16px", borderBottom: "1px solid var(--border)" }}>
            <span className="eyebrow">Goal</span>
            <span className="eyebrow">Project</span>
            <span className="eyebrow">State</span>
            <span className="eyebrow">Last session</span>
            <span className="eyebrow" style={{ textAlign: "right" }}>Seen</span>
          </div>
          {shown.map((g) => (
            <div key={g.id} className="rowlink" onClick={() => nav(`/goals/${encodeURIComponent(g.id)}${qs}`)}
              style={{ display: "grid", gridTemplateColumns: COLS, gap: 16, alignItems: "center", padding: "13px 16px", borderBottom: "1px solid var(--border)", opacity: g.outcome ? 0.6 : 1 }}>
              <div style={{ minWidth: 0 }}>
                <div className="truncate" style={{ fontWeight: 550, fontSize: 13 }}>{g.objective || g.id}</div>
                <div className="mono muted truncate" style={{ fontSize: 11, marginTop: 2 }}>{g.id} · #{g.issues.join(", #")}</div>
              </div>
              <span className="truncate secondary" style={{ fontSize: 12.5 }}>{g.projectId}</span>
              <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12.5 }}>
                <StatusDot color={stateColor(g.outcome ?? g.state)} live={stateIsLive(g.state)} />
                {g.outcome ?? g.state}
              </span>
              <span className="mono truncate" style={{ fontSize: 12, color: exitColor(g.lastSession?.exit) }}
                onClick={(e) => { if (g.lastSession) { e.stopPropagation(); nav(`/sessions/${encodeURIComponent(g.lastSession.id)}${qs}`); } }}
                title={g.lastSession ? "open the session" : undefined}>
                {g.lastSession ? `${g.lastSession.exit ?? g.lastSession.status}${g.lastSession.exitDetail ? ": " + g.lastSession.exitDetail : ""}` : "—"}
              </span>
              <span className="mono secondary" style={{ textAlign: "right", fontSize: 12.5 }}>{relativeTime(g.lastSeenAt)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
