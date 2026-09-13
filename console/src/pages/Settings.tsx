import { useEffect, useState } from "react";
import { fetchControl, pauseDispatch, resumeDispatch, setSchedule, type ControlState } from "../api";
import { ErrorNote, Loading, SectionLabel } from "../ui";

export function Settings() {
  const [ctrl, setCtrl] = useState<ControlState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ enabled: false, start: "22:00", end: "05:00", tz: "Europe/Dublin" });

  const load = () =>
    fetchControl()
      .then((c) => {
        setCtrl(c);
        setForm(c.schedule);
      })
      .catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
  }, []);

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      setErr(String(e));
    }
  };

  if (!ctrl) return <div className="page">{err ? <ErrorNote>{err}</ErrorNote> : <Loading />}</div>;
  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 20px" }}>Settings</h1>
      {err && <ErrorNote>{err}</ErrorNote>}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <SectionLabel>Dispatch</SectionLabel>
        <p className="secondary" style={{ fontSize: 12.5, margin: "6px 0 10px" }}>
          {ctrl.operatorHold.on ? `Held: ${ctrl.operatorHold.reason || "(no reason)"}` : ctrl.pause ? `Paused until ${new Date(ctrl.pause.untilMs).toLocaleString()} — ${ctrl.pause.reason}` : ctrl.blocked ? `Off-hours: ${ctrl.whyBlocked}` : "Open."}
        </p>
        {ctrl.operatorHold.on ? (
          <button className="btn primary" onClick={() => run(resumeDispatch)}>Release the hold</button>
        ) : (
          <button className="btn" onClick={() => run(() => pauseDispatch("held from the console"))}>Hold all new sessions</button>
        )}
      </div>
      <div className="card" style={{ padding: 16 }}>
        <SectionLabel>Run window</SectionLabel>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 8 }}>
          <label style={{ fontSize: 12.5 }}>
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> enabled
          </label>
          <input className="input" style={{ width: 80 }} value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
          <span>→</span>
          <input className="input" style={{ width: 80 }} value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
          <input className="input" style={{ width: 160 }} value={form.tz} onChange={(e) => setForm({ ...form, tz: e.target.value })} />
          <button className="btn primary" onClick={() => run(() => setSchedule(form))}>Save</button>
        </div>
        <p className="secondary" style={{ fontSize: 12, marginTop: 8 }}>Disabled = 24/7. In-flight sessions always finish.</p>
      </div>
    </div>
  );
}
