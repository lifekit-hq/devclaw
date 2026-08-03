import { useEffect, useState } from "react";
import {
  fetchTranscript,
  fetchTranscripts,
  type TranscriptFull,
  type TranscriptRow,
} from "../api";
import { relativeTime } from "../util/time";
import { EmptyState, ErrorNote, Loading, StatusDot } from "../ui";

// Cognition — the FULL prompt + response of every `claude --print` call the goal
// made. The layer trace (LayerTrace) shows THAT a cognition hop happened and
// whether it errored; this shows exactly what it was fed and what it said back.
// Until now that ground truth was only reachable via the `devclaw trace_view`
// CLI or by catting files on the box. Read-only: it renders `.md` files the goal
// tick already wrote — no steer, no wake, no re-run.

function human(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function timeOfDay(ts: string): string {
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(ts);
  return m ? m[1] : ts.slice(0, 19) || "—";
}

function tsMs(ts: string): number | null {
  const n = Date.parse(ts);
  return Number.isNaN(n) ? null : n;
}

export function Cognition({ goalId }: { goalId: string }) {
  const [rows, setRows] = useState<TranscriptRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setRows(null);
    setErr(null);
    setSelected(null);
    fetchTranscripts(goalId)
      .then((r) => alive && setRows(r))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [goalId]);

  if (err) return <ErrorNote>{err}</ErrorNote>;
  if (!rows) return <Loading />;
  if (rows.length === 0)
    return (
      <EmptyState
        title="No cognition transcripts yet"
        hint="Each claude --print call (firming, planning, decompose, evaluate) lands its full prompt + response here once the goal starts thinking."
      />
    );

  const errored = rows.filter((r) => r.error).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
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
            {rows.length} cognition call{rows.length === 1 ? "" : "s"}
          </span>
          {errored > 0 && (
            <span className="mono" style={{ fontSize: 11, color: "var(--red)" }}>
              {errored} with error{errored === 1 ? "" : "s"}
            </span>
          )}
          <span className="mono muted" style={{ marginLeft: "auto", fontSize: 11 }}>
            largest prompt {human(Math.max(...rows.map((r) => r.promptChars)))}
          </span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="wire" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
            <thead>
              <tr>
                {["", "#", "time", "role", "model", "in", "out", "cost", ""].map((h, i) => (
                  <th
                    key={i}
                    style={{
                      textAlign: i >= 5 && i <= 7 ? "right" : "left",
                      padding: "7px 12px",
                      fontWeight: 550,
                      color: "var(--text-muted)",
                      borderBottom: "1px solid var(--border)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const active = r.filename === selected;
                return (
                  <tr
                    key={r.filename}
                    onClick={() => setSelected(active ? null : r.filename)}
                    style={{
                      cursor: "pointer",
                      background: active ? "var(--surface-2, var(--surface))" : undefined,
                      borderLeft: r.error ? "2px solid var(--red)" : "2px solid transparent",
                    }}
                    title="Show full prompt + response"
                  >
                    <td style={{ padding: "7px 12px" }}>
                      <StatusDot color={r.error ? "var(--red)" : "var(--green)"} />
                    </td>
                    <td className="mono muted" style={{ padding: "7px 12px" }}>{r.seq}</td>
                    <td className="mono muted" style={{ padding: "7px 12px", whiteSpace: "nowrap" }}>
                      {timeOfDay(r.ts)}
                    </td>
                    <td style={{ padding: "7px 12px", fontWeight: active ? 600 : 500 }}>{r.role || "?"}</td>
                    <td className="mono muted" style={{ padding: "7px 12px", whiteSpace: "nowrap" }}>
                      {(r.model || "").replace("claude-", "") || "—"}
                    </td>
                    <td className="mono" style={{ padding: "7px 12px", textAlign: "right" }}>{human(r.promptChars)}</td>
                    <td className="mono muted" style={{ padding: "7px 12px", textAlign: "right" }}>{human(r.responseChars)}</td>
                    <td className="mono muted" style={{ padding: "7px 12px", textAlign: "right", whiteSpace: "nowrap" }}>
                      {r.costUsd && r.costUsd !== "n/a" ? r.costUsd : "—"}
                    </td>
                    <td style={{ padding: "7px 12px" }}>
                      {r.error && (
                        <span className="badge" style={{ color: "var(--red)", borderColor: "var(--red)" }}>ERR</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {selected && <TranscriptDetail goalId={goalId} filename={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function TranscriptDetail({
  goalId,
  filename,
  onClose,
}: {
  goalId: string;
  filename: string;
  onClose: () => void;
}) {
  const [full, setFull] = useState<TranscriptFull | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setFull(null);
    setErr(null);
    fetchTranscript(goalId, filename)
      .then((f) => alive && setFull(f))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [goalId, filename]);

  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600 }}>{full?.role ?? "…"}</span>
        {full && (
          <span className="mono muted" style={{ fontSize: 11.5 }}>
            {(full.model || "").replace("claude-", "") || "default"}
          </span>
        )}
        {full && tsMs(full.ts) !== null && (
          <span className="mono muted" style={{ fontSize: 11 }}>{relativeTime(tsMs(full.ts)!)}</span>
        )}
        <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={onClose}>
          Close
        </button>
      </div>

      {err && <ErrorNote>{err}</ErrorNote>}
      {!full && !err && <Loading />}

      {full && (
        <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="mono muted" style={{ fontSize: 11 }}>
            tokens_in={full.tokensIn || "—"} · tokens_out={full.tokensOut || "—"} · cost=
            {full.costUsd || "—"} · prompt {human(full.promptChars)} · response {human(full.responseChars)}
          </div>
          {full.error && (
            <div
              className="mono"
              style={{
                color: "var(--red)",
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                background: "var(--red-soft)",
                padding: "8px 12px",
                borderRadius: 6,
              }}
            >
              ERROR: {full.error}
            </div>
          )}
          <Block label="Prompt" text={full.prompt} />
          <Block
            label="Response"
            text={full.response}
            emptyNote="(empty — the call produced no response)"
          />
        </div>
      )}
    </div>
  );
}

function Block({ label, text, emptyNote }: { label: string; text: string; emptyNote?: string }) {
  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 6 }}>{label}</div>
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
          color: text ? "var(--text)" : "var(--text-muted)",
        }}
      >
        {text || emptyNote || "(empty)"}
      </pre>
    </div>
  );
}
