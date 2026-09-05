# Research — spec 035 pinned done-gate clauses

Four design decisions, each grounded in the existing code. No
NEEDS CLARIFICATION markers survived the clarify session; these are the
plan-level shape choices under the clarified spec.

## D1 — Where the pinned decomposition comes from

**Decision**: Harvest round 1's own evaluator output. The evaluator already
returns a per-clause array (`devclaw/goal/evaluator.py::_parse_clauses` →
`ClauseVerdict`, populated by prompt step 1). When no pin exists for the
current revision, the round runs in decomposition mode exactly as today; its
parsed clause list is then persisted as the pin (ids assigned mechanically),
and that same round's verdict is already a judgment over those clauses.

**Rationale**: Zero new cognition calls (the spec's Assumption holds by
construction: "the evaluator remains a single cognition call per round").
`claude --print` is the least reliable call class in the system — adding a
separate decomposition call would add a new OOM/timeout/parse surface for no
information gain: round 1 must judge the clauses anyway.

**Alternatives considered**: (a) a dedicated pin-time decomposition call
before the first judgment — rejected: second unreliable call, and its output
would still be one sample of the same variance; (b) mechanical decomposition
(split on list markers/sentences, no cognition) — rejected: `done_when`
clauses carry ANDs/ORs and ceremony text that need judgment to split
correctly (prompt steps 1/1a exist because the mechanical split was never
right); the admission lint (spec 031) already normalizes the worst input
shapes upstream.

## D2 — Clause id scheme

**Decision**: `c1..cN` in pinned order, assigned by `clause_pin.py` at
harvest time — never by the model. Ids are unique within one pin record
only; a re-pin assigns fresh ids (carry-forward maps old→new by
byte-identical text, then re-ids).

**Rationale**: The id is a key, not a semantic; mechanical assignment keeps
the model out of identity bookkeeping (clarify Q1: identity = id + verbatim
text). Per-revision scoping avoids any global sequence state.

**Alternatives considered**: content-hash ids (stable across re-pins for
identical text — attractive, but collides on duplicate clause text and makes
the prompt noisier); model-chosen ids (rejected outright: the party being
constrained must not mint the keys).

## D3 — Where per-clause accounting lives

**Decision**: Inside the pin record itself (`goal_contract_pins.clauses`
JSON: each entry carries id, text, `satisfied`, `evidence`, `satisfied_at`
round, `via_decision` ref), updated through a GoalStore method on the same
round-settlement path that already writes `donegate_progress`.
`goal_status.donegate_progress` keeps its existing meaning (best satisfied
count) — now computed against the pinned denominator.

**Rationale**: One record answers "what is the rubric and where does it
stand" — the audit read the fs-479 post-mortem lacked. Splitting accounting
into a second table would put the denominator and the numerator in different
rows with nothing forcing them consistent. Single-writer holds: only
GoalStore touches it, only from the done-gate round path.

**Alternatives considered**: accounting in `goal_status` columns (rejected:
variable-length per-clause state doesn't fit fixed columns; `goal_status` is
already the widest row in the store); accounting derived fresh from each
round's verdict with no persistence (rejected: FR-011's flip rule needs the
prior satisfied set as its comparison base — deriving it from the last
verdict re-creates a read-back-the-transcript smell).

## D4 — Churn-counter exemption mechanics for malformed rounds

**Decision**: The existing round increment (`tick_donegate.py` — `rounds =
status.donegate_rounds + 1` on the refusal path) moves behind the verdict
classification: a round that produced a *judgment* (achieved / refusal with
clause verdicts) increments; a round that died as a mechanism failure
(`GoalEvalError`: crash, unparseable JSON, unknown clause id, flip without
cause) does not increment `donegate_rounds` and is recorded as a problems-
catalog entry (existing `cognition|review` class), exactly like an evaluator
crash today. The #186 fail-closed consequence is unchanged — the goal does
not close; it just isn't charged a judgment round.

**Rationale**: The churn brake exists to catch *judgment* that refuses to
converge, not formatting failures — the same taxonomy correction as #817
(transient vs terminal), applied at the gate. Without this, FR-002's strict
id enforcement would convert model formatting noise into `donegate_churn`
parks.

**Alternatives considered**: a retry-once-on-malformed loop inside the round
(rejected: adds a second cognition call on the flakiest path — the next
heartbeat round IS the retry, for free); counting malformed rounds but
raising the churn threshold (rejected: a counter where a measurement
belongs).
