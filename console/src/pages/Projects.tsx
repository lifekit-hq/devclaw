import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchProjects, tokenQueryString, type ProjectRow } from "../api";
import { EmptyState, ErrorNote, Loading } from "../ui";

export function Projects() {
  const [rows, setRows] = useState<ProjectRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const qs = tokenQueryString();
  useEffect(() => {
    fetchProjects().then(setRows).catch((e) => setErr(String(e)));
  }, []);
  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 4px" }}>Projects</h1>
      <p className="secondary" style={{ margin: "0 0 20px", fontSize: 13.5 }}>The repositories devclaw works on.</p>
      {err && <ErrorNote>{err}</ErrorNote>}
      {!rows && !err && <Loading />}
      {rows && rows.length === 0 && <EmptyState title="No projects" hint="register_project from the waiter or the CLI." />}
      {rows && rows.map((p) => (
        <div key={p.id} className="card" style={{ padding: 16, marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>
                <Link to={`/projects/${encodeURIComponent(p.id)}${qs}`}>{p.name}</Link> <span className="mono muted" style={{ fontSize: 11 }}>{p.id}</span>
              </div>
              <div className="mono secondary" style={{ fontSize: 12, marginTop: 2 }}>{p.repoUrl || "no repo_url"} · {p.workspaceDir || "no workspace"}</div>
            </div>
            <span className="badge">{p.health}</span>
          </div>
          {p.goals.length > 0 && (
            <div className="mono secondary" style={{ fontSize: 12, marginTop: 8, display: "flex", gap: 10, flexWrap: "wrap" }}>
              {p.goals.map((g) => (
                <Link key={g.id} to={`/goals/${encodeURIComponent(g.id)}${qs}`}>{g.id} ({g.outcome ?? g.state}{g.attentionKind ? ", needs you" : ""})</Link>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
