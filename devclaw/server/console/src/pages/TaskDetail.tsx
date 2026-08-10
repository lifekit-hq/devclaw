import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchTask, tokenQueryString, type TaskDetailFeed } from "../api";
import { TaskTrace } from "../components/Execution";
import { IconExternal } from "../icons";
import { KIND_LABEL, taskStatusColor } from "../status";
import { relativeTime } from "../util/time";
import { Badge, ErrorNote, Loading, StatusDot } from "../ui";

// TaskDetail — a task is a drill-in ROUTE, not a dead ID (console-legibility
// P1-A), and since the universal-drill-in change it is FIRST-CLASS: the same
// anatomy for every task no matter how it was born — goal-dispatched
// (/console/goals/:id/tasks/:taskId) or standalone dispatch_task
// (/console/tasks/:taskId). The page reads /tasks/{id}.json directly (never
// through the goal), so standalone tasks are no longer dead rows. Anatomy:
// header facts → the CONTRACT (full prompt passed to the worker) → settled
// verdicts (verify / delivery / diff / usage) → the worker's turn-by-turn
// Execution trace (TaskTrace, live while running).

const RUNNING = new Set(["pending", "running"]);

export function TaskDetail() {
  const { id, taskId } = useParams<{ id?: string; taskId: string }>();
  const qs = tokenQueryString();
  const [feed, setFeed] = useState<TaskDetailFeed | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const task = feed?.task ?? null;
  const running = task != null && RUNNING.has(task.status);

  useEffect(() => {
    if (!taskId) return;
    let alive = true;
    const load = () =>
      fetchTask(taskId)
        .then((f) => {
          if (!alive) return;
          setFeed(f);
          setErr(null);
          setLoaded(true);
        })
        .catch((e) => {
          if (!alive) return;
          setErr(String(e));
          setLoaded(true);
        });
    load();
    // Poll while the task is unsettled so status/PR/verdicts flip in place.
    const poll = setInterval(() => {
      if (feed == null || RUNNING.has(feed.task.status)) load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(poll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, feed?.task.status]);

  const goalId = id ?? task?.parentGoalId ?? null;

  return (
    <div className="page" style={{ maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, flexWrap: "wrap" }}>
        {goalId ? (
          <>
            <Link to={`/goals${qs}`} className="secondary">Goals</Link>
            <span className="muted">›</span>
            <Link to={`/goals/${goalId}${qs}`} className="secondary mono">{goalId}</Link>
          </>
        ) : (
          <span className="secondary">Task</span>
        )}
        <span className="muted">›</span>
        <span className="mono muted">{taskId?.slice(0, 8)}</span>
      </div>

      {err && <ErrorNote>{err}</ErrorNote>}
      {!loaded && !err && <Loading />}

      {task && (
        <>
          <div style={{ margin: "16px 0 18px" }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <Badge k="Kind">{KIND_LABEL[task.kind] ?? task.kind}</Badge>
              <Badge k="Status">
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <StatusDot color={taskStatusColor(task.status)} live={running} />
                  {task.status}
                </span>
              </Badge>
              {task.milestone && <Badge k="Milestone">{task.milestone}</Badge>}
              <Badge k="Created">{relativeTime(task.createdAt)}</Badge>
              {task.completedAt && <Badge k="Settled">{relativeTime(task.completedAt)}</Badge>}
              {task.prUrl && (
                <a href={task.prUrl} target="_blank" rel="noreferrer" style={{ fontSize: 12.5, display: "inline-flex", alignItems: "center", gap: 4 }}>
                  {task.prUrl.replace("https://github.com/", "")} <IconExternal size={12} />
                </a>
              )}
            </div>
          </div>

          <Contract text={task.goal} />

          {task.error && (
            <section className="card" style={{ padding: "12px 16px", marginBottom: 14 }}>
              <span className="eyebrow">Failure</span>
              <p className="mono" style={{ fontSize: 12.5, whiteSpace: "pre-wrap", margin: "8px 0 0", color: "var(--red, #c4665a)" }}>
                {task.error}
              </p>
            </section>
          )}

          <Verdicts feed={feed!} />

          <TaskTrace taskId={task.id} live={running} />
        </>
      )}
    </div>
  );
}

// The CONTRACT — the exact prompt the worker was handed. Long by design;
// collapsed past a screenful so the verdicts stay above the fold.
function Contract({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = (text ?? "").length > 700;
  return (
    <section className="card" style={{ padding: "12px 16px", marginBottom: 14 }}>
      <span className="eyebrow">Contract — what the worker was asked</span>
      <p
        style={{
          fontSize: 13.5,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
          margin: "8px 0 0",
          ...(long && !open ? { maxHeight: 170, overflow: "hidden", maskImage: "linear-gradient(#000 60%, transparent)" } : {}),
        }}
      >
        {text || "—"}
      </p>
      {long && (
        <button className="secondary" style={{ marginTop: 8, fontSize: 12.5, background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--accent, inherit)" }} onClick={() => setOpen(!open)}>
          {open ? "Collapse" : "Show full contract"}
        </button>
      )}
    </section>
  );
}

// Settled verdicts — what the gates and delivery said. Absent sections simply
// don't render (an unsettled or crashed task has no verdicts yet).
function Verdicts({ feed }: { feed: TaskDetailFeed }) {
  const { verify, delivery, diffStats, usage } = feed;
  if (!verify && !delivery && !diffStats && !usage) return null;
  return (
    <section className="card" style={{ padding: "12px 16px", marginBottom: 14 }}>
      <span className="eyebrow">Verdicts</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8, fontSize: 13 }}>
        {verify && (
          <div>
            <StatusDot color={verify.passed ? "var(--green, #7fb069)" : "var(--red, #c4665a)"} />{" "}
            <b>Verify</b> — <span className="mono" style={{ fontSize: 12 }}>{verify.cmd ?? "—"}</span>{" "}
            {verify.passed ? "passed" : `failed (exit ${verify.exit_code ?? "?"}${verify.timed_out ? ", timed out" : ""})`}
            {verify.output && !verify.passed && (
              <pre className="mono" style={{ fontSize: 11.5, whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto", margin: "6px 0 0", opacity: 0.85 }}>
                {verify.output.slice(-1500)}
              </pre>
            )}
          </div>
        )}
        {delivery && (
          <div>
            <StatusDot color={delivery.delivered ? "var(--green, #7fb069)" : "var(--red, #c4665a)"} />{" "}
            <b>Delivery</b> — {delivery.delivered ? "delivered" : "not delivered"}
            {delivery.branch && <> on <span className="mono" style={{ fontSize: 12 }}>{delivery.branch}</span></>}
            {delivery.error && <span> — {delivery.error}</span>}
          </div>
        )}
        {(diffStats || usage) && (
          <div className="secondary" style={{ fontSize: 12.5 }}>
            {diffStats && <>Diff: {diffStats.files ?? 0} files, +{diffStats.insertions ?? 0}/−{diffStats.deletions ?? 0}</>}
            {diffStats && usage && " · "}
            {usage && <>Worker: {fmtTokens(usage.output_tokens)} out / {fmtTokens(usage.cache_read_tokens)} cache read{usage.cost_usd != null ? ` ≈ $${usage.cost_usd.toFixed(2)}` : ""}</>}
          </div>
        )}
      </div>
    </section>
  );
}

function fmtTokens(n: number | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
