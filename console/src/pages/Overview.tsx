import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  fetchAllGoals,
  fetchLoopHealth,
  fetchProjects,
  tokenQueryString,
  type GoalWithProject,
  type LoopHealth,
} from "../api";
import { phaseColor, phaseIsLive } from "../status";
import { relativeTime } from "../util/time";
import { EmptyState, ErrorNote, Loading, SectionLabel, StatusDot } from "../ui";

export function Overview() {
  const nav = useNavigate();
  const qs = tokenQueryString();
  const [rows, setRows] = useState<GoalWithProject[] | null>(null);
  const [projectCount, setProjectCount] = useState<number | null>(null);
  const [health, setHealth] = useState<LoopHealth | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.all([fetchAllGoals(), fetchProjects()])
        .then(([goals, projs]) => {
          if (!alive) return;
          setRows(goals);
          setProjectCount(projs.length);
        })
        .catch((e) => alive && setErr(String(e)));
      // Separate and best-effort: an older server has no /loop-health.json and
      // the page renders without the strip rather than erroring on the lot.
      fetchLoopHealth()
        .then((h) => alive && setHealth(h))
        .catch(() => alive && setHealth(null));
    };
    load();
    const t = setInterval(load, 20000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const live = (rows ?? []).filter((g) => phaseIsLive(g.phase));
  // Backend-computed flag — the same predicate the Node page's count uses.
  // Filtering on phase === "blocked" here is how the Overview said "All
  // clear" over a dozen stalled/needs_human goals.
  const blocked = (rows ?? []).filter((g) => !g.archived && g.needsYou);
  const projects = projectCount ?? 0;
  const recent = [...(rows ?? [])]
    .filter((g) => !g.archived)
    .sort((a, b) => (b.lastUpdateMs ?? 0) - (a.lastUpdateMs ?? 0))
    .slice(0, 6);

  const open = (id: string) => nav(`/goals/${id}${qs}`);

  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 18px" }}>
        Overview
      </h1>

      {err && <ErrorNote>{err}</ErrorNote>}
      {!rows && !err && <Loading />}

      {rows && projectCount !== null && (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 30 }}>
            <Stat label="Projects" value={projects} />
            <Stat label="Running" value={live.length} color="var(--accent)" live={live.length > 0} />
            <Stat label="Needs you" value={blocked.length} color={blocked.length ? "var(--amber)" : "var(--text-muted)"} />
          </div>

          {health && <LoopHealthStrip h={health} />}

          <section style={{ marginBottom: 34 }}>
            <SectionLabel count={blocked.length}>Needs you</SectionLabel>
            {blocked.length === 0 ? (
              <div className="card">
                <EmptyState title="All clear" hint="Nothing is waiting on you right now." />
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {blocked.map((g) => (
                  <GoalCard key={g.id} g={g} onOpen={() => open(g.id)} tone="amber" />
                ))}
              </div>
            )}
          </section>

          <section style={{ marginBottom: 34 }}>
            <SectionLabel count={live.length}>Running now</SectionLabel>
            {live.length === 0 ? (
              <div className="card">
                <EmptyState title="Idle" hint="No goals are executing at the moment." />
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {live.map((g) => (
                  <GoalCard key={g.id} g={g} onOpen={() => open(g.id)} tone="accent" />
                ))}
              </div>
            )}
          </section>

          <section>
            <SectionLabel>Recently active</SectionLabel>
            <div className="card" style={{ overflow: "hidden" }}>
              {recent.map((g) => (
                <div
                  key={g.id}
                  className="rowlink"
                  onClick={() => open(g.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    padding: "12px 16px",
                    borderBottom: "1px solid var(--border)",
                  }}
                >
                  <StatusDot color={phaseColor(g.phase)} live={phaseIsLive(g.phase)} />
                  <span className="truncate" style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{g.objective || g.id}</span>
                  <span className="secondary truncate" style={{ fontSize: 12, maxWidth: 160 }}>{g.projectName}</span>
                  <span className="mono muted" style={{ fontSize: 12 }}>{relativeTime(g.lastUpdateMs)}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, color, live }: { label: string; value: number; color?: string; live?: boolean }) {
  return (
    <div className="card" style={{ padding: "14px 18px", minWidth: 130, flex: "1 1 130px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {color && <StatusDot color={color} live={live} />}
        <span style={{ fontSize: 26, fontWeight: 650, letterSpacing: "-0.02em" }}>{value}</span>
      </div>
      <div className="eyebrow" style={{ marginTop: 4 }}>{label}</div>
    </div>
  );
}

function GoalCard({ g, onOpen, tone }: { g: GoalWithProject; onOpen: () => void; tone: "amber" | "accent" }) {
  const border = tone === "amber" ? "var(--amber)" : "var(--accent)";
  return (
    <div
      className="card rowlink"
      onClick={onOpen}
      style={{ padding: "14px 16px", borderLeft: `2px solid ${border}` }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 5 }}>
        <StatusDot color={phaseColor(g.phase)} live={phaseIsLive(g.phase)} />
        <span className="truncate" style={{ fontWeight: 600, fontSize: 14, flex: 1 }}>
          {g.objective || g.id}
        </span>
        <span className="badge" style={{ marginLeft: "auto", flexShrink: 0 }}>{g.phaseLabel}</span>
      </div>
      <div
        className="secondary truncate"
        style={{ fontSize: 12, paddingLeft: 17, display: "flex", gap: 8, alignItems: "center" }}
      >
        <span className="mono muted">{g.id}</span>
        <span>· {g.projectName}</span>
        {g.action && g.action !== "—" && <span className="truncate">· {g.action}</span>}
      </div>
    </div>
  );
}

// ---- Loop health strip (spec 039 US5) --------------------------------------
// The three north-star failures, on the page the owner already opens: stopped
// when it shouldn't (not-stuck rate + where the idle went), ran and produced
// garbage (first-pass, clean cycles), ran but needed the owner (self-heal).
// Every rate is null on an empty sample and renders "—" with a reason — never
// 0%, which reads as a catastrophe rather than as no data.

const BUCKET_LABEL: Record<string, string> = {
  devclaw: "devclaw's fault",
  owner: "your turn",
  no_work: "no work",
};
const BUCKET_COLOR: Record<string, string> = {
  devclaw: "var(--red)",
  owner: "var(--amber)",
  no_work: "var(--text-muted)",
};

function hours(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function ratePct(r: number | null): string {
  return r === null ? "—" : `${Math.round(r * 100)}%`;
}

/** Green/amber/red against the ratchet's own thresholds where there is one,
 *  and muted when the sample is empty — an unknown is never coloured as bad. */
function healthColor(r: number | null, good: number, ok: number): string {
  if (r === null) return "var(--text-muted)";
  if (r >= good) return "var(--green)";
  if (r >= ok) return "var(--amber)";
  return "var(--red)";
}

function LoopHealthStrip({ h }: { h: LoopHealth }) {
  const { idle, self_heal, clean_cycle, first_pass } = h;
  const total = idle.buckets.devclaw + idle.buckets.owner + idle.buckets.no_work;
  const days = Math.round(h.window_hours / 24);

  return (
    <section style={{ marginBottom: 34 }}>
      <SectionLabel
        right={
          <span className="mono muted" style={{ fontSize: 11 }}>
            last {days === 1 ? "24h" : `${days}d`}
          </span>
        }
      >
        Loop health
      </SectionLabel>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
        <HealthTile
          label="Not stuck"
          value={ratePct(idle.not_stuck_rate)}
          color={healthColor(idle.not_stuck_rate, 0.9, 0.7)}
          sub={
            idle.observed_seconds
              ? `${hours(idle.working_seconds)} working of ${hours(idle.observed_seconds)}`
              : "nothing observed yet"
          }
        />
        <HealthTile
          label="First pass"
          value={ratePct(first_pass.rate)}
          color={healthColor(first_pass.rate, 0.7, 0.5)}
          sub={
            first_pass.goals_closed
              ? `${first_pass.first_pass} / ${first_pass.goals_closed} goals closed` +
                (first_pass.rounds_median !== null ? ` · median ${first_pass.rounds_median} rounds` : "")
              : "no goals closed in window"
          }
        />
        <HealthTile
          label="Clean cycles"
          value={ratePct(clean_cycle.rate)}
          color={healthColor(clean_cycle.rate, 1, 0.8)}
          sub={clean_cycle.total ? `${clean_cycle.clean} / ${clean_cycle.total} scored` : "no scored cycles"}
        />
        <HealthTile
          label="Self-heal"
          value={ratePct(self_heal.rate)}
          color={healthColor(self_heal.rate, 0.7, 0.4)}
          sub={
            self_heal.problems
              ? `${self_heal.recovered} recovered · ${self_heal.terminal} terminal`
              : "no problems in window"
          }
        />
      </div>

      {/* Where the idle went — the "stopped when it shouldn't" axis, by whose
          fault it was. Only devclaw's share lowers the not-stuck rate. */}
      {total > 0 && (
        <div className="card" style={{ padding: "12px 16px" }}>
          <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", marginBottom: 10 }}>
            {(["devclaw", "owner", "no_work"] as const).map((b) => {
              const s = idle.buckets[b];
              return s > 0 ? (
                <div key={b} style={{ width: `${(s / total) * 100}%`, background: BUCKET_COLOR[b] }} title={`${BUCKET_LABEL[b]}: ${hours(s)}`} />
              ) : null;
            })}
          </div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 11.5 }}>
            {(["devclaw", "owner", "no_work"] as const).map((b) => (
              <span key={b} className="secondary" style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 7, height: 7, borderRadius: 2, background: BUCKET_COLOR[b], display: "inline-block" }} />
                {BUCKET_LABEL[b]} <span className="mono muted">{hours(idle.buckets[b])}</span>
              </span>
            ))}
          </div>
          {idle.causes.filter((c) => c.bucket === "devclaw").length > 0 && (
            <div className="muted" style={{ fontSize: 11, marginTop: 9 }}>
              Leading devclaw cause:{" "}
              <span className="mono" style={{ color: "var(--red)" }}>
                {idle.causes.filter((c) => c.bucket === "devclaw")[0].cause}
              </span>{" "}
              ({hours(idle.causes.filter((c) => c.bucket === "devclaw")[0].seconds)})
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function HealthTile({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div className="card" style={{ padding: "14px 18px", minWidth: 150, flex: "1 1 150px" }}>
      <div style={{ fontSize: 24, fontWeight: 650, letterSpacing: "-0.02em", color }}>{value}</div>
      <div className="eyebrow" style={{ marginTop: 4 }}>{label}</div>
      <div className="secondary" style={{ fontSize: 11.5, marginTop: 2 }}>{sub}</div>
    </div>
  );
}
