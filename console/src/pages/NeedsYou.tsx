import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cancelGoal, fetchGoals, tokenQueryString, type GoalRow } from "../api";
import { AttentionCard } from "../components/AttentionCard";
import { EmptyState, ErrorNote, Loading, SectionLabel } from "../ui";

// The owner's one page: every goal waiting on a decision, oldest first, then
// the ones already answered and waiting for the tick to read the answer.
export function NeedsYou() {
  const nav = useNavigate();
  const qs = tokenQueryString();
  const [rows, setRows] = useState<GoalRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => fetchGoals().then(setRows).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const cancel = async (id: string) => {
    if (!window.confirm(`Cancel goal ${id}? Its running session is torn down; the branch and PR stay.`)) return;
    try {
      await cancelGoal(id);
      await load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const withAsk = (rows ?? []).filter((g) => g.attention);
  const open = withAsk.filter((g) => !g.attention!.answered).sort((a, b) => a.attention!.since - b.attention!.since);
  const answered = withAsk.filter((g) => g.attention!.answered);

  const card = (g: GoalRow) => (
    <div key={g.id} className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 8, flexWrap: "wrap" }}>
        <a className="truncate" style={{ fontWeight: 550, fontSize: 13, cursor: "pointer" }} onClick={() => nav(`/goals/${encodeURIComponent(g.id)}${qs}`)}>
          {g.objective || g.id}
        </a>
        <span className="mono muted" style={{ fontSize: 11 }}>{g.id} · {g.projectId}</span>
      </div>
      <AttentionCard goalId={g.id} a={g.attention!} onDecided={load} onCancel={() => cancel(g.id)} />
    </div>
  );

  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 4px" }}>Needs you</h1>
      <p className="secondary" style={{ margin: "0 0 20px", fontSize: 13.5 }}>
        A click posts your decision on the issue; the next tick reads it. The options are the session's own.
      </p>
      {err && <ErrorNote>{err}</ErrorNote>}
      {!rows && !err && <Loading />}
      {rows && withAsk.length === 0 && <EmptyState title="Nothing needs you" hint="Every open goal is running, waiting on the world, or answered." />}
      {open.length > 0 && (
        <>
          <SectionLabel>Waiting on you · {open.length}</SectionLabel>
          <div style={{ marginTop: 8 }}>{open.map(card)}</div>
        </>
      )}
      {answered.length > 0 && (
        <>
          <SectionLabel>Answered, waiting for the tick · {answered.length}</SectionLabel>
          <div style={{ marginTop: 8 }}>{answered.map(card)}</div>
        </>
      )}
    </div>
  );
}
