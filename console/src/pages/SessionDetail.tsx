import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchTask, fetchTaskEvents, tokenQueryString, type TaskDetail, type TaskEvent } from "../api";
import { exitColor } from "../status";
import { relativeTime } from "../util/time";
import { ErrorNote, Loading, SectionLabel } from "../ui";

function when(ms: number | null): string {
  return ms ? `${new Date(ms).toISOString().replace("T", " ").slice(0, 19)}Z (${relativeTime(ms)})` : "—";
}

function Part({ title, value }: { title: string; value: unknown }) {
  const absent = value === null || value === undefined || (typeof value === "object" && Object.keys(value as object).length === 0);
  return (
    <div className="card" style={{ padding: 16, marginTop: 14 }}>
      <SectionLabel>{title}</SectionLabel>
      {absent ? (
        <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>not recorded</p>
      ) : typeof value === "string" ? (
        <pre className="mono" style={{ fontSize: 11.5, margin: "8px 0 0", whiteSpace: "pre-wrap", maxHeight: 480, overflow: "auto" }}>{value}</pre>
      ) : (
        <pre className="mono" style={{ fontSize: 11.5, margin: "8px 0 0", whiteSpace: "pre-wrap", maxHeight: 480, overflow: "auto" }}>{JSON.stringify(value, null, 2)}</pre>
      )}
    </div>
  );
}

function payloadSummary(e: TaskEvent): string {
  try {
    const p = JSON.parse(e.payloadJson);
    if (p && typeof p === "object") {
      const keys = Object.keys(p);
      const head = keys.slice(0, 4).map((k) => `${k}=${typeof p[k] === "string" ? String(p[k]).slice(0, 80) : JSON.stringify(p[k]).slice(0, 80)}`);
      return head.join("  ") + (keys.length > 4 ? "  …" : "");
    }
    return String(e.payloadJson).slice(0, 160);
  } catch {
    return String(e.payloadJson).slice(0, 160);
  }
}

export function SessionDetail() {
  const { id = "" } = useParams();
  const qs = tokenQueryString();
  const [d, setD] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchTask(id).then((r) => alive && setD(r)).catch((e) => alive && setErr(String(e)));
    fetchTaskEvents(id)
      .then((r) => {
        if (!alive) return;
        setEvents(r.events);
        setCursor(r.nextCursor);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [id]);

  const more = async () => {
    if (!cursor) return;
    const r = await fetchTaskEvents(id, cursor);
    setEvents((ev) => [...ev, ...r.events]);
    setCursor(r.nextCursor);
  };

  if (err && !d) return <div className="page"><ErrorNote>{err.includes("404") ? `No such session: ${id}` : err}</ErrorNote></div>;
  if (!d) return <div className="page"><Loading /></div>;
  const t = d.task;

  return (
    <div className="page">
      <div style={{ minWidth: 0 }}>
        <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 4px" }}>
          {t.kind === "review_repository" ? "Review session" : "Session"} <span className="mono muted" style={{ fontSize: 13 }}>{t.id.slice(0, 8)}</span>
        </h1>
        <div className="mono secondary" style={{ fontSize: 12, display: "flex", gap: 10, flexWrap: "wrap" }}>
          {t.parentGoalId && <Link to={`/goals/${encodeURIComponent(t.parentGoalId)}${qs}`}>goal {t.parentGoalId}</Link>}
          {t.projectId && <Link to={`/projects/${encodeURIComponent(t.projectId)}${qs}`}>project {t.projectId}</Link>}
          {t.prUrl && <a href={t.prUrl} target="_blank" rel="noreferrer">the PR</a>}
        </div>
      </div>

      <div className="card" style={{ padding: 16, marginTop: 18 }}>
        <SectionLabel>Exit</SectionLabel>
        <div style={{ marginTop: 8, fontSize: 13 }}>
          <span className="mono" style={{ color: exitColor(t.exit), fontWeight: 600 }}>{t.exit ?? t.status}</span>
          {t.exitDetail && <span style={{ marginLeft: 10, whiteSpace: "pre-wrap" }}>{t.exitDetail}</span>}
        </div>
        {t.error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8, whiteSpace: "pre-wrap" }}>{t.error}</div>}
        <div className="mono secondary" style={{ fontSize: 11.5, marginTop: 10, display: "grid", gridTemplateColumns: "120px minmax(0,1fr)", rowGap: 3 }}>
          <span>status</span><span>{t.status}</span>
          <span>created</span><span>{when(t.createdAt)}</span>
          <span>started</span><span>{when(t.startedAt)}</span>
          <span>completed</span><span>{when(t.completedAt)}</span>
          <span>pre-run sha</span><span>{t.preRunSha || "not recorded"}</span>
          <span>branch</span><span>{t.targetBranch || "not recorded"}</span>
          <span>workspace</span><span>{t.workspaceDir}</span>
          <span>delivers</span><span>{t.deliver ? "yes" : "no"}</span>
        </div>
      </div>

      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <SectionLabel>Brief the session was given</SectionLabel>
        <pre className="mono" style={{ fontSize: 11.5, margin: "8px 0 0", whiteSpace: "pre-wrap", maxHeight: 360, overflow: "auto" }}>{t.goal || "not recorded"}</pre>
      </div>

      {d.block && (
        <div className="card" style={{ padding: 16, marginTop: 14 }}>
          <SectionLabel>Block</SectionLabel>
          <div style={{ fontSize: 13, marginTop: 8, whiteSpace: "pre-wrap" }}>{d.block.question}</div>
          {d.block.options.length > 0 && (
            <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
              {d.block.options.map((o, i) => <li key={i}>{o}{i === d.block!.recommended && <span className="mono muted"> — the session would take this</span>}</li>)}
            </ul>
          )}
          {d.block.default && d.block.recommended < 0 && <div className="secondary" style={{ fontSize: 12.5, marginTop: 8 }}>default: {d.block.default}</div>}
          {d.block.kind === "env" && <div className="secondary" style={{ fontSize: 12.5, marginTop: 8 }}>environment gap: {d.block.item}</div>}
        </div>
      )}

      <Part title="Verify" value={d.verify} />
      <Part title="Delivery" value={d.delivery} />
      <Part title="Change span" value={d.change} />
      <Part title="Agent output" value={d.agentOutput} />

      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <SectionLabel count={events.length}>Events</SectionLabel>
        {events.length === 0 && <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>not recorded</p>}
        {events.map((e) => (
          <div key={e.id} style={{ display: "grid", gridTemplateColumns: "150px 120px 90px minmax(0,1fr)", gap: 12, padding: "7px 0", borderBottom: "1px solid var(--border)", fontSize: 12, alignItems: "baseline" }}>
            <span className="mono secondary">{when(e.ts).slice(0, 20)}</span>
            <span className="mono">{e.type}</span>
            <span className="mono muted">{e.source}</span>
            <span className="mono truncate" title={e.payloadJson}>{payloadSummary(e)}</span>
          </div>
        ))}
        {cursor && <button className="btn sm" style={{ marginTop: 10 }} onClick={more}>load more</button>}
      </div>
    </div>
  );
}
