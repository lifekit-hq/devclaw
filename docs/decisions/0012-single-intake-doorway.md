# ADR 0012 — the single intake doorway: file_intake before any dispatch

- **Status:** accepted 2026-08-13 (Denys — "start", scheduling the tranche the
  same day the direction locked). Graduated from
  `proposals/single-intake-doorway.md` (LOCKED 2026-08-13, all 5 `[OPEN]`s
  resolved in-session); that proposal stays the home of the named-unsized P2
  (doorway becomes unavoidable at dispatch) and P3 (push-notify the asker;
  scorecard-gated auto-pickup). Freezes the doorway decision.
- **Scope:** layer-1 surface + agent-side contract. No goal-layer, gate, queue,
  or cognition change.
- **Shipped as:** PR #512 (proposal lock) + PR #513 (`devclaw/intake.py`, the
  `file_intake` tool, `docs/reference/intake-shape.md`) +
  lifekit-hq/lifekit-stack#117 (Kit's AGENTS.md contract).

## Context

Work reaches devclaw from multiple sources — Denys via Claude-PC, Denys via the
OpenClaw agent on Telegram, and domain agents over A2A. The devclaw MCP was
already the single *runtime* doorway, but intake had no contract: the
2026-08-13 incident had Ledger's A2A ask (a real finance-sentry MCP bug) live
only in an ephemeral chat — no receipt, nothing durable, and no rule on whether
a non-human ask may be acted on.

The alternatives considered and rejected in the clarify step: **raw `gh`
filing** (rejected by Denys: a generic CLI enforces nothing — shape, provenance
and authorization would live in prompt/template discipline; and its supposed
trust-surface win is illusory, since askers are always mediated by an agent
that already holds MCP credentials) and an **internal pending-approval queue**
(rejected as a second backlog store duplicating GitHub; backlog = issues is
already the decided substrate).

## Decision

Two stages, two privilege tiers of the one MCP surface — everything proceeds
through a pipeline we own:

1. **Stage 1 — intent.** A low-privilege MCP tool, `file_intake`
   (`devclaw/intake.py`): validates the intake shape in python at the choke
   point (synchronous, all problems named at once), resolves the target as a
   **registered project** (unknown id or missing GitHub `repoUrl` ⇒ reject
   before any gh call), stamps provenance server-side (asker recorded — not
   authenticated — channel, UTC timestamp), files a `devclaw-intake`-labeled
   issue on the project's repo, and returns the issue URL as the asker's
   durable receipt. A filing failure raises loudly; there is no receipt unless
   the issue exists. The tool can only create issues — never dispatch.
2. **Stage 2 — execution admission.** The existing dispatch tools, gated on
   the authorized dispatcher (today: Denys, any interface). Dispatch
   references the intake issue so the delivery PR closes it — the URL is the
   live status surface (pull ack; the asker follows up on its own cadence).

**Everything goes through stage 1** — Denys's own asks included (his mediating
agent files, then dispatches on his go). Non-human askers are file-only: the
mediating agent may call `file_intake` without human confirmation precisely
because of its bounded blast radius, and must never call dispatch tools.

Issues stay canonical for *intent*, SQLite for *execution state* (the
issue-driven-pipelines §1 split; no CAS in the GitHub API — an issue is
written-to, never read back for a transition decision). The shape lives in
code + one reference doc + the lifekit-stack agent instructions; deliberately
no per-repo issue templates (a second copy of the shape would rot).

## Consequences

- Every agent-originated ask leaves a durable, labeled, provenance-stamped
  record with a receipt the asker can poll; the incident class is dead.
- Accepted costs: availability coupling (devclaw down ⇒ no intake filing) and
  recorded-not-authenticated provenance (one shared MCP token) — fine at
  fleet-of-one scale, revisit if the token surface widens.
- The auto-pickup upgrade touches stage 2 only; its gate is shaped (ledger
  scorecard on intake-dispatched work) with thresholds deliberately deferred
  to a dedicated clarify step owned by Denys.
- P2 must decide `Closes` vs `Refs` per goal mode — a `long_lived` goal's
  first merged PR must not auto-close the intake issue and falsely mark it
  shipped.
