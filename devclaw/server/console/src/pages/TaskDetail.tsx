import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchGoal, tokenQueryString, type TaskRow } from "../api";
import { TaskTrace } from "../components/Execution";
import { IconExternal } from "../icons";
import { KIND_LABEL, taskStatusColor } from "../status";
import { relativeTime } from "../util/time";
import { Badge, ErrorNote, Loading, StatusDot } from "../ui";

// TaskDetail — a task is a drill-in ROUTE, not a dead ID (console-legibility
// P1-A). /console/goals/:id/tasks/:taskId shows one task's worker Execution
// trace + its PR/status, LIVE while it runs (the goal is polled so the status
// and trace update in place) and a frozen history once it settles. The trace
// render is TaskTrace, the exact component the Execution tab uses.

const RUNNING = new Set(["pending", "running"]);

export function TaskDetail() {
  const { id, taskId } = useParams<{ id: string; taskId: string }>();
  const qs = tokenQueryString();
  const [task, setTask] = useState<TaskRow | null>(null);
  const [objective, setObjective] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const running = task != null && RUNNING.has(task.status);

  useEffect(() => {
    if (!id || !taskId) return;
    let alive = true;
    const load = () =>
      fetchGoal(id)
        .then((g) => {
          if (!alive) return;
          setObjective(g.objective ?? "");
          setTask((g.tasks ?? []).find((t) => t.id === taskId) ?? null);
          setLoaded(true);
        })
        .catch((e) => {
          if (!alive) return;
          setErr(String(e));
          setLoaded(true);
        });
    load();
    // Poll the goal while the task is unsettled so status/PR flip in place.
    const poll = setInterval(() => {
      if (task == null || RUNNING.has(task.status)) load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(poll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, taskId, task?.status]);

  return (
    <div className="page" style={{ maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, flexWrap: "wrap" }}>
        <Link to={`/goals${qs}`} className="secondary">Goals</Link>
        <span className="muted">›</span>
        <Link to={`/goals/${id}${qs}`} className="secondary mono">{id}</Link>
        <span className="muted">›</span>
        <span className="mono muted">{taskId?.slice(0, 8)}</span>
      </div>

      {err && <ErrorNote>{err}</ErrorNote>}
      {!loaded && !err && <Loading />}

      {loaded && !task && !err && (
        <ErrorNote>No task <span className="mono">{taskId}</span> on this goal — it may have been pruned.</ErrorNote>
      )}

      {task && (
        <>
          <div style={{ margin: "16px 0 18px" }}>
            <p style={{ fontSize: 15.5, lineHeight: 1.55, margin: "0 0 14px", maxWidth: 820 }}>
              {task.goal || objective || "—"}
            </p>
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

          <TaskTrace taskId={task.id} live={running} />
        </>
      )}
    </div>
  );
}
