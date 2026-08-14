# Implementation Plan: Review-Gate Repositioning

**Branch**: `001-review-gate-repositioning` | **Date**: 2026-08-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-review-gate-repositioning/spec.md`

## Summary

Under the default `trust` strictness, the per-increment adversarial diff review is
**removed from the task gate pipeline** — it is the #1 mechanism-wedge source on
the live instance, and under companion mode (human reviews every PR) it is
redundant with the human review, its unique catches re-caught by the goal-level
done-gate. Under `strict`, it runs exactly as today. Technically this is a
**gate-composition** change at the single orchestration site that builds the gate
tuple (`devclaw/task_queue.py`), not a policy or gate-internal change — strictness
already governs there. The constitution's Principle V and the matching CLAUDE.md
hardening bullet are amended in the same PR to record that under `trust` the
review gate is not part of the chain (fail-closed-on-crash governs *consulted*
gates only).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: internal — `devclaw.quality.gate_pipeline`
(`run_pipeline`, `GateInput`, `Gate`), `devclaw.quality.gate_policy`
(`gate_consequence`, `ALWAYS_HARD`, `DIAL_ABLE`), `devclaw.task_queue` (the gate
orchestrator). No new third-party dependency.

**Storage**: none new. Strictness is already snapshotted on the task row
(`row.strictness`, `task_queue.py:1562`) and read at gate time.

**Testing**: pytest, fully stubbed (`FakeClaude`/`FakeEngine`/`Fake>`); the
review path is exercised via `tests/test_review_gate*.py`, `test_task_retry.py`,
and the gate-policy unit tests. New named regression tests required (FR-007).

**Target Platform**: the devclaw host process (layer 4, TaskQueue orchestrator).

**Project Type**: single project — the devclaw agentic-loop backend.

**Performance Goals**: removes ≥1 large-input `claude --print` call per delivered
increment under trust (the review call) — directly reduces quota burn and
host-memory contention in the nightly window.

**Constraints**: must preserve the zero-token idle guard, single-writer-to-state,
and all always-hard gate semantics. Strict-mode behavior must be byte-identical
to today.

**Scale/Scope**: one conditional at one call site + a constitution/CLAUDE.md
amendment + regression tests. P1 is one PR.

## Constitution Check

*GATE: evaluated against `.specify/memory/constitution.md` v2.0.0.*

| Principle | Impact | Verdict |
|---|---|---|
| I. OAuth only | No cognition-billing path touched; if anything, fewer `claude` calls. | ✅ pass |
| II. Model-agnostic worker | No worker/skill/hook change; orchestrator-only. | ✅ pass |
| III. Zero-token idle | **Improved** — one fewer LLM call per trust increment; idle/blocked paths unchanged. The `FakeClaude.calls == 0` guard tests must stay green. | ✅ pass |
| IV. Single writer to state | Only gate *composition* changes; no new writer, no row mutation moved. | ✅ pass |
| V. Verification fails closed; "done" is a proposal | **REQUIRES AMENDMENT.** Today: "the trust dial recalibrates the two review-shaped gates; it never repeals fail-closed for crash/quota cases." This spec makes the per-increment review *not part of the chain* under trust. This does not repeal #186 (nothing ships on a *consulted* gate's silence; an unconsulted gate produces no silence). The verify, test-integrity, and done gates stay always-hard in both modes; the goal-level done-gate keeps its `review_repository` LLM call. Principle V + the CLAUDE.md hardening bullet are amended in the SAME PR. | ⚠️ amend-in-arc (declared) |
| VI. Loud failure over silent degradation | Every trust delivery discloses on the PR that no per-increment agent review ran (FR-004) — the removal is loud, not silent. | ✅ pass |
| VII. Fix the class, not the instance | The change is at the gate-composition layer (a rule), not a per-repo/per-goal patch. | ✅ pass |

**Gate result**: PASS with one *declared* amendment (Principle V), executed in the
same PR per Governance. No unjustified violation.

## Project Structure

### Documentation (this feature)

```text
specs/001-review-gate-repositioning/
├── spec.md              # done (clarified 2026-08-14)
├── plan.md              # this file
├── research.md          # Phase 0 — the seam decision + rejected seams
├── quickstart.md        # Phase 1 — how to validate trust-skip and strict-parity
└── tasks.md             # /speckit-tasks output (next command)
```
(No `data-model.md` / `contracts/` — this feature introduces no data entities and
no external interface; it changes internal gate composition only.)

### Source Code (repository root)

```text
devclaw/
├── task_queue.py        # THE change site: the gate tuple built at ~L1748 inside
│                        #   the settle/gate path — omit _ReviewGate(self) when
│                        #   strictness == "trust". strictness is already in scope
│                        #   (row.strictness, ~L1562).
├── quality/
│   ├── gate_policy.py    # ALWAYS_HARD / DIAL_ABLE — unchanged; "review" stays a
│   │                     #   dial-able id for the strict path. (Read-only reference.)
│   └── gate_pipeline.py  # run_pipeline / Gate protocol — unchanged.
└── delivery/__init__.py  # FR-004: PR-surface disclosure that no per-increment
                          #   review ran under trust (small body/label addition).

.specify/memory/constitution.md   # Principle V amendment (same PR)
CLAUDE.md                         # "Hardening philosophy" gate-strictness bullet amendment (same PR)

tests/
├── test_review_gate*.py          # strict-mode parity assertions (unchanged behavior)
├── test_task_retry.py            # gate-chain retry behavior under both modes
└── test_gate_policy.py (or new)  # + named regressions (FR-007)
```

**Structure Decision**: Single-project backend change confined to the layer-4
orchestrator (`task_queue.py`) plus governance docs and a small delivery-surface
disclosure. The gate classes and the policy module are **not** modified — the
composition decision lives in the orchestrator, honoring gate_pipeline's stated
rule "Policy never moves into a gate."

## Approach (the seam)

The gate pipeline is assembled inline:

```python
# devclaw/task_queue.py ~L1748 (today)
verdict = await run_pipeline(gate_input, (
    _VerifyGate(), _IntegrityGate(), _ReviewGate(self), _BrowserGate(self),
))
```

Change: build the tuple conditionally on the already-in-scope `strictness`:

```python
gates = [_VerifyGate(), _IntegrityGate()]
if strictness != "trust":
    gates.append(_ReviewGate(self))     # per-increment adversarial review: strict-only
gates.append(_BrowserGate(self))
verdict = await run_pipeline(gate_input, tuple(gates))
```

Consequences, each mapped to a spec requirement:
- **FR-001** (trust = full skip): under trust `_ReviewGate` is absent → never
  constructed, never calls `claude`, no crash surface. Zero calls, zero tokens.
- **FR-002** (strict = byte-identical): under strict the tuple is unchanged from
  today; every existing review-gate test holds.
- **FR-003** (other gates always-hard): verify/integrity/browser positions and
  ordering unchanged; browser stays dial-able via `gate_consequence`.
- **FR-005** (read at gate time): `strictness` is the row snapshot read at
  settle, so a mid-goal flip to `strict` applies at the next task's gate.
- **FR-008** (done-gate untouched): the goal-level `review_repository` done-check
  is a separate path (`goal/` layer), not in this tuple — no change.

## Complexity Tracking

No unjustified constitutional violation. The single declared amendment
(Principle V) is required by the feature's intent and executed in-arc per
Governance, not worked around.

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| Principle V amendment | Trust drops a review-shaped gate from the chain; the invariant text must reflect it or the constitution lies. | Shipping behavior without amending = a spec violation by our own Governance rule. |
| Orchestrator gate-list filter (vs `applies()` on the gate) | Keeps strictness knowledge in the orchestrator where it already lives; honors "policy never lives in a gate". | An `applies()`-based skip would require threading `strictness` into `GateInput` and teaching the gate the dial — moves policy into the gate, the exact thing the pipeline design forbids. |
