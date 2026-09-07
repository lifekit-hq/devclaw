# North star — the test every change passes silently, before any skill is asked

Auto-loaded. This is the always-on half: apply it to every proposal, idea,
PR and adoption in this repo without being asked. The `north-star` skill is
the formal verdict (eight tests, written into the artifact); the
`north-star-case-guard.py` hook is the mechanical check at `gh pr create`.
Nothing here needs to be invoked.

## The destination

**devclaw runs 24/7 non-idle on planned work, nights come out clean, and it
self-heals through stops instead of waiting for the owner.** A helping hand,
not a second job. It fails in exactly three ways, and every change is filed
under ONE of them:

1. **stopped when it shouldn't** — idle with a cause (`get_loop_health`)
2. **ran and produced garbage** — first-pass rate, rounds-to-close
3. **ran but needed the owner** — `interventions` per verb

The failing axis is read live (`get_scorecard_metrics` → `ratchet.checks`),
never remembered; the last recorded read is in
`~/memory/projects/devclaw/STATUS.md`, dated.

## Standing rulings that bind every proposal

- **The owner acts only on decisions** (2026-09-07). `decide`,
  `correct_implementation` and direction rulings are Denys's job; a resume,
  a steer, a button, a hand merge, a hand commit, a status question that a
  change ADDS is a defect it introduces.
- **A system, not a pile of use cases** (2026-07-18, restated 2026-09-07).
  Judge against system design and engineering practice BEFORE the number a
  change moves. Name the class, the layer that enforces it, and count the
  mechanisms already at that boundary — two or more means REPLACE with one
  policy, never add a third.
- **Instruct thin, verify thick** (constitution IX). Software owns five
  things — safety, money, state, the verdict of record, the protocol.
  Close a gap as a fact first, an instruction second, a brake last.
- **Put each decision where it can be enforced** (2026-08-08): an invariant
  → Python, a judgment → the model, a standard → the prompt.

## The silent checklist (answer before proposing, not after)

- Which failure, which number, what cuts it? No answer → not proposed.
- Does the owner do more or less after it ships?
- Class or instance? Mechanisms already at the boundary?
- Heavier or lighter? Standard practice or a devclaw-specific mechanism?
- If it shipped and nothing else changed, what would we observe next week?
  A change that only saves a round, adds a protocol, completes a spec's
  shape, or moves a number without moving behaviour is dropped first.

## When the skill is required, not optional

A new spec or tinyspec (at specify and at clarify), a feature or tool we
adopt, a PR that changes `devclaw/` or `runner/` (the `/ship` ritual runs
it), and a `/devclaw-morning` recommendation. Everywhere else the checklist
above is the judgment, applied in the same breath as the proposal.
