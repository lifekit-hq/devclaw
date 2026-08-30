# Cognition callers & prompt files — conventions

Applies to `devclaw/prompts/*.md` + the gate's own `devclaw/quality/prompts/*.md`
and the layer-3 caller modules — every module that calls `load_prompt`:
`devclaw/goal/evaluator.py`, `devclaw/goal/summary.py`, `devclaw/goal/triage.py`,
`devclaw/intake_readiness.py`, `devclaw/quality/`.

Write module references as full repo-relative paths. `tests/test_harness_docs_map.py`
asserts every path named here resolves, so a brace-expanded or abbreviated form is
invisible to the guard and rots silently — which is exactly what happened to the
five modules the 008 shrink deleted.

## Prompt templates (`devclaw/prompts/*.md`, `devclaw/quality/prompts/*.md`)

- Templates render through `load_prompt(slug, **kwargs)` with `str.format` —
  literal braces must be escaped `{{ }}`. A new `{placeholder}` needs the
  matching kwarg at EVERY call site or rendering raises.
- **Grounding pattern (the #227 shape — copy it, don't reinvent):** repo facts
  enter as a `REPOSITORY CONTEXT` block + a prompt clause forbidding inference
  "from your own working directory, the host/Claude process context, or any
  repository you have seen before; absent ⇒ unknown". Every repo-reasoning
  prompt has one; a new cognition caller must too.
- Don't quote a section header like `## Repository context` inside a prompt's
  instruction text — it leaks the literal header into every rendering and makes
  omission tests vacuous (bit us in #234). Reference it as `Repository context`
  without the `##`.
- **Prompt style — state each rule ONCE, imperatively.** No persona/
  motivational framing ("this makes the difference between…"), no incident
  history in the template — the war story goes in the commit message that adds
  the rule, never in the prompt. A rule needed in two sections is a smell:
  restructure so it lives in one. Every template line costs tokens on the
  least-reliable call class in the system (`claude --print` OOM/timeouts scale
  with input size), so the bar for a line is "changes model behaviour", not
  "adds emphasis".

## Caller modules

- **Snapshot collectors** follow one shape: an `asyncio.to_thread` wrapper
  around `task_git._review_repo_context_sync`, defined as a module global in
  the CALLER's module (so tests patch it there), **best-effort and
  never-raises** — a git hiccup degrades to `""` (section omitted), it never
  fails the step. Collection happens at the layer-2 call site, outside any
  fail-closed `try`.
- **Zero-token idle guard is sacred**: no LLM call — and ideally no
  subprocess — on an idle or blocked tick path. New per-tick work goes AFTER
  the `should_plan` / phase gates. The tests asserting `FakeClaude.calls == 0`
  must stay green.
- Optional prompt inputs are blank-safe kwargs (`repo_context: str | None =
  None`; `if x and x.strip():` renders the section) so existing call sites and
  test stubs stay byte-unaffected.
- Cognition callers return parsed output; layer 2 persists it. Never write to
  the goal store or reach into the task queue from a caller module.
- Fixing a bad cognition outcome means **grounding the reasoning, never
  weakening a gate** — verification stays fail-CLOSED; an undocumented model
  output field is ignored, not honored (cf. the removed planner `verify_cmd`
  override, #233).

## Load-bearing inputs vs best-effort collectors (spec 019, 2026-08-25)

The never-raises degrade-to-`""` convention above applies to OPTIONAL
grounding (repo-context snapshots). It does NOT apply to load-bearing
cognition inputs — a completion contract (the scenario-default `done_when`
read live from referenced issues) or a worker brief's referenced-issue
content. Those fail LOUD AND BLOCKING (`lost_ref` / `needs_answer`): a gate
judged against an empty contract is a silently-weakened gate, and a worker
dispatched with no ask is a burned session. When adding a new cognition
input, decide which class it is and say so at the call site.
