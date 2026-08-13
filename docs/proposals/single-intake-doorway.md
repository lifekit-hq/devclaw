# Proposal — single intake doorway: every ask, one owned pipeline, one durable receipt

- **Status:** **GRADUATED → [ADR 0012](../decisions/0012-single-intake-doorway.md)**
  — 2026-08-13, same day as the lock (Denys scheduled the tranche immediately;
  P1 shipped as #513 + lifekit-stack#117). The ADR is canonical for the doorway
  decision; this proposal remains the home of the named-unsized **P2** (doorway
  unavoidable at dispatch, `Closes`-vs-`Refs` per goal mode) and **P3**
  (push-notify the asker; scorecard-gated auto-pickup — thresholds get their
  own clarify step when scheduled). History: drafted, clarified (all 5
  `[OPEN]`s walked one-by-one with Denys, §4), and LOCKED (direction) earlier
  the same session.
- **Date opened:** 2026-08-13 · **Authors:** Denys + Claude
- **Relates to / does not restate:**
  - `CLAUDE.md` invariants — single-writer-to-state + CAS choke point, zero-token
    idle guard, OAuth-only, the 5-layer map. Referenced in §7; none amended.
  - [`issue-driven-pipelines.md`](./issue-driven-pipelines.md) §1 — the
    load-bearing split this proposal **adopts**: GitHub Issues canonical for
    *intent*, SQLite canonical for *execution state* (no CAS in the GitHub API;
    issues are written-to, never read-back for transition decisions). Its
    deferred D1 control-plane arc (webhooks, manifests, auto-pickup) **stays
    deferred** — this proposal is push-intake through an owned tool, not
    pull-pickup, so the reliability precondition on D1 is not violated.
  - [`self-issue-filing.md`](./self-issue-filing.md) — the precedent: devclaw
    already files GitHub issues mechanically (the GATHER→FILE edge). This
    generalizes *filing on behalf of an asker* from self-found problems to any
    ask from any source.
  - [`project-reference-key.md`](./project-reference-key.md) (DRAFT) — the
    registry-resolution chain (`project_id` → repo/workspace/knobs) the doorway
    is designed to ride: an intake targets a **registered project**, and the
    issue's repo derives from the registry row. Alignment is by design; the
    binding decision on dispatch-side resolution stays that proposal's.
  - Vault: `projects/devclaw/companion-direction-2026-08-05.md` — scope stays
    human; autonomy is evidence-gated on the ledger scorecard. §4-O3 encodes
    the gate shape here.

---

## 1. The incident that surfaced the class

2026-08-13: Ledger (Denys's finance OpenClaw agent) found a real issue in
finance-sentry's MCP and used A2A to ask the OpenClaw devclaw agent to get it
fixed — **exactly the intended end-state behavior**, with a broken flow: the ask
lived only inside an ephemeral A2A chat, Ledger never got a receipt, nothing
durable recorded the request, and no rule said whether the devclaw agent may act
on a non-human ask.

The class, not the instance: **intake has no contract.** Work reaches devclaw
from multiple sources (Denys via Claude-PC, Denys via the OpenClaw devclaw agent
on Telegram, domain agents like Ledger via A2A) and the devclaw MCP is already
the single *runtime* doorway — but nothing defines what an ask must look like,
what the asker gets back, or who may turn an ask into execution.

## 2. Direction — two stages, one owned pipeline

Denys's rule, stated during clarify and load-bearing for the whole design:
**everything proceeds through a pipeline we own.** The generic `gh` CLI is not a
doorway — an issue filed "in whatever way" enforces nothing. The devclaw MCP is
the only authorized way to interact with devclaw, for *intent* as well as
execution. This matches the house enforcement rule (vault, SDLC pipeline model):
put each decision where it can be ENFORCED — the intake shape is an invariant,
so it lives in python at the tool choke point, not in template/prompt discipline.

```
  any asker ──▶ mediating agent ──▶ STAGE 1: file_intake (MCP, low-privilege)
  (Denys, Ledger,  (Claude-PC or the      │  validate shape (python, synchronous reject)
   future agents)   OpenClaw devclaw      │  stamp provenance
                    agent — both already  │  file GitHub issue on target repo
                    hold MCP creds)       ▼
                                 issue URL = the durable receipt (returned to asker)
                                          │
                                          ▼
                        STAGE 2: execution admission (existing dispatch tools)
                        authorized dispatcher ONLY (today: Denys, any interface)
                        dispatch references the intake issue → PR carries Closes #N
                        → merge auto-closes the issue → URL is the live status surface
```

- **Stage 1 — intent.** A new **low-privilege MCP tool, `file_intake`**:
  validates the intake shape (§5), stamps provenance server-side, files a
  labeled GitHub issue on the target project's repo, returns the issue URL as
  the receipt. It can *only* create issues — never dispatch. That bounded blast
  radius is what makes it safe for the devclaw agent to call on a non-human ask
  without waking Denys.
- **Stage 2 — execution admission.** The existing dispatch tools
  (`create_goal` / `dispatch_task` / aliases), gated on the authorized
  dispatcher. Upgrading stage 2 (auto-pickup) never touches stage 1.
- **The two stages are two privilege tiers of the same MCP surface** — a
  cleaner statement of "single doorway" than any split across gh/MCP.

Two accepted costs, named honestly: **availability coupling** (devclaw down ⇒
no intake filing; the receipt is durable once filed, filing needs the daemon
up) and **provenance is recorded, not authenticated** (one shared MCP token —
`asker=ledger` is a claim devclaw stamps, not verifies; fine at fleet-of-one
scale, revisit if the token surface ever widens).

## 3. What each party's contract becomes

- **Ledger (and any future domain agent):** A2A ask to the devclaw agent →
  the devclaw agent calls `file_intake` → the issue URL comes back over A2A
  (optionally + a Telegram ping to Denys). Ledger records the URL and follows
  up on its own cadence. Ledger **never** calls dispatch tools, and never
  needs to.
- **The OpenClaw devclaw agent:** on a non-human ask it MAY call `file_intake`
  and MUST NOT call dispatch tools; on a Denys ask it proceeds to dispatch
  only on his explicit selection. This rule lives in its instructions in
  `lifekit-hq/lifekit-stack` (repo moved from dsdevq — update the reference
  while touching it).
- **Claude-PC (Denys's session):** files intake via `file_intake` too (§4-O5:
  everything through stage 1), then — being operated by the authorized
  dispatcher — dispatches referencing the issue in the same flow. The added
  friction is one agent-side tool call, not Denys's effort.
- **devclaw itself:** self-issue-filing keeps its own FILE edge; a devclaw
  self-found problem is already an issue-shaped intake with `asker=devclaw`.
  Convergence of that edge onto `file_intake` internals is P2 hygiene, not P1.

## 4. Resolved `[OPEN]`s (clarify step — walked one-by-one, 2026-08-13)

- **O1 — substrate: internal queue vs issues vs hybrid → RESOLVED: MCP
  `file_intake` → GitHub issue.** Denys rejected raw-gh filing ("too generic —
  the issue could be filled in whatever way; proceed everything through a
  pipeline we own"); the initial trust-surface argument for raw gh dissolved on
  inspection (askers are always mediated by an agent that already holds MCP
  creds). An internal-only pending queue was rejected as a second backlog store
  (backlog = issues) with no redeeming case. Shape enforcement moves to python
  at the choke point; issues stay canonical for intent per
  issue-driven-pipelines §1.
- **O2 — ack contract → RESOLVED: pull; the URL is the contract.** Receipt =
  issue URL (+ optional Telegram ping to Denys at file time). Dispatch
  discipline requires referencing the intake issue so the delivery PR carries
  `Closes #N`; merge auto-closes the issue, making the URL the live status
  surface (open = pending, closed-by-merged-PR = shipped). The asker owns its
  own follow-up. Push-notify-the-asker is a named later slice (P3),
  mechanism = OpenClaw-side A2A reply, not devclaw plumbing.
- **O3 — dispatcher policy + upgrade gate → RESOLVED: human-only now; gate
  shape locked, thresholds deferred.** Only Denys dispatches (any interface).
  The named upgrade — auto-pickup of `devclaw-intake`-labeled issues — touches
  stage 2 only and is gated on the **ledger scorecard measured on
  intake-dispatched work specifically** (deliveries merged without rework; no
  mechanism-wedge nights in the window). Exact thresholds are firmed in a
  dedicated clarify step when Denys schedules the upgrade — explicit
  deferral-with-owner (Denys), taken deliberately over inventing numbers ahead
  of scorecard data.
- **O4 — where the shape lives → RESOLVED: code + one devclaw doc +
  lifekit-stack.** The `file_intake` schema in devclaw code is the enforcement
  point; one `docs/reference/` page is the canonical narrative; the
  lifekit-stack devclaw-agent instructions carry the behavioral contract (§3).
  **No per-repo issue templates in P1** — the tool renders the issue body from
  one place; a hand-filed template is a second copy of the shape that will rot.
- **O5 — scope → RESOLVED: everything through stage 1.** Denys chose full
  consistency over the asker-type split: even his own in-session asks get an
  intake issue filed first (Claude-PC calls `file_intake`, then dispatches).
  Consequence: **every PR traces to an intake issue.** The doorway rule is
  absolute, not role-dependent. Enforcement of issue-ref-at-dispatch (dispatch
  tools rejecting a call without one) is a behavior change to the dispatch
  surface and is sliced as P2, not assumed in P1.

## 5. The intake shape (v1 — enforced by `file_intake`, rendered into the issue)

- `project` — the target as a **registered project** (the repo derives from
  the registry row; unknown project ⇒ synchronous reject — the
  project-reference-key admission pattern applied to intent).
- `what` — the ask, one paragraph.
- `done_when` — verifiable completion criteria (the firming vocabulary).
- `context` — evidence: where seen, repro, links.
- `provenance` — stamped server-side: asker, channel (chat / telegram / a2a),
  timestamp; plus the `devclaw-intake` label.

Field evolution is execution-side detail (speckit territory once a tranche is
scheduled); the *existence* of a python-validated shape with exactly this
intent/provenance split is the locked line.

## 6. Slices

- **P1 — the doorway exists (~2 devclaw PRs + 1 lifekit-stack PR):**
  `file_intake` on the MCP surface (validate → stamp → file → return URL, with
  named regression tests; mechanical, zero LLM) + `docs/reference/` intake-shape
  page; lifekit-stack PR updates the devclaw-agent instructions with the §3
  contract (incl. Ledger's A2A reply carrying the URL). Independently shippable:
  the incident's failure mode (undurable ask, no receipt, no may-act rule) is
  dead with P1 alone, with dispatch discipline (issue refs) as convention.
  The tool PR extends the layer-1 allowed-callee enumeration in
  `docs/architecture.md` in the same PR (docs honesty; invariant-guard note).
- **P2 — the doorway becomes unavoidable (named, unsized):** dispatch tools
  take/require an intake-issue reference; provenance threads onto task/goal rows
  and delivery surfaces (`Closes #N` mechanical, not conventional — deciding
  `Closes` vs `Refs` per goal mode here: a `long_lived` goal's first merged PR
  must not auto-close the intake and falsely mark it shipped); self-issue-
  filing's FILE edge converges on the same internals.
- **P3 — closing loops (named, unsized, separately gated):** push-notify the
  asking agent on ship (OpenClaw-side); auto-pickup of labeled intake issues —
  scheduled only through the O3 gate, with its own clarify step.

## 7. Invariants respected (references, not restatements)

- **Single writer to state + CAS choke point** — issues are intent, never
  execution state; nothing reads an issue back to make a transition decision.
- **Zero-token idle guard** — `file_intake` is mechanical (no LLM call); no
  tick-path work is added anywhere.
- **OAuth-only** — no new metered surface; issue-filing uses the same gh
  credential delivery/self-issue-filing already hold.
- **Layer map** — `file_intake` is layer-1 surface calling a mechanical filing
  helper; it never dispatches, so layer 1 still doesn't reach into layer 4.
- **"Done" is a proposal, gated on grounded evaluation** — untouched; intake
  changes how work *arrives*, not how it completes.

## 8. Out of scope

Webhook/poll discovery, per-repo `.devclaw/` manifests, and any auto-pickup
mechanics (the issue-driven-pipelines D1 arc — still deferred behind its
reliability precondition, and behind the O3 gate besides). Any change to goal
lifecycle, gates, or the heartbeat. Authenticating (vs recording) asker
identity.
