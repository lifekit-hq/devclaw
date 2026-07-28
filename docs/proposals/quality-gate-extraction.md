# Proposal — quality-gate extraction: the fail-closed review gate as a fork-and-own tool

- **Status:** **DRAFT** — clarify step pending; every `[OPEN]` below needs an answer
  or an explicit deferral-with-owner before this can flip to LOCKED.
- **Invariant impact:** none inside devclaw for the P1 slice (devclaw's embedded
  gate is untouched; the extraction is a new artifact). The OAuth-only question
  applies to the **new** repo and is decided at `[OPEN] O3`, not silently
  inherited. The P3 dependency inversion (devclaw consumes the published
  package) would touch the layer-4 settle path and gets its own invariant review
  when sliced.
- **Date opened:** 2026-07-28 · **Authors:** Denys + Claude
- **Grounded on:** `devclaw/quality/README.md` read at `main` @ `9a24fe7` — the
  package already documents its own extraction ("When this package moves to its
  own repo, those three seams are the entire integration surface");
  `tests/test_quality_package.py` pins the import-boundary. Vault:
  `~/memory/system/proposals.md` § `2026-07-28-devclaw-modular-extraction`
  (the brainstorm that produced this) and
  `~/memory/projects/devclaw/wheelhouse-teardown-2026-07-28.md` (the
  kunchenguid fleet pattern that triggered it).
- **Relates to:**
  - **The goalclaw precedent (vault, 2026-06-20)** — the last extraction was
    folded back because at N=1 the cross-service seam cost more than it paid.
    This proposal's admission ticket is the **second-consumer test**: the gate
    is the only devclaw piece with a live second consumer today (Denys's other
    repos' PRs). If `[OPEN] O6` can't name one concretely, this proposal should
    be abandoned, not locked.
  - [`self-issue-filing.md`](./self-issue-filing.md) Stage-2 lock — `quality/`
    + gate prompts sit in the **human-gated core** of the self-merge
    blast-radius tiers. Extraction moves the files, not the classification
    (see `[OPEN] O8`).
  - [ADR 0007](../decisions/0007-gate-strictness-dial.md) — the `trust|strict`
    dial is devclaw *policy* layered on gate *verdicts*. The extraction takes
    the verdict machinery; `gate_policy.py` (the dial glue) stays devclaw-side.
  - [`issue-driven-pipelines.md`](./issue-driven-pipelines.md) (DEFERRED arc) —
    a standalone gate a repo can adopt without devclaw is a stepping stone that
    thread would inherit, not a competitor to it.

---

## 1. Why (the direction, in one paragraph)

Denys wants kunchenguid-style modularity — small, fork-and-own tools that
compose across repos via GitHub primitives — without giving up devclaw's
robustness. The 2026-07-28 brainstorm scored the options; the only extraction
that passes the second-consumer test *and* produces a CV-legible public
artifact is `devclaw/quality/`: a **fail-closed adversarial review gate for
agent-written code** ("no verdict ⇒ no approval; a crash is a failed run,
never an approval"). Everything else (loom, runner, delivery, an IssueOps
surface) either has no second consumer (the goalclaw trap) or is already owned
by someone else's tool (wheelhouse). The companion move — registering the
extracted repo as a devclaw project so the loop maintains its own published
tooling — is the fleet demo: *my agentic loop maintains my published tools*.

## 2. Ground truth — the extraction is already prepared

- `devclaw/quality/` is ~1,530 lines + 3 prompt templates, with its own README
  and its own `prompts/` loader (renders verdicts without devclaw's prompt
  dir).
- Its entire inward surface is three deliberate leaf seams, pinned by
  `tests/test_quality_package.py`: the **LLM caller** (any
  `Callable[[str], Awaitable[str]]` — already injected in tests), the
  **model-tier table** (two lines), and **`devclaw/loom`** (pure stdlib:
  `test_integrity`, `limits`). Nothing imports the planner, queue, goal layer,
  or state store.
- One consumer call site inside devclaw (`task_queue.py` settle path), so the
  eventual P3 inversion is a bounded change.
- Wheelhouse (kunchenguid) proves the distribution pattern end-to-end: a
  Claude-powered check running in public GitHub Actions off a
  `CLAUDE_CODE_OAUTH_TOKEN` secret, fork-and-own with one config file. The
  gate can ship the same way.

## 3. Direction (to be firmed at clarify)

A new public repo containing the gate as a standalone tool:

1. **Input:** a diff (or a PR ref) + optional verify/test output + a repo
   snapshot for grounding. **Output:** a verdict — approved, or blocking
   findings — with exit code semantics; fail-closed on any crash/non-verdict.
2. **The devclaw design rules travel with it** (they ARE the product): fail
   closed always; fail fast when retry is futile; grounded-never-remembered
   (the `REPOSITORY CONTEXT` pattern); evidence wins (panel union);
   read-the-change-never-trust-the-changer.
3. **devclaw is untouched in P1.** The embedded gate keeps running; the new
   repo starts as the extraction target, and the dependency inversion is a
   later slice with its own review.
4. **The fleet move:** the new repo is registered as a devclaw project so
   issues/maintenance flow through the loop that birthed it.

## 4. Slices (per the sizing rule — firm P1 only at lock)

- **P1 (proposed shape, firmed at lock):** repo scaffold + the review core
  (`review_diff` / `review_panel` / `format_feedback` + prompts + the needed
  loom modules per O5) runnable standalone on an arbitrary diff via the O2
  interface, with its own test suite (stubbed caller, same style as
  `tests/test_review_gate*.py`) + ONE non-devclaw consumer actually running it
  (per O6). Sizing: **~2–3 PRs, end-of-week cap.**
- **P2 (named, unsized):** GitHub Action wrapper (wheelhouse-pattern OAuth
  secret), README-as-product, fork-and-own config surface.
- **P3 (named, unsized):** dependency inversion — devclaw consumes the
  published package (one source of truth, kills divergence) — plus fleet
  registration if O7 defers it out of P1. Gets its own invariant-guard pass.

## 5. `[OPEN]` items (the clarify step — all must resolve before LOCK)

- **`[OPEN] O1` — Name, org, license.** Repo name (candidate wanted — the
  wheelhouse teardown suggests naming matters for fork-and-own adoption), under
  `lifekit-hq` or personal, license (MIT?).
- **`[OPEN] O2` — P1 interface: CLI-first or Action-first?** Recommended:
  CLI-first (`pipx`-able, runs in any CI, no Actions coupling; the Action is a
  thin P2 wrapper). Action-first optimizes adoption but front-loads the OAuth
  question.
- **`[OPEN] O3` — Auth posture of the NEW repo.** devclaw strips
  `ANTHROPIC_API_KEY` everywhere (autonomous runs must never silently go
  metered). A standalone tool's users may *want* API-key billing. Options:
  inherit OAuth-only; allow both with an explicit flag; caller-injected only
  (the seam already allows it). Recommended: OAuth-default with explicit
  opt-in for API key — but this is a real posture decision, not a default.
- **`[OPEN] O4` — Divergence control between embedded and extracted gate until
  P3 lands.** Options: freeze the embedded copy (changes go to the new repo
  first, ported back); or accept short-lived drift with a named re-sync owner.
  The goalclaw lesson lives here — pick one explicitly.
- **`[OPEN] O5` — How much of loom moves?** Recommended: absorb only
  `test_integrity` + `limits` into the new repo (pure stdlib, small); loom
  itself stays devclaw's substrate and is NOT separately published (N=1).
- **`[OPEN] O6` — Name the second consumer.** finance-sentry or lifekit-stack,
  which pipeline, and who wires it in P1. If neither can be named, abandon
  (see the goalclaw line in the header).
- **`[OPEN] O7` — Scope boundary of the extraction.** Recommended: review core
  only; `browser_gate.py` / `reachability.py` (Playwright-verify-shaped
  inputs), `eval_judge.py` / `evals.py` (devclaw's eval loop) stay behind —
  they're devclaw-shaped, not general. Counter-argument: the browser gate is
  the most *distinctive* piece.
- **`[OPEN] O8` — Fleet registration timing + blast-radius classification.**
  Register the new repo as a devclaw project in P1 or after P2? And does the
  extracted gate inherit the self-issue-filing "human-gated core"
  classification (recommended: yes — a gate that gates the loop must not be
  auto-merged by the loop, wherever it lives)?

## 6. Explicitly not proposed

- No change to devclaw's embedded gate behavior in P1 — no gate weakened, no
  fail-closed semantics touched, zero tick-path impact.
- No extraction of loom, runner, delivery, or a decision surface (scored and
  declined in the brainstorm; revisit only on a real second consumer).
- No PyPI-subpackage/monorepo scheme (distribution modularity without the
  fork-and-own story).
- No self-hosted IssueOps surface (wheelhouse owns that niche; delivery-last-mile
  O3 locked the ledger into the cycle report).
