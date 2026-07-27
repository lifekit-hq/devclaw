# Proposal — observation resolution: one lifecycle for trends + problems, notify-only operation

- **Status:** **DRAFT — 2026-07-27.** Written from a live investigation of the
  running instance (trends.md files read on the VPS, problems catalog + filing
  code read on `main`). The `[OPEN]` items below are the clarify step — this
  cannot lock until each has an answer or an explicit deferral-with-owner.
- **Date opened:** 2026-07-27 · **Authors:** Denys (ask) + Claude (draft)
- **The ask, verbatim in spirit:** "we have trends, we have problems, we have a
  lot of different sources of truth. I want you somehow to address those
  automatically without me. I want just to be notified about resolutions… there
  should be some resolve mechanism if it's not yet there."
- **Relates to:**
  - `proposals/issue-driven-pipelines.md` (DRAFT) — already names **GitHub
    Issues as the single canonical store of intent**; its N1 slice (collapse
    `problems`→Issues) is the same doctrine this proposal extends to trends.
  - `proposals/self-issue-filing.md` (Stage 1 FILE **live**, Stage 2 propose-only
    pickup **built**) — the pipeline trends should ride instead of growing a
    second one.
  - ADR 0009 — the console problem-lifecycle tracker
    (identified → filed → resolved) that trends would appear in for free.

---

## 1. The problem — observations without a resolve loop

devclaw currently has **four observation surfaces** and only ONE of them has a
lifecycle:

| Surface | Store | Dedup/identity | Lifecycle | Reaches Denys as |
|---|---|---|---|---|
| `problems` catalog | SQLite (fingerprinted) | ✅ | ✅ identified→filed→resolved (self-issue-filing) | console + Issues |
| **trends** | append-only `trends.md` (per-project + harness-self) | ❌ none | ❌ none | **Telegram post per fire** |
| cycle reports | SQLite + `/evals/cycles.json` | per-night | n/a (health readout) | digest |
| GitHub Issues | GitHub | n/a | ✅ (canonical intent) | backlog |

The trends half is **write-only**: every fire appends an entry with a
`Proposed action` field and posts to Telegram — and nothing ever reads the
action back. This is not hypothetical; the live files show the failure mode
*twice over*:

- **closeloop-bench** D3 entries (07-12 → 07-23 → 07-24) flagged the same
  `backend/` directory as "new" three times; by the third entry the trend text
  itself says "this has now recurred 3 times **without action**" and asks for a
  bookmark fix. (That specific mechanical bug is being fixed as a plain bug-fix
  PR alongside this draft — but the *pattern* is the point: the proposal had no
  pipeline to land in.)
- **harness-self** H4 (07-18): "the 2026-07-14 proposed action … **was never
  confirmed as done** and the count kept rising regardless."

Meanwhile the problems half already has exactly the loop Denys is asking for:
recurrence-gated auto-filing to Issues (live since 2026-07-23), a propose-only
fix pickup (built), close/age-out (built), and a console lifecycle view
(shipped). **The fix is not a new mechanism — it is routing trends into the
existing one.**

## 2. Direction — one intake, one lifecycle, Issues as the only intent store

> An *actionable observation* — whatever surface noticed it — becomes a
> fingerprinted problem; recurrence files it as a GitHub Issue; the existing
> propose-fix/close machinery resolves it; Denys is **notified of outcomes,
> not asked for triage** (except where blast radius demands it).

Concretely:

1. **Trend intake (the new seam).** When the trend retrospective emits an entry
   whose `proposed_action` is real (not "none — pattern noted"), ALSO call
   `store.record_problem(category="trend", kind=<signal id + scope>,
   message=<normalized proposed_action>, recovered=False, goal_id=…)`. The
   fingerprint dedups re-proposals of the same action (the D3 case would have
   collapsed to one row with `count=3`); `problem_cycles` recurrence then gates
   filing exactly as for mechanical problems. Zero new LLM calls — the
   retrospective already ran; this is one extra mechanism write on its output.
2. **Filing + labels.** Recurring trend-problems auto-file to the self-repo
   (existing Stage-1 path) labeled `area:trends` + the standard taxonomy.
   Trends about a *target project* (e.g. closeloop) file on that project's repo
   **only if** it is opted in via the issue-driven-pipelines manifest — until
   then they file on the self-repo with the project named in the body.
   *(Keeps this proposal from silently expanding the multi-repo DEFERRED arc.)*
3. **Resolution = Issue close, and it notifies.** The existing CLOSE/age-out
   pass is the resolve verb. Add ONE notifier hook: when a self-filed Issue
   transitions to closed (fixed by a merged propose-PR, or aged out), send the
   Telegram note ("resolved: <title> — via PR #N" / "aged out after N quiet
   cycles"). That is the "just notify me about resolutions" surface.
4. **Trend noise drops out of Telegram.** Per-fire trend posts move to the
   digest tier: no-action observations stop pinging individually (they remain
   in trends.md + console); actionable ones surface when they become *filed
   Issues* (one ping), and again on resolution. Denys's channel then carries
   state *changes*, not raw observations. `[OPEN] O2` decides the exact cut.
5. **Autonomy stays tiered — this proposal does NOT change merge authority.**
   Fix pickup remains propose-only (fix → PR → Denys merges), per the
   self-issue-filing Stage-2 lock. The "without me" ceiling rises via that
   proposal's P2.1/P2.2 classifier arc, not here. What THIS proposal removes
   from Denys's plate is *triage*: observations file, dedup, link, and close
   themselves; he only merges PRs and reads resolution pings.

## 3. What already exists vs what's new (honest inventory)

**Exists, reused untouched:** fingerprint/normalize/UPSERT (`state_store/problems.py`),
cycle-recurrence gate (`problem_cycles`, min_cycles=3), Stage-1 FILE + labels +
issue linkage, Stage-2 propose-only pickup, CLOSE/age-out, console lifecycle
tracker, the notifier plumbing to Telegram, trend signals + retrospective +
cooldowns.

**New (small):** the `record_problem` call on actionable trend output
(~one call site in `trend_detector._fire`), the `"trend"` category constant
(`[OPEN] O1`), the resolution-notification hook on the CLOSE pass, the
Telegram-noise re-tier (`[OPEN] O2`), and tests.

## 4. Slices (size only P1 on lock)

- **P1 — trend→problem intake + resolution notification.** Items 1–3 above.
  Standalone value: trends stop being write-only; the D3-class loop
  (observe → file → fix-PR → close → ping) becomes real end-to-end. Estimated
  ~2 PRs (intake + notify), each with named regression tests.
- **P2 — notification re-tier.** Item 4 (digest for no-action trends). Small,
  but separable — it changes what Denys's channel feels like, so it deserves
  its own on/off.
- **P3 — autonomy dial.** Nothing here; explicitly delegated to
  self-issue-filing P2.1/P2.2 so there is exactly one place deciding merge
  autonomy.

## 5. `[OPEN]` — the clarify step (answers required before LOCK)

- **[OPEN] O1 — category vocabulary.** Add `"trend"` to `PROBLEM_CATEGORIES`
  (recommended: distinct category keeps `list_problems(category=…)` filters and
  the console breakdown honest), or map trends onto existing categories?
- **[OPEN] O2 — the Telegram cut.** (a) per-fire posts stop entirely for
  no-action trends (digest only), (b) stop for ALL trends (first ping = the
  filed Issue), or (c) keep today's per-fire posts and only ADD resolution
  pings? Recommended: (a).
- **[OPEN] O3 — cross-repo trends.** Confirm the §2.2 rule (target-project
  trends file on the self-repo until the manifest arc revives). Alternative:
  drop cross-repo trend filing entirely for P1.
- **[OPEN] O4 — recurrence gate for trends.** Same `min_cycles=3` as mechanical
  problems (recommended — one rule, and signal cooldowns already damp volume),
  or a lower/higher gate for trend-sourced problems?
- **[OPEN] O5 — harness-self trends.** They live in the vault
  (`~/memory/projects/devclaw/trends.md`) and describe devclaw itself — same
  intake to the self-repo (recommended), or excluded?

## 6. Invariants (references, not restatements)

Zero-token idle guard: the intake is a mechanism write on an ALREADY-FIRED
retrospective — no new LLM, nothing on idle paths. Single-writer: `record_problem`
rides the existing StateStore lock; the trend detector's write boundary
(constructor-enforced) gains exactly the injected store handle it already has.
Fail-closed gates, OAuth-only, model-agnostic layer 5 — untouched. The
self-issue-filing Stage-2 merge-authority lock is explicitly NOT modified (§2.5).
