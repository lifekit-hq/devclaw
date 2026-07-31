# v2 MVP — the stubborn loop (goal-grain, never-block, mechanical shell)

**Status: LOCKED (direction)** — locked by Denys 2026-07-31 (in-session, after the
root-cause brainstorm; execution of P1 started same session). P2+ items below are
explicitly deferred with an owner, per the clarify rule.

## The failure that forced this

2026-07-31: another failed night, and weeks of daily reliability fixes had not
converged. Same-night evidence (`list_problems`, snippets-api-dotnet goal): the
review gate repeatedly rejected worker diffs for scope-creep; 3× "gate passed
but delivery failed: branch behind remote" push races; the circuit breaker
parked the goal `needs_answer`; plus standing 105KB-prompt `claude --print`
timeouts (#422/#450) and "one-shot goal has no checklist — decomposition
failed" (#451). Three days earlier the #1 wedge (claude signal-death) was fixed
properly (#448/#449) — and the night failed anyway, on *different* classes.
That is the signature of a generator, not a bug backlog.

## Root cause (brainstormed and locked)

**v1 put intelligence in the control plane.** ~6 distinct `claude --print`
callers (planner / evaluator / decomposer / firming / review / summary) plus
per-checklist-item tasks, gates, and pushes make a night's reliability the
*product* of ~30 fragile LLM boundaries. Contributing root: the **escalation
contract** — block-and-wait at 3am *is* a failed night under the clean-run
definition ("nothing reached the owner before morning"). Vault capture:
`~/memory/system/proposals.md` → `2026-07-31-devclaw-root-cause-control-plane`.

## The decision

devclaw v2 = `mvp/loop.py`: a mechanical shell (~300 lines, stdlib, no imports
from the v1 package) around ONE cognition boundary. Decided knobs (each an
explicit answer from Denys):

- **Goal-grain:** one goal = one session-chain = one branch = one PR. No
  orchestrator decomposition.
- **Agent-owned split:** decomposition survives as `.devclaw2/PLAN.md`, written
  and maintained by the agent (session 1 plans; later sessions execute one item
  each, check it off, commit it). Steering = editing the file.
- **Strategies are prompt variants ONLY** (`--strategy plan-first|replan|direct|@file`);
  the shell is byte-identical across them. A strategy that needs shell
  machinery is rejected by design.
- **Never-block:** no "ask the human and wait" state exists. Transient claude
  deaths retry (bounded); no-progress or session-cap → abandon loudly with
  `.devclaw2/REPORT.md` (+ WIP draft PR when delivering). Morning artifact =
  PR or report, always.
- **No gate initially** (agent self-reports via `.devclaw2/DONE.md`); `--verify`
  exists but is OFF by default. *Recorded honestly: Denys chose this with
  doubt; flipping it on is a flag, not a redesign.*
- **CLI-first, no MCP, no docker** — host run, git as the only state.

v1 is archived intact: tag `v1-final`, locked branch `archive/v1` (both at
`dd83d91`). It stays on `main` and keeps running on the VPS until v2 earns the
takeover; nothing was deleted.

## Invariants carried over from v1 (the scar tissue worth keeping)

OAuth-only env stripping; transient-retry on signal-death/timeout (#448/#449);
no-progress brake; loud delivery failure — never "done without a PR" (#183);
verify (when enabled) fails CLOSED, including on its own crash.

## P1 (this slice — sized) — SHIPPED on branch `v2/mvp`

`mvp/loop.py` + `mvp/README.md` + `tests/test_v2_loop.py` (stubbed; named
regression tests for iterate-until-done, the no-progress brake, transient
retry, verify fail-closed feedback, dirty-workspace refusal, strategy prompt
presence/absence, OAuth stripping). **Exit criterion: one real PR produced by
one command against a real repo.**

## Deferred (named, unsized — owner: Denys)

- `[OPEN → deferred]` Does `--verify` become the default once the loop is
  trusted? (The grounded done-gate is the moat argument.)
- `[OPEN → deferred]` P2: nightly/scheduled runs, a multi-goal runner, VPS
  rollout (and whether v2 takes over `main`).
- `[OPEN → deferred]` P3: OpenClaw/Telegram driving (likely just the CLI
  invoked by the waiter), any return of an MCP surface, v1 deprecation
  timeline.

## The tripwire

If the v2 shell ever needs a **second LLM call** or a **third config knob** to
work reliably, the simple shape is wrong. Stop; either return to v1 with
evidence, or collapse further to a launcher over the vendor harness.
