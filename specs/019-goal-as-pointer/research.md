# Research: Goal-as-Pointer

Decisions grounded in the 2026-08-25 session evidence (the #684
stale-contract night; 45% per-goal first-pass, spec 018) and the current
code. Clarifications A/A/A recorded in spec.md.

## D1 — Reference shape

- **Decision**: `issue_refs: list[int]` — ordered issue NUMBERS, resolved
  against the goal's project repository (`Project.repo_url` /
  `Goal.repo_url`). Not URLs, not owner/repo#n strings.
- **Rationale**: FR-008's same-project rule makes the repo derivable, so
  storing it would be a second copy that can drift; numbers keep goal.yaml
  human-scannable and the doorway message short.
- **Alternatives**: full URLs (rejected: invite cross-repo refs the spec
  forbids); owner/repo#n strings (rejected: same, plus parsing).

## D2 — One reference seam, one new module

- **Decision**: `devclaw/goal/issue_ref.py` owns parse/validate, the
  injectable fetcher (`gh api repos/{owner}/{repo}/issues/{n}` → title,
  body, state, labels), the readiness read (the earned ready label from
  specs 006/009 — reuse the label constant where the grading pipeline
  defines it, never a second literal), and acceptance-scenario extraction.
  Callers: service (creation), tick_dispatch (brief), done-gate call site
  (contract). Injection mirrors `self_issue.py`'s gh-protocol and
  `remote_checks.default_checker()` binding so the stubbed suite runs a
  `FakeIssueFetcher`.
- **Rationale**: three consumers, one seam — otherwise freshness semantics
  fork per call site.
- **Alternatives**: extend self_issue.py (rejected: that module is the
  self-FILING pipeline; reading refs is a different concern) — grep-level
  reuse of its gh runner is fine.

## D3 — Budget home and default

- **Decision**: `DEVCLAW_GOAL_TEXT_BUDGET` in `devclaw/config.py`, default
  **1000 characters**, applied to the objective + free-text note of
  referenced goals only (done_when when explicitly provided is NOT counted —
  it is a contract, not context).
- **Rationale**: ~a short paragraph of ordering/scope glue (the audited
  essay was ~2,700 chars of objective alone); config doorway rule.
- **Alternatives**: word count (rejected: chars are what `str.len` enforces
  unambiguously); counting done_when (rejected: punishes the legitimate
  explicit-contract override).

## D4 — Scenario extraction convention

- **Decision**: The recognizable format is spec 015's convention — an
  acceptance section in the graded issue (the grading pipeline already
  requires scenarios for readiness; `devclaw/prompts/scope-grill.md` names
  the `## Acceptance` shape). Extraction is mechanical (section slice), not
  cognition. Absence at creation refuses the scenario-default (US2 sc.4);
  absence at evaluation time (edited away mid-goal) blocks the gate round
  legibly.
- **Rationale**: zero-token; the grading pipeline is the guarantor of the
  format, the extractor only slices it.
- **Alternatives**: LLM extraction (rejected: cognition for a mechanical
  slice; violates the enforcement doctrine).

## D5 — Load-bearing contract fetch (deviation from collector convention)

- **Decision**: The done-gate's scenario fetch and the dispatch fetch FAIL
  LOUD AND BLOCK on error — they do not follow the best-effort
  degrade-to-`""` convention of the repo-context collectors in
  `.claude/rules/cognition-prompts.md`. That rule doc gains the distinction
  (optional grounding degrades; load-bearing contract input blocks) in the
  same PR.
- **Rationale**: evaluating a done-gate against an empty contract is a
  silently-weakened gate — the exact class Principle V forbids; dispatching
  a worker with no ask is a wasted session.
- **Alternatives**: best-effort with a warning (rejected: constitution V/VI).

## D6 — Exclusivity check

- **Decision**: At creation, scan live goals (phase not in done/cancelled)
  for overlapping `issue_refs` within the same project; refuse on overlap.
  A plain read inside the existing create path — no new lock: creation is
  human-paced and already serialized through the service.
- **Rationale**: mirrors 007's single-claim semantics one layer earlier
  (clarified Q3=A); the race window between two simultaneous creations is
  operator-vs-operator and acceptably rare — the CAS'd goal-store transition
  remains the backstop.

## D7 — Closed/not-ready item at dispatch

- **Decision**: Skip with a loud goal-log entry and advance (closed ⇒ the
  work is no longer needed; ready-label revoked ⇒ the owner pulled it);
  when no referenced items remain, the normal done-proposal path takes over.
  Unfetchable (404/permission/network) is different: block with the existing
  human-gated lost-ref semantics.
- **Rationale**: closed-out-of-band is the freshness guard SUCCEEDING (the
  #684 class caught); unfetchable is unknown state — never guess.

## D8 — Lane recording

- **Decision**: No separate flag — the lane IS the presence of
  `issue_refs` (non-empty = referenced lane; empty = issue-less lane,
  today's behavior). The creation surface documents the choice; get_goal
  displays it.
- **Rationale**: a boolean that must always equal `bool(issue_refs)` is a
  drift invitation; US5's "explicit choice, visible on the record" is
  satisfied by the refs field itself being the record.
