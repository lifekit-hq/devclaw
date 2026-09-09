import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  fetchProblems, tokenQueryString, PROBLEM_CATEGORIES,
  type ProblemCategory, type ProblemRow, type ProblemStage, type ProblemsResponse,
} from "../api";
import { relativeTime } from "../util/time";
import { IconExternal } from "../icons";
import { EmptyState, ErrorNote, Loading, SectionLabel, StatusDot } from "../ui";

// Problems — the problem-lifecycle tracker (ADR 0009 P2 + N2/#372). Renders the
// deduplicated problems catalog as the self-improving loop: each entry moves
// identified → filed → fixing → resolved — the full "issue → reviewed PR" story.
// HONEST (§5.5): `fixing` means a self-fix goal is running and a PR opens for
// YOUR review — it is propose-only/human-merges, never autonomous auto-fix, so
// the UI never implies autonomy that isn't there.
// Every card is click-to-expand (issue #682, increment 2), showing all stored
// fields the list row doesn't surface: fingerprint, sample_message, first_seen_ms,
// last_goal_id / last_task_id (linked to existing drill-ins), issue_state.

const STAGE_COLOR: Record<ProblemStage, string> = {
  identified: "var(--amber)",
  filed: "var(--accent)",
  fixing: "var(--violet)",
  resolved: "var(--green)",
};
const STAGE_LABEL: Record<ProblemStage, string> = {
  identified: "Identified",
  filed: "Filed",
  fixing: "Fixing",
  resolved: "Resolved",
};
const STAGES: (ProblemStage | "all")[] = ["all", "identified", "filed", "fixing", "resolved"];

// Recency windows. The catalog is bounded per fingerprint but unbounded in
// VOCABULARY and `count` is a LIFETIME counter, so an all-time read sorts
// long-dead rows above live ones. `null` = all-time; `undefined` = whatever the
// server's shared default is (DEFAULT_PROBLEM_WINDOW_DAYS) — the UI never
// hardcodes that number, it reads back the `windowDays` the server applied.
const WINDOWS: { label: string; days: number | null }[] = [
  { label: "7d", days: 7 },
  { label: "14d", days: 14 },
  { label: "30d", days: 30 },
  { label: "All time", days: null },
];

export function Problems() {
  const [data, setData] = useState<ProblemsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [stage, setStage] = useState<ProblemStage | "all">("all");
  const [expanded, setExpanded] = useState<string | null>(null); // fingerprint
  // `undefined` = let the server apply its default window; `null` = all-time.
  const [win, setWin] = useState<number | null | undefined>(undefined);
  const [category, setCategory] = useState<ProblemCategory | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchProblems({
        sinceDays: win === undefined ? undefined : (win ?? 0),
        category,
      })
        .then((r) => alive && (setData(r), setErr(null)))
        .catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 20000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [win, category]);

  const problems = data?.problems ?? [];
  const countBy = (s: ProblemStage) => problems.filter((p) => p.lifecycle === s).length;
  const shown = stage === "all" ? problems : problems.filter((p) => p.lifecycle === stage);

  return (
    <div className="page">
      <h1 style={{ fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em", margin: "0 0 18px" }}>
        Problems
      </h1>

      {err && <ErrorNote>{err}</ErrorNote>}
      {!data && !err && <Loading />}

      {data && (
        <>
          <FilterBar
            windowDays={data.windowDays}
            onWindow={setWin}
            category={category}
            onCategory={setCategory}
          />

          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 22 }}>
            <Tile label="Problems" value={problems.length} />
            <Tile label="Identified" value={countBy("identified")} color="var(--amber)" />
            <Tile label="Filed" value={countBy("filed")} color="var(--accent)" />
            <Tile label="Fixing" value={countBy("fixing")} color="var(--violet)" />
            <Tile label="Resolved" value={countBy("resolved")} color="var(--green)" />
          </div>

          {data.selfRepo === null && (
            <div className="card" style={{ padding: "10px 14px", marginBottom: 18 }}>
              <span className="secondary" style={{ fontSize: 12.5 }}>
                Self-issue-filing is off (no <span className="mono">DEVCLAW_SELF_REPO</span>) — problems are still
                catalogued and shown, but none get filed as issues.
              </span>
            </div>
          )}

          <SectionLabel
            count={shown.length}
            right={
              <div style={{ display: "flex", gap: 6 }}>
                {STAGES.map((s) => (
                  <button
                    key={s}
                    className={`btn ghost sm${stage === s ? " active" : ""}`}
                    onClick={() => setStage(s)}
                    style={stage === s ? { color: "var(--accent)" } : undefined}
                  >
                    {s}
                  </button>
                ))}
              </div>
            }
          >
            Catalog
          </SectionLabel>

          {shown.length === 0 ? (
            <div className="card">
              <EmptyState
                title="Nothing here"
                hint={
                  data.windowDays === null
                    ? "No problems match these filters."
                    : `No problems match these filters in the last ${data.windowDays} days — try a wider window.`
                }
              />
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {shown.map((p) => (
                <ProblemCard
                  key={p.fingerprint}
                  p={p}
                  selfRepo={data.selfRepo}
                  expanded={expanded === p.fingerprint}
                  onToggle={() => setExpanded((e) => e === p.fingerprint ? null : p.fingerprint)}
                />
              ))}
            </div>
          )}

          <div className="muted" style={{ fontSize: 11, marginTop: 14 }}>
            Showing {data.truncated ? <>the first <b>{data.count}</b> of </> : null}
            {data.windowDays === null
              ? "the whole catalog"
              : <>problems seen in the <b>last {data.windowDays} days</b></>}
            {category ? <> in <b>{category}</b></> : null}
            {data.truncated ? <> — more rows exist than fit one page; narrow the window or pick a category</> : null}.
            Rows outside the window are hidden, never deleted — widen it to see them. Sorted most-frequent first, and <span className="mono">×N</span>{" "}
            is a <b>lifetime</b> count, so a wide window floats long-dead rows to the top.
          </div>

          <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
            Lifecycle: <b>identified</b> (in the catalog) → <b>filed</b> (a GitHub issue is open) → <b>fixing</b>
            (a self-fix goal is running) → <b>resolved</b> (issue closed). <b>Fixing</b> opens a PR for <b>your</b>{" "}
            review — it is <b>propose-only, human-merges</b>, never autonomous auto-fix.
          </div>
        </>
      )}
    </div>
  );
}

function FilterBar({
  windowDays,
  onWindow,
  category,
  onCategory,
}: {
  windowDays: number | null;
  onWindow: (d: number | null) => void;
  category: ProblemCategory | null;
  onCategory: (c: ProblemCategory | null) => void;
}) {
  return (
    <div
      className="card"
      style={{ padding: "10px 14px", marginBottom: 18, display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}
    >
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span className="eyebrow" style={{ marginRight: 2 }}>Seen in</span>
        {WINDOWS.map((w) => {
          const active = w.days === windowDays;
          return (
            <button
              key={w.label}
              className={`btn ghost sm${active ? " active" : ""}`}
              onClick={() => onWindow(w.days)}
              style={active ? { color: "var(--accent)" } : undefined}
            >
              {w.label}
            </button>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <span className="eyebrow" style={{ marginRight: 2 }}>Category</span>
        <button
          className={`btn ghost sm${category === null ? " active" : ""}`}
          onClick={() => onCategory(null)}
          style={category === null ? { color: "var(--accent)" } : undefined}
        >
          all
        </button>
        {PROBLEM_CATEGORIES.map((c) => (
          <button
            key={c}
            className={`btn ghost sm${category === c ? " active" : ""}`}
            onClick={() => onCategory(c)}
            style={category === c ? { color: "var(--accent)" } : undefined}
          >
            {c}
          </button>
        ))}
      </div>
    </div>
  );
}


function ProblemCard({
  p,
  selfRepo,
  expanded,
  onToggle,
}: {
  p: ProblemRow;
  selfRepo: string | null;
  expanded: boolean;
  onToggle: () => void;
}) {
  const nav = useNavigate();
  const issueUrl = selfRepo && p.issue_number ? `https://github.com/${selfRepo}/issues/${p.issue_number}` : null;
  return (
    <div
      className="card"
      style={{ padding: 0, borderLeft: `2px solid ${STAGE_COLOR[p.lifecycle]}`, cursor: "pointer" }}
      onClick={onToggle}
      title="Click to expand detail"
    >
      <div style={{ padding: "12px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <StatusDot color={STAGE_COLOR[p.lifecycle]} />
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{STAGE_LABEL[p.lifecycle]}</span>
          <span className="mono muted" style={{ fontSize: 11 }}>{p.category} · {p.kind}</span>
          <span className="mono muted" style={{ marginLeft: "auto", fontSize: 11 }}>×{p.count}</span>
        </div>
        <div className="secondary" style={{ fontSize: 12.5, whiteSpace: "pre-wrap", wordBreak: "break-word", marginBottom: 8 }}>
          {p.summary}
        </div>
        {p.lifecycle === "fixing" && p.fix_goal_id && (
          <button
            className="btn ghost sm"
            onClick={(e) => { e.stopPropagation(); nav(`/goals/${p.fix_goal_id}`); }}
            style={{ color: "var(--violet)", marginBottom: 8, display: "inline-flex", alignItems: "center", gap: 5 }}
          >
            ▸ fix goal running — open PR for your review
          </button>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <Stat label="terminal" value={p.terminal_count} tone={p.terminal_count ? "var(--red)" : undefined} />
          <Stat label="recovered" value={p.recovered_count} tone={p.recovered_count ? "var(--green)" : undefined} />
          <span className="mono muted" style={{ fontSize: 11 }}>last {relativeTime(p.last_seen_ms)}</span>
          {issueUrl ? (
            <a
              href={issueUrl}
              target="_blank"
              rel="noreferrer"
              style={{ marginLeft: "auto", fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}
              onClick={(e) => e.stopPropagation()}
            >
              issue #{p.issue_number} <IconExternal size={12} />
            </a>
          ) : p.issue_number ? (
            <span className="mono muted" style={{ marginLeft: "auto", fontSize: 11 }}>issue #{p.issue_number}</span>
          ) : null}
        </div>
      </div>

      {expanded && <ProblemDetail p={p} />}
    </div>
  );
}

function ProblemDetail({ p }: { p: ProblemRow }) {
  const qs = tokenQueryString();
  return (
    <div
      style={{
        padding: "12px 16px",
        borderTop: "1px solid var(--border)",
        background: "var(--surface-raised, var(--bg-alt))",
        fontSize: 12.5,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <DField label="fingerprint" value={p.fingerprint} mono />
        <DField label="first_seen" value={relativeTime(p.first_seen_ms)} mono />
        {p.issue_state && <DField label="issue_state" value={p.issue_state} />}
      </div>

      {p.sample_message && (
        <div>
          <span className="muted" style={{ fontSize: 11 }}>sample_message</span>
          <pre
            className="mono"
            style={{
              fontSize: 11.5,
              whiteSpace: "pre-wrap",
              margin: "4px 0 0",
              maxHeight: 100,
              overflow: "auto",
            }}
          >
            {p.sample_message}
          </pre>
        </div>
      )}

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        {p.last_goal_id && (
          <span className="secondary" style={{ fontSize: 12 }}>
            Last goal:{" "}
            <Link
              to={`/goals/${p.last_goal_id}${qs}`}
              className="mono"
              style={{ fontSize: 12 }}
              onClick={(e) => e.stopPropagation()}
            >
              {p.last_goal_id.slice(0, 12)}
            </Link>
          </span>
        )}
        {p.last_task_id && (
          <span className="secondary" style={{ fontSize: 12 }}>
            Last task:{" "}
            <Link
              to={`/tasks/${p.last_task_id}${qs}`}
              className="mono"
              style={{ fontSize: 12 }}
              onClick={(e) => e.stopPropagation()}
            >
              {p.last_task_id.slice(0, 12)}
            </Link>
          </span>
        )}
      </div>
    </div>
  );
}

function DField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <span style={{ whiteSpace: "nowrap" }}>
      <span className="muted" style={{ fontSize: 11 }}>{label}: </span>
      <span className={mono ? "mono" : undefined} style={{ fontSize: 12 }}>{value}</span>
    </span>
  );
}

function Tile({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="card" style={{ padding: "14px 18px", minWidth: 120, flex: "1 1 120px" }}>
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: "-0.02em", color: color ?? "var(--text)" }}>{value}</div>
      <div className="eyebrow" style={{ marginTop: 4 }}>{label}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <span style={{ fontSize: 11.5 }}>
      <span className={tone ? "mono" : "mono secondary"} style={tone ? { color: tone } : undefined}>{value}</span>{" "}
      <span className="muted">{label}</span>
    </span>
  );
}
