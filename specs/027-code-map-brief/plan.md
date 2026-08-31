# Plan — spec 027 code-map-brief

## Load-bearing choices

- **Architecture pointer lives in `repo_brief.py`** — that module owns all brief assembly; adding `architecture_map_pointer()` there keeps the pattern consistent and avoids spreading brief logic into `tick_dispatch.py`.
- **Pointer prepended BEFORE the repo-notes brief** — the map is navigational context, not an operational note; putting it first means the worker sees the structural orientation before the build quirks.
- **Best-effort never-raises** — same convention as all other dispatch-path probes (branch staleness, slice guard): a probe hiccup degrades to "" and dispatch continues.
- **12 000 char cap** — 3× the prior 4 000; leaves headroom for a mature multi-goal brief without tiering complexity; if evidence shows 12 000 is also too tight, revisit.

## Story slices and their file surfaces

### US1 — Architecture map pointer (P1)
Files/areas touched:
- `devclaw/goal/repo_brief.py` — add `architecture_map_pointer(workspace_dir: str) -> str`
- `devclaw/goal/tick_dispatch.py` — call `architecture_map_pointer` and prepend to `brief_prefix`
- `runner/skills/_common.md` — belt-and-suspenders instruction: read ARCHITECTURE.md if present
- `tests/test_repo_brief.py` — two named tests: pointer present / absent (new file, pure-function, real tmp dirs)
- `tests/test_runner_skills.py` — presence test: ARCHITECTURE.md pointer instruction in always-on brief

### US2 — Raise MAX_BRIEF_CHARS (P2)
Files/areas touched:
- `devclaw/goal/repo_brief.py` — raise `MAX_BRIEF_CHARS` from 4 000 to 12 000
- `tests/test_repo_brief.py` — named test: no eviction at 5 000-char brief (same file as US1)

## Constraints discovered

- The `test_craft_stays_out_of_the_always_on_brief` and `test_writes_code_brief_stays_lean_after_spoonfeeding_cut` tests in `tests/test_runner_skills.py` assert a brief size ceiling (currently 13 200). Adding an ARCHITECTURE.md instruction to `_common.md` will grow the brief; the ceiling must be bumped deliberately (named in the PR body as a judgment call — spec 021 precedent: "ceiling bumps in tests/test_runner_skills.py deliberate and named in the PR body").
- The architecture pointer must not appear in the goal-log or delivery record (the same `_display_goal` separation that keeps the repo-notes brief out of delivery labels).
