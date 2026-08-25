# Data Model: Goal-as-Pointer

## Extended: `Goal` (goal.yaml / devclaw/goal/models.py)

| Field | Type | Notes |
|---|---|---|
| `issue_refs` | list[int] = [] | ordered issue numbers against the goal's project repo; non-empty ⇒ referenced lane, empty ⇒ issue-less lane (today's behavior). Additive, dataclass-defaulted — every existing goal.yaml loads unchanged |

Validation at creation (all hard refusals, FR-010 messages):
- budget: `len(objective + note) <= DEVCLAW_GOAL_TEXT_BUDGET` when refs present
- each ref: exists, same-project repo, graded ready, carries an acceptance
  section (only when `done_when` is defaulted), not referenced by another
  live goal, no duplicates in the list

## New module surface: `goal/issue_ref.py`

| Item | Shape | Notes |
|---|---|---|
| `IssueSnapshot` | title, body, state (open/closed), labels, fetched_at_ms | ephemeral — threaded into ONE brief or ONE gate round, never persisted |
| `IssueFetcher` | protocol: `fetch(repo, number) -> IssueSnapshot` | gh-backed default, `FakeIssueFetcher` in tests (injection per remote_checks precedent) |
| `extract_acceptance(body) -> str \| None` | mechanical section slice | spec 015 convention; None ⇒ refuse default / block gate |

## Extended: config (config.py doorway)

| Env var | Default | Meaning |
|---|---|---|
| `DEVCLAW_GOAL_TEXT_BUDGET` | `1000` | max chars of free text on a referenced goal |

## State transitions touched

- **Dispatch boundary (referenced item)**: fetch → open+ready ⇒ brief built
  from snapshot; closed or ready-revoked ⇒ skip + loud log + advance;
  fetch error ⇒ BLOCK (existing lost-ref, human-gated).
- **Done-gate round (defaulted done_when)**: fetch scenarios live → evaluate;
  scenarios absent or fetch error ⇒ BLOCK the round legibly (never evaluate
  an empty contract).
- **Creation**: any validation failure ⇒ nothing persisted.

## Explicitly NOT stored

- Issue content snapshots (freshness by construction — a creation-time copy
  is unrepresentable).
- A lane flag (derived from `issue_refs`, D8).
- Any per-ref claim table (exclusivity is a live-goal scan; 007's CAS claim
  remains the autonomous-path mechanism).
