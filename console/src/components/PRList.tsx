import { useEffect, useState } from "react";
import { fetchGoalPrs, mergePr, type PRRow } from "../api";
import { prMeta } from "../status";
import { relativeTime } from "../util/time";
import { IconMerge } from "../icons";
import { EmptyState, Modal, StatusDot } from "../ui";

// Per-PR review rows for a goal. Reads /goals/{id}/prs.json (live `gh pr view`
// enriched on the backend). Merge fires POST /prs/merge.
// Every row is click-to-expand (issue #682, increment 2), showing the hidden
// fields ts, gatePassed, mergeStateStatus, mergedAt, and error that prs.json
// already returns but the compact row doesn't surface.

export function PRList({ goalId }: { goalId: string }) {
  const [rows, setRows] = useState<PRRow[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<PRRow | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null); // prUrl

  const load = () => fetchGoalPrs(goalId).then(setRows).catch(() => setRows([]));

  useEffect(() => {
    let alive = true;
    setRows(null);
    fetchGoalPrs(goalId).then((r) => alive && setRows(r)).catch(() => alive && setRows([]));
    return () => {
      alive = false;
    };
  }, [goalId]);

  const doMerge = async (row: PRRow) => {
    setConfirm(null);
    setBusy(row.prUrl);
    setFlash(null);
    try {
      const r = await mergePr(row.prUrl);
      setFlash(r.merged ? `#${row.prNumber} merged` : `merge failed: ${r.error ?? "unknown"}`);
      load();
    } catch (e) {
      setFlash(String(e));
    } finally {
      setBusy(null);
    }
  };

  if (rows === null) return <EmptyState title="Loading pull requests…" />;
  if (rows.length === 0) return <EmptyState title="No pull requests yet" hint="Delivered PRs will appear here." />;

  return (
    <>
      {flash && (
        <div className="mono secondary" style={{ fontSize: 12, marginBottom: 10 }}>{flash}</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((row) => {
          const m = prMeta(row);
          const isBusy = busy === row.prUrl;
          const isOpen = expanded === row.prUrl;
          return (
            <div
              key={row.prUrl}
              className="card"
              style={{ padding: 0, cursor: "pointer" }}
              onClick={() => setExpanded((e) => e === row.prUrl ? null : row.prUrl)}
              title="Click to expand detail"
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 14px" }}>
                <span className="mono muted" style={{ fontSize: 12, flexShrink: 0 }}>#{row.prNumber}</span>
                <a
                  href={row.prUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="truncate"
                  style={{ flex: 1, fontSize: 13 }}
                  title={row.repo}
                  onClick={(e) => e.stopPropagation()}
                >
                  {row.title || row.actionLabel || row.prUrl}
                </a>
                <span className="badge" style={{ color: m.color, flexShrink: 0 }}>
                  <StatusDot color={m.color} />
                  {m.label}
                </span>
                <button
                  className="btn good sm"
                  disabled={!m.canMerge || busy !== null}
                  onClick={(e) => { e.stopPropagation(); setConfirm(row); }}
                  style={{ flexShrink: 0 }}
                >
                  <IconMerge size={14} />
                  {isBusy ? "…" : "Merge"}
                </button>
              </div>

              {isOpen && <PRDetail row={row} />}
            </div>
          );
        })}
      </div>

      {confirm && (
        <Modal
          title={`Merge ${confirm.repo}#${confirm.prNumber}?`}
          onClose={() => setConfirm(null)}
          footer={
            <>
              <button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
              <button className="btn good" onClick={() => doMerge(confirm)}>Merge & delete branch</button>
            </>
          }
        >
          <div style={{ fontSize: 13.5, lineHeight: 1.55 }}>{confirm.title}</div>
          <div className="mono muted" style={{ fontSize: 12, marginTop: 10 }}>
            Squash-merges into the default branch and deletes the source branch.
          </div>
        </Modal>
      )}
    </>
  );
}

function PRDetail({ row }: { row: PRRow }) {
  return (
    <div
      style={{
        padding: "10px 14px",
        borderTop: "1px solid var(--border)",
        background: "var(--surface-raised, var(--bg-alt))",
        fontSize: 12.5,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {row.ts && <DField label="delivered" value={fmtTs(row.ts)} mono />}
        {row.gatePassed !== null && row.gatePassed !== undefined && (
          <DField
            label="gate"
            value={row.gatePassed ? "passed" : "failed"}
            valueStyle={{ color: row.gatePassed ? "var(--green)" : "var(--red)" }}
          />
        )}
        {row.mergeStateStatus && <DField label="merge_status" value={row.mergeStateStatus} mono />}
        {row.mergedAt && <DField label="merged_at" value={fmtTs(row.mergedAt)} mono />}
        <DField label="repo" value={row.repo} mono />
      </div>
      {row.error && (
        <div>
          <span className="muted" style={{ fontSize: 11 }}>error: </span>
          <span className="mono" style={{ fontSize: 11.5, color: "var(--red)" }}>{row.error}</span>
        </div>
      )}
    </div>
  );
}

function fmtTs(s: string | null): string {
  if (!s) return "—";
  const ms = new Date(s).getTime();
  return Number.isFinite(ms) ? relativeTime(ms) : s;
}

function DField({
  label,
  value,
  mono,
  valueStyle,
}: {
  label: string;
  value: string;
  mono?: boolean;
  valueStyle?: React.CSSProperties;
}) {
  return (
    <span style={{ whiteSpace: "nowrap" }}>
      <span className="muted" style={{ fontSize: 11 }}>{label}: </span>
      <span className={mono ? "mono" : undefined} style={{ fontSize: 12, ...valueStyle }}>
        {value}
      </span>
    </span>
  );
}
