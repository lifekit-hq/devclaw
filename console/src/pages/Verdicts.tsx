import { useEffect, useState } from "react";
import { fetchVerdicts, type VerdictFeed } from "../api";
import { VerdictList } from "../components/VerdictList";
import { EmptyState, ErrorNote, Loading } from "../ui";

// Every done-gate review across goals, newest first: the live read of the
// "ran and produced garbage" axis — is the gate refusing on the same kind of
// clause again and again?
export function Verdicts() {
  const [feed, setFeed] = useState<VerdictFeed | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => fetchVerdicts(200).then((f) => alive && setFeed(f)).catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 4px" }}>Verdicts</h1>
      <p className="secondary" style={{ margin: "0 0 20px", fontSize: 13.5 }}>
        What the done-gate found, clause by clause, read from each review session's own output. Click a row to expand.
      </p>
      {err && <ErrorNote>{err}</ErrorNote>}
      {!feed && !err && <Loading />}
      {feed && feed.count === 0 && <EmptyState title="No reviews yet" hint="A verdict appears when a session proposes DONE and CI is green." />}
      {feed && feed.count > 0 && (
        <div className="card" style={{ padding: "4px 16px" }}>
          <VerdictList rows={feed.verdicts} showGoal />
          {feed.truncated && <p className="muted" style={{ fontSize: 12, margin: "10px 0 6px" }}>Showing the newest {feed.count}; older reviews are not listed.</p>}
        </div>
      )}
    </div>
  );
}
