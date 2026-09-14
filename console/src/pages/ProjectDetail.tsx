import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { fetchProject, tokenQueryString, type ProjectRow } from "../api";
import { exitColor, stateColor, stateIsLive } from "../status";
import { relativeTime } from "../util/time";
import { ErrorNote, Loading, SectionLabel, StatusDot, UsageChip } from "../ui";

const COLS = "minmax(0,1.6fr) 130px minmax(0,1fr) 110px";

export function ProjectDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qs = tokenQueryString();
  const [p, setP] = useState<ProjectRow | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => fetchProject(id).then((r) => alive && setP(r)).catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [id]);

  if (err && !p) return <div className="page"><ErrorNote>{err.includes("404") ? `No such project: ${id}` : err}</ErrorNote></div>;
  if (!p) return <div className="page"><Loading /></div>;

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 4px" }}>{p.name}</h1>
          <div className="mono secondary" style={{ fontSize: 12 }}>
            {p.id} · {p.status}
            {p.repoUrl && <> · <a href={p.repoUrl.replace(/\.git$/, "")} target="_blank" rel="noreferrer">{p.repoUrl.replace(/\.git$/, "")}</a></>}
          </div>
          <div className="mono muted" style={{ fontSize: 11.5, marginTop: 2 }}>{p.workspaceDir || "no workspace"}</div>
          <div style={{ marginTop: 4 }}><UsageChip usage={p.usage} /></div>
        </div>
        <span className="badge">{p.health}</span>
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}

      <div className="card" style={{ marginTop: 18, overflow: "hidden" }}>
        <div style={{ padding: "12px 16px 0" }}><SectionLabel count={p.goals.length}>Goals</SectionLabel></div>
        {p.goals.length === 0 && <p className="secondary" style={{ fontSize: 12.5, padding: "8px 16px 16px" }}>No goals on this project.</p>}
        {p.goals.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: COLS, gap: 16, padding: "10px 16px", borderBottom: "1px solid var(--border)" }}>
            <span className="eyebrow">Goal</span>
            <span className="eyebrow">State</span>
            <span className="eyebrow">Last session</span>
            <span className="eyebrow" style={{ textAlign: "right" }}>Needs</span>
          </div>
        )}
        {p.goals.map((g) => (
          <div key={g.id} className="rowlink" onClick={() => nav(`/goals/${encodeURIComponent(g.id)}${qs}`)}
            style={{ display: "grid", gridTemplateColumns: COLS, gap: 16, alignItems: "center", padding: "13px 16px", borderBottom: "1px solid var(--border)", opacity: g.outcome ? 0.6 : 1 }}>
            <div style={{ minWidth: 0 }}>
              <div className="truncate" style={{ fontWeight: 550, fontSize: 13 }}>{g.objective || g.id}</div>
              <div className="mono muted truncate" style={{ fontSize: 11, marginTop: 2 }}>{g.id}{g.issues.length > 0 && ` · #${g.issues.join(", #")}`}</div>
            </div>
            <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12.5 }}>
              <StatusDot color={stateColor(g.outcome ?? g.state)} live={stateIsLive(g.state)} />
              {g.outcome ?? g.state}
            </span>
            <span className="mono truncate" style={{ fontSize: 12, color: exitColor(g.lastSession?.exit) }}>
              {g.lastSession ? `${g.lastSession.exit ?? g.lastSession.status} · ${relativeTime(g.lastSession.completedAt ?? g.lastSession.createdAt)}` : "—"}
            </span>
            <span className="mono secondary" style={{ textAlign: "right", fontSize: 12 }}>{g.attentionKind ? "you" : ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
