import { useEffect, useState } from "react";
import { fetchGoalPlan, type PlanDoc } from "../api";
import { EmptyState, ErrorNote, Loading } from "../ui";

// Plan — the goal's worker-owned PLAN.md, the durable plan the demolition moved
// out of the control plane and into a repo file (cognition-demolition §3). This
// surfaces it read-only so the operator can read and EVALUATE the plan itself —
// the "plan-as-spine" view. Rendered with a small dependency-free markdown pass
// (headings + bullets + paragraphs); the raw file is the source of truth.

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
        title="No PLAN.md yet"
        hint="The worker writes a PLAN.md into the repo as it plans the goal — its destination, decisions so far, and what's next. It'll show here once the first session commits one."
      />
    );

  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 16px", borderBottom: "1px solid var(--border)" }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>PLAN.md</span>
        {doc.ref && <span className="mono muted" style={{ fontSize: 11 }}>{doc.ref}</span>}
        <span className="mono muted" style={{ marginLeft: "auto", fontSize: 11 }}>{sourceLabel(doc.source)}</span>
      </div>
      <PlanMarkdown text={doc.content} />
    </div>
  );
}

// A deliberately small markdown renderer — PLAN.md is markdown headings, bullets
// and prose. We render those three well and pass everything else through as
// text; no HTML injection (elements are built, never dangerouslySetInnerHTML).
function PlanMarkdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: JSX.Element[] = [];
  let list: string[] = [];

  const flushList = () => {
    if (list.length) {
      const items = list;
      blocks.push(
        <ul key={`ul-${blocks.length}`} style={{ margin: "2px 0 12px", paddingLeft: 22 }}>
          {items.map((li, i) => (
            <li key={i} style={{ fontSize: 13.5, lineHeight: 1.6, margin: "3px 0" }}>{li}</li>
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
      list.push(bullet[1]);
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
