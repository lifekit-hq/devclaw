import { useEffect, useState } from "react";
import { fetchGoalPlan, type PlanDoc } from "../api";
import { EmptyState, ErrorNote, Loading } from "../ui";

// Plan — the goal's worker-owned execution contract: the ACTIVE speckit
// feature's specs/NNN-*/tasks.md. Plan-state lives in repo files the worker
// maintains, not in the control plane. This surfaces it read-only so the
// operator can read and EVALUATE the plan itself — the "plan-as-spine" view.
//
// This used to render a repo-root PLAN.md. Nothing has written that file since
// the spec 008 speckit shrink, so this tab showed "No PLAN.md yet" for every
// goal until #614 repointed it at the contract the worker actually keeps.
//
// Rendered with a small dependency-free markdown pass (headings + bullets +
// checkboxes + paragraphs); the raw file is the source of truth.

// The feature the plan belongs to — "012-saga-prompt-contract" out of
// "specs/012-saga-prompt-contract/tasks.md". A goal can carry several features
// over its life, so naming the one on screen matters.
function featureLabel(path: PlanDoc["path"]): string {
  if (!path) return "Plan";
  const parts = path.split("/");
  return parts.length >= 2 ? parts[parts.length - 2] : "Plan";
}

function sourceLabel(source: PlanDoc["source"]): string {
  if (source === "branch") return "live delivery branch";
  if (source === "head") return "checked-out branch";
  if (source === "worktree") return "workspace file";
  return "";
}

export function Plan({ goalId }: { goalId: string }) {
  const [doc, setDoc] = useState<PlanDoc | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setDoc(null);
    setErr(null);
    fetchGoalPlan(goalId)
      .then((d) => alive && setDoc(d))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [goalId]);

  if (err) return <ErrorNote>{err}</ErrorNote>;
  if (!doc) return <Loading />;
  if (!doc.content)
    return (
      <EmptyState
        title="No plan yet"
        hint="The worker runs speckit in the sandbox and commits specs/NNN-*/tasks.md as it plans the goal — the story slices and the tasks under each. It'll show here once the first session commits one."
      />
    );

  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 16px", borderBottom: "1px solid var(--border)" }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>{featureLabel(doc.path)}</span>
        {doc.path && <span className="mono muted" style={{ fontSize: 11 }}>{doc.path}</span>}
        {doc.ref && <span className="mono muted" style={{ fontSize: 11 }}>{doc.ref}</span>}
        <span className="mono muted" style={{ marginLeft: "auto", fontSize: 11 }}>{sourceLabel(doc.source)}</span>
      </div>
      <PlanMarkdown text={doc.content} />
    </div>
  );
}

// A deliberately small markdown renderer — tasks.md is markdown headings, bullets
// and prose. We render those three well and pass everything else through as
// text; no HTML injection (elements are built, never dangerouslySetInnerHTML).
function PlanMarkdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: JSX.Element[] = [];
  // done === null for a plain bullet; true/false for a task checkbox.
  let list: { done: boolean | null; text: string }[] = [];

  const flushList = () => {
    if (list.length) {
      const items = list;
      const tasks = items.some((i) => i.done !== null);
      blocks.push(
        <ul
          key={`ul-${blocks.length}`}
          style={{
            margin: "2px 0 12px",
            paddingLeft: tasks ? 4 : 22,
            listStyle: tasks ? "none" : undefined,
          }}
        >
          {items.map((li, i) => (
            <li
              key={i}
              style={{
                fontSize: 13.5,
                lineHeight: 1.6,
                margin: "3px 0",
                display: li.done === null ? undefined : "flex",
                gap: 8,
                alignItems: "baseline",
                opacity: li.done ? 0.55 : 1,
              }}
            >
              {li.done !== null && (
                <span aria-hidden="true" style={{ fontSize: 12.5, flex: "none" }}>
                  {li.done ? "☑" : "☐"}
                </span>
              )}
              <span style={{ textDecoration: li.done ? "line-through" : undefined }}>
                {li.done !== null && (
                  <span className="sr-only">{li.done ? "done: " : "not done: "}</span>
                )}
                {li.text}
              </span>
            </li>
          ))}
        </ul>,
      );
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (heading) {
      flushList();
      const level = heading[1].length;
      const size = level === 1 ? 16 : level === 2 ? 14.5 : 13;
      blocks.push(
        <div key={`h-${blocks.length}`} style={{ fontSize: size, fontWeight: 650, margin: `${level <= 2 ? 18 : 12}px 0 6px` }}>
          {heading[2]}
        </div>,
      );
    } else if (bullet) {
      // speckit task rows are `- [ ] T001 [P] [US1] do the thing`; render the
      // state as a real checkbox so an operator can see progress at a glance
      // instead of reading literal brackets.
      const task = /^\[([ xX])\]\s+(.*)$/.exec(bullet[1]);
      if (task) list.push({ done: task[1] !== " ", text: task[2] });
      else list.push({ done: null, text: bullet[1] });
    } else if (line.trim() === "") {
      flushList();
    } else {
      flushList();
      blocks.push(
        <p key={`p-${blocks.length}`} style={{ fontSize: 13.5, lineHeight: 1.62, margin: "0 0 10px" }}>{line}</p>,
      );
    }
  }
  flushList();

  return <div style={{ padding: "16px 20px", maxWidth: 760 }}>{blocks}</div>;
}

