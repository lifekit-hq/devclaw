# Proposal — quality-gate extraction: the fail-closed review gate as a fork-and-own tool

- **Status:** **LOCKED (direction)** — 2026-07-28, same-day clarify: all 8
  `[OPEN]`s resolved by Denys (O2/O3/O6/O7 in conversation, O1/O4/O5/O8 via
  structured questions), recorded in §5. Locking commits direction only;
  tranche scheduling stays Denys's call. The extracted repo is
  **`lifekit-hq/portcullis`** (MIT).
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
    be abandoned, not locked. *(Bar passed at clarify: O6 = finance-sentry.)*
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

- **P1 (firmed at lock):** `lifekit-hq/portcullis` scaffold + the review core
  (`review_diff` / `review_panel` / `format_feedback` + prompts + the **whole
  loom substrate**, per O5) runnable standalone on an arbitrary diff via a
  **CLI** (O2), with its own test suite (stubbed caller, same style as
  `tests/test_review_gate*.py`) + **finance-sentry** actually running it
  pre-merge (O6). Sizing: **~2–3 PRs, end-of-week cap.**
- **P2 (named, unsized):** GitHub Action wrapper (wheelhouse-pattern OAuth
  secret), README-as-product, fork-and-own config surface.
- **P3 (named, unsized):** dependency inversion — devclaw consumes the
  published package, including loom (one source of truth, kills divergence and
  retires devclaw's frozen copies). Gets its own invariant-guard pass.

## 5. `[OPEN]` items — RESOLVED (clarify step, Denys, 2026-07-28)

- **O1 — Name, org, license? → `lifekit-hq/portcullis`, MIT.** The castle gate
  that drops shut when anything fails — fail-closed by construction, which is
  the product's one-line pitch.
- **O2 — CLI-first or Action-first? → CLI-first.** `pipx`-able, runs in any
  CI; the GitHub Action is a thin P2 wrapper.
- **O3 — Auth posture of the new repo? → OAuth-default, explicit API-key
  opt-in.** Default behavior matches devclaw's posture (subscription OAuth,
  never silently metered); a deliberate flag enables API-key billing for
  standalone users. The caller-injection seam stays for embedders.
- **O4 — Divergence control until P3? → Freeze the embedded copy.** Gate (and
  loom, per O5) changes land in portcullis first and are ported back to
  devclaw mechanically; portcullis is canonical from day one. The port-back
  ritual dies at P3.
- **O5 — How much of loom moves? → Loom moves ENTIRELY.** (Denys overrode the
  absorb-two-modules recommendation.) The whole substrate
  (`test_integrity`, `limits`, `trace`) becomes part of portcullis — shipped
  inside it, not separately published. devclaw's `loom/` copy stays in place
  but frozen under the O4 rule until the P3 inversion imports it back from
  portcullis and deletes the copy. P1 still leaves devclaw's tree untouched;
  "moves" means portcullis is canonical, not that devclaw's copy vanishes
  early.
- **O6 — Second consumer? → finance-sentry.** Its PRs run the portcullis CLI
  pre-merge as part of the P1 slice; wiring is part of P1's definition of
  done. The goalclaw admission bar is passed.
- **O7 — Scope boundary? → Review core only.** `browser_gate.py` /
  `reachability.py` / `eval_judge.py` / `evals.py` stay devclaw-side —
  devclaw-shaped inputs, not general. Revisit the browser gate only after
  portcullis has external users asking for it.
- **O8 — Fleet registration + blast radius? → Register at P1 close,
  human-gated.** Once P1 ships, portcullis is registered as a devclaw project
  (the loop maintains its own published tooling); it inherits the
  self-issue-filing **human-gated core** classification — the loop never
  auto-merges the gate that gates it, wherever the gate lives.

## 6. Explicitly not proposed

- No change to devclaw's embedded gate behavior in P1 — no gate weakened, no
  fail-closed semantics touched, zero tick-path impact.
- No extraction of loom, runner, delivery, or a decision surface (scored and
  declined in the brainstorm; revisit only on a real second consumer).
- No PyPI-subpackage/monorepo scheme (distribution modularity without the
  fork-and-own story).
- No self-hosted IssueOps surface (wheelhouse owns that niche; delivery-last-mile
  O3 locked the ledger into the cycle report).
