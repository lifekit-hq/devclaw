import { useEffect, useMemo, useState } from "react";
import {
  fetchTaskEvents,
  type TaskRow,
  type WorkerEventKind,
  type WorkerEventRow,
} from "../api";
import { relativeTime } from "../util/time";
import { EmptyState, ErrorNote, Loading, StatusDot } from "../ui";

// Execution — the WORKER's turn-by-turn run of a task, decoded from the events
// the sandbox emitted (agent messages → tool actions → observations). The
// Cognition tab shows the CONTROL-PLANE claude --print calls; this shows what the
// spawned agent actually DID. The captured events were always in the DB; this is
// the reader that makes them legible (the mirror of #455 one layer down).
// Read-only: it renders rows the runner already wrote — no steer, no re-run.

const KIND_COLOR: Record<WorkerEventKind, string> = {
  message: "var(--accent, #6ea8fe)",
  action: "var(--amber, #d9a441)",
  observation: "var(--green, #4caf7d)",
  error: "var(--red, #e5534b)",
  other: "var(--text-muted, #8a8f98)",
};

const KIND_LABEL: Record<WorkerEventKind, string> = {
  message: "msg",
  action: "act",
  observation: "obs",
  error: "err",
  other: "evt",
};

function taskStatusColor(status: string): string {
  if (status === "done") return "var(--green)";
  if (status === "failed" || status === "cancelled") return "var(--red)";
  if (status === "running") return "var(--accent, #6ea8fe)";
  return "var(--text-muted)";
}

export function Execution({ tasks }: { tasks: TaskRow[] }) {
  // Most-recent task first — that's the one you usually want to inspect.
  const ordered = useMemo(
    () => [...tasks].sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0)),
    [tasks],
  );
  const [selected, setSelected] = useState<string | null>(ordered[0]?.id ?? null);

  useEffect(() => {
    // keep a valid selection as the task list changes under us
    if (ordered.length && !ordered.some((t) => t.id === selected)) {
      setSelected(ordered[0].id);
    }
  }, [ordered, selected]);

  if (tasks.length === 0)
    return (
      <EmptyState
        title="No tasks dispatched yet"
        hint="Once the goal dispatches a worker session, its turn-by-turn execution — every agent message, tool call, and observation — lands here."
      />
    );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {ordered.map((t) => {
          const active = t.id === selected;
          return (
            <button
              key={t.id}
              onClick={() => setSelected(t.id)}
              className="btn ghost sm"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 7,
                borderColor: active ? "var(--accent, #6ea8fe)" : "var(--border)",
                background: active ? "var(--surface-2, var(--surface))" : undefined,
                fontWeight: active ? 600 : 500,
              }}
              title={t.goal}
            >
              <StatusDot color={taskStatusColor(t.status)} />
              <span className="mono">{t.kind.replace("_", " ")}</span>
              <span className="mono muted" style={{ fontSize: 11 }}>
                {t.id.slice(0, 8)}
              </span>
            </button>
          );
        })}
      </div>

      {selected && <TaskTrace taskId={selected} />}
    </div>
  );
}

function TaskTrace({ taskId }: { taskId: string }) {
  const [rows, setRows] = useState<WorkerEventRow[] | null>(null);
  const [more, setMore] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setRows(null);
    setErr(null);
    setOpen(null);
    fetchTaskEvents(taskId)
      .then((r) => {
        if (!alive) return;
        setRows(r.events);
        setMore(r.nextCursor != null);
      })
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [taskId]);

  if (err) return <ErrorNote>{err}</ErrorNote>;
  if (!rows) return <Loading />;
  if (rows.length === 0)
    return (
      <EmptyState
        title="No turns recorded for this task"
        hint="The task settled without emitting worker events (a prep/dispatch failure, or it never ran)."
      />
    );

  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "9px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <span className="mono muted" style={{ fontSize: 11.5 }}>
          {rows.length} turn{rows.length === 1 ? "" : "s"}
        </span>
        {more && (
          <span className="mono muted" style={{ fontSize: 11 }}>
            (showing the first page — older turns truncated)
          </span>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column" }}>
        {rows.map((ev) => {
          const active = ev.id === open;
          return (
            <div key={ev.id} style={{ borderBottom: "1px solid var(--border)" }}>
              <div
                onClick={() => setOpen(active ? null : ev.id)}
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 10,
                  padding: "8px 16px",
                  cursor: "pointer",
                  background: active ? "var(--surface-2, var(--surface))" : undefined,
                }}
                title="Show the full turn"
              >
                <span
                  className="mono"
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    letterSpacing: 0.4,
                    color: KIND_COLOR[ev.kind],
                    minWidth: 30,
                  }}
                >
                  {KIND_LABEL[ev.kind]}
                </span>
                <span style={{ fontWeight: active ? 600 : 500, fontSize: 12.5, whiteSpace: "nowrap" }}>
                  {ev.title}
                </span>
                <span
                  className="muted"
                  style={{
                    fontSize: 12,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                  }}
                >
                  {ev.summary}
                </span>
              </div>
              {active && <TurnDetail ev={ev} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TurnDetail({ ev }: { ev: WorkerEventRow }) {
  const [showRaw, setShowRaw] = useState(false);
  return (
    <div style={{ padding: "4px 16px 16px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="mono muted" style={{ fontSize: 11 }}>
        {ev.type} · {ev.source || "—"}
        {ev.ts != null && Number.isFinite(ev.ts) ? ` · ${relativeTime(ev.ts)}` : ""}
      </div>
      <Pre text={ev.detail} />
      <button
        className="btn ghost sm"
        style={{ alignSelf: "flex-start" }}
        onClick={() => setShowRaw((v) => !v)}
      >
        {showRaw ? "Hide raw payload" : "Show raw payload"}
      </button>
      {showRaw && <Pre text={JSON.stringify(ev.raw, null, 2)} muted />}
    </div>
  );
}

function Pre({ text, muted }: { text: string; muted?: boolean }) {
  return (
    <pre
      className="mono"
      style={{
        margin: 0,
        maxHeight: 460,
        overflow: "auto",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        fontSize: 12,
        lineHeight: 1.5,
        background: "var(--surface-2, var(--surface, transparent))",
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: "12px 14px",
        color: muted ? "var(--text-muted)" : text ? "var(--text)" : "var(--text-muted)",
      }}
    >
      {text || "(empty)"}
    </pre>
  );
}
