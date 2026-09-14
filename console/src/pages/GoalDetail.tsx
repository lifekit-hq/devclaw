import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { cancelGoal, decideGoal, fetchGoal, tokenQueryString, type GoalDetail as Detail } from "../api";
import { exitColor, stateColor, stateIsLive } from "../status";
import { relativeTime } from "../util/time";
import { AttentionCard } from "../components/AttentionCard";
import { ErrorNote, Loading, SectionLabel, StatusDot } from "../ui";

function repoIssueUrl(repoUrl: string, n: number): string {
  return `${repoUrl.replace(/\.git$/, "")}/issues/${n}`;
}

export function GoalDetail() {
  const { id = "" } = useParams();
  const qs = tokenQueryString();
  const [goal, setGoal] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => fetchGoal(id).then(setGoal).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const decide = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await decideGoal(id, text.trim());
      setText("");
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm(`Cancel goal ${id}? Its running session is torn down; the branch and PR stay.`)) return;
    setBusy(true);
    try {
      await cancelGoal(id);
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (err && !goal) return <div className="page"><ErrorNote>{err}</ErrorNote></div>;
  if (!goal) return <div className="page"><Loading /></div>;
  const last = goal.lastSession;

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 4px" }}>{goal.objective || goal.id}</h1>
          <div className="mono secondary" style={{ fontSize: 12 }}>
            {goal.id} · <Link to={`/projects/${encodeURIComponent(goal.projectId)}${qs}`}>{goal.projectId}</Link> · <code>{goal.branch}</code>
          </div>
        </div>
        <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 13 }}>
          <StatusDot color={stateColor(goal.outcome ?? goal.state)} live={stateIsLive(goal.state)} />
          {goal.outcome ?? goal.state}
        </span>
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}

      <div className="card" style={{ padding: 16, marginTop: 18 }}>
        <SectionLabel>The contract</SectionLabel>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
          {goal.issues.map((n) => (
            <a key={n} className="btn sm" href={repoIssueUrl(goal.repoUrl, n)} target="_blank" rel="noreferrer">issue #{n}</a>
          ))}
          {last?.prUrl && <a className="btn sm" href={last.prUrl} target="_blank" rel="noreferrer">the PR</a>}
        </div>
      </div>

      {goal.attention && (
        <div className="card" style={{ padding: 16, marginTop: 14 }}>
          <SectionLabel>Needs you</SectionLabel>
          <div style={{ marginTop: 8 }}>
            <AttentionCard goalId={id} a={goal.attention} onDecided={load} onCancel={cancel} />
          </div>
        </div>
      )}

      {!goal.outcome && (
        <div className="card" style={{ padding: 16, marginTop: 14 }}>
          <SectionLabel>Decide</SectionLabel>
          <p className="secondary" style={{ fontSize: 12.5, margin: "6px 0 10px" }}>
            Posted on the issue as an instruction mentioning the bot; the next tick reads it. This is the owner's one verb.
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <input className="input" style={{ flex: 1 }} value={text} onChange={(e) => setText(e.target.value)}
              placeholder="e.g. use SQLite; or: accept the gate's finding as a follow-up and close" disabled={busy} />
            <button className="btn primary" onClick={decide} disabled={busy || !text.trim()}>Decide</button>
            <button className="btn ghost" onClick={cancel} disabled={busy}>Cancel goal</button>
          </div>
        </div>
      )}

      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <SectionLabel>Sessions</SectionLabel>
        {goal.sessions.length === 0 && <p className="secondary" style={{ fontSize: 12.5 }}>No session yet.</p>}
        {goal.sessions.map((s) => (
          <div key={s.id} style={{ display: "grid", gridTemplateColumns: "150px 110px minmax(0,1fr) 90px", gap: 12, padding: "8px 0", borderBottom: "1px solid var(--border)", fontSize: 12.5, alignItems: "center" }}>
            <span className="mono secondary">{relativeTime(s.createdAt)} · {s.kind === "review_repository" ? "review" : "session"}</span>
            <span className="mono" style={{ color: exitColor(s.exit) }}>{s.exit ?? s.status}</span>
            <span className="truncate">{s.exitDetail || ""}</span>
            <Link className="mono truncate" style={{ fontSize: 11 }} to={`/sessions/${encodeURIComponent(s.id)}${qs}`}>{s.id.slice(0, 8)} →</Link>
          </div>
        ))}
      </div>

      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <SectionLabel>Decisions</SectionLabel>
        {goal.decisions.length === 0 && <p className="secondary" style={{ fontSize: 12.5 }}>None.</p>}
        {goal.decisions.map((d) => (
          <div key={d.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border)", fontSize: 12.5 }}>
            <span className="mono secondary">{relativeTime(d.madeAt)}</span> — {d.text}
            {d.commentUrl && <> · <a href={d.commentUrl} target="_blank" rel="noreferrer">thread</a></>}
          </div>
        ))}
      </div>

      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <SectionLabel>Last world the session was given</SectionLabel>
        <pre className="mono" style={{ fontSize: 11.5, margin: "8px 0 0", whiteSpace: "pre-wrap" }}>
          {goal.lastSeen ? JSON.stringify(goal.lastSeen, null, 2) : "—"}
        </pre>
      </div>
    </div>
  );
}
