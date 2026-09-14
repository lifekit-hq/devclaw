import { useState } from "react";
import { decideGoal, type Attention } from "../api";
import { relativeTime } from "../util/time";

// One goal's ask, rendered the same on the needs-you page and the goal page.
// Buttons exist only for a session block: they are the session's options,
// the recommended one first. A host-authored stop shows its fact, a free-text
// decide and cancel — never options the host made up.
export function AttentionCard({
  goalId, a, onDecided, onCancel,
}: {
  goalId: string; a: Attention; onDecided: () => Promise<void> | void; onCancel?: () => Promise<void> | void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const decide = async (t: string) => {
    if (!t.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await decideGoal(goalId, t.trim());
      setText("");
      await onDecided();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const order = a.options.map((o, i) => ({ o, i })).sort((x, y) => (x.i === a.recommended ? -1 : y.i === a.recommended ? 1 : x.i - y.i));
  const showDefault = a.default && a.recommended < 0;

  if (a.answered) {
    return (
      <div>
        <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{a.question}</div>
        <div className="secondary" style={{ fontSize: 12.5, marginTop: 8 }}>
          Answered {relativeTime(a.answered.madeAt)}: <span style={{ color: "var(--text)" }}>{a.answered.text}</span>
          {a.answered.commentUrl && <> · <a href={a.answered.commentUrl} target="_blank" rel="noreferrer">thread</a></>}
        </div>
        <div className="mono muted" style={{ fontSize: 11.5, marginTop: 4 }}>waiting for the tick · {a.answered.waitingOn}</div>
      </div>
    );
  }

  return (
    <div>
      <div className="mono muted" style={{ fontSize: 11, marginBottom: 6 }}>
        {a.kind} · open {relativeTime(a.since)}
        {a.link && <> · <a href={a.link} target="_blank" rel="noreferrer">{a.kind === "session" || a.kind === "env" ? "issue" : "thread"}</a></>}
      </div>
      <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{a.question}</div>
      {a.kind === "env" && <p className="secondary" style={{ fontSize: 12.5, margin: "8px 0 0" }}>Wakes on its own once the credential probes green.</p>}
      {a.kind !== "env" && (
        <>
          {(order.length > 0 || showDefault) && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
              {order.map(({ o, i }) => (
                <button key={i} className={`btn sm${i === a.recommended ? " primary" : ""}`} disabled={busy} onClick={() => decide(o)} title={o}>
                  <span className="truncate" style={{ maxWidth: 360 }}>{o.split("\n")[0]}</span>
                  {i === a.recommended && <span className="mono" style={{ opacity: 0.75, fontSize: 10.5 }}>recommended</span>}
                </button>
              ))}
              {showDefault && (
                <button className="btn sm primary" disabled={busy} onClick={() => decide(a.default)} title={a.default}>
                  take the default: <span className="truncate" style={{ maxWidth: 320 }}>{a.default}</span>
                </button>
              )}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <input className="input" style={{ flex: 1 }} value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
              placeholder={order.length > 0 ? "or answer in your own words" : "your decision, posted on the issue"} />
            <button className="btn sm" onClick={() => decide(text)} disabled={busy || !text.trim()}>Decide</button>
            {onCancel && <button className="btn ghost sm" onClick={() => onCancel()} disabled={busy}>Cancel goal</button>}
          </div>
        </>
      )}
      {err && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8 }}>{err}</div>}
    </div>
  );
}
