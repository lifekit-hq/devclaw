# Contract: the seams — what each stop must cite, and what happens when it cannot

One row per place text can stop a goal. The columns are the contract every
raise site is held to; the structural guard (research.md D12) checks the
`evidence=` keyword exists, this table says what goes in it.

"Unproven direction" is FR-003 applied through `tick._unproven_stop`
(research.md D3): **trust** = `dropped_claim` row + log line, the loop
proceeds; **strict** = Problem raised with `evidence.proven=false`, `drop`
recommended, standard timebox. "n/a (fact)" means the stop is a mechanical
fact whose citation is proven by construction; "n/a (design)" means FR-016
— always raised, never dropped.

| # | seam (module) | stop kind / raised_by | the claim | citation shape | check · snapshot | unproven direction | catalog row |
|---|---|---|---|---|---|---|---|
| 1 | intake staleness (`intake_readiness.validate`, `intake.grade`) | not a goal stop: a readiness revocation | `stale: true` | `path:<file>[:line\|#symbol]` from `stale_evidence` | `path_in_tree` · the graded workspace's `git ls-files` set (D6) | **both modes**: grade as not stale; the comment names the missing / unfound citation (D5) | `intake_staleness` |
| 2 | readiness revoked at dispatch (`tick.py`, all open refs unready) | `needs_answer` / `dispatch_park` | "the owner revoked readiness" | `report:grade:<repo>#<n>` — the latest `intake_grades` row; **or** `owner` when no grade row revoked it (a hand-pulled label) | `report_present` · `store.intake_grade(repo, n)` | n/a (fact): a grade row either exists or the owner acted; the Problem always carries one of the two | — (a single unready issue among ready ones narrows scope: the log line cites the grade; no stop, no Problem) |
| 3 | worker env report (`tick_settle` → `tick_guards._block_on_env_deficiency`) | `mechanical:env` project hold | "the sandbox lacks `<credential>`" | `probe:<cap_id>` — the superseding capability when the item names a registry credential | `probe_registered` + the probe row reads **green** · `env_cap.read_result` (persisted, zero network) | green probe ⇒ the report is the claim, the probe the fact: **trust** no hold, superseded with the probe id; **strict** Problem `needs_answer`/`worker_block` citing the probe, options `(PROCEED, SUPPLY, CANCEL)`, default `proceed`. Red or unknown probe, or no registered credential ⇒ the hold stands exactly as today (`report:<task_id>`, proven) | `env_report` |
| 4 | worker honest block — design stop (`tick_settle`, `BLOCKED: <conflict>`) | `needs_answer` / `worker_block` | "the plan cannot be built as designed" | `report:<task_id>` | `report_present` · `store.increment_records(goal_id)` | n/a (design): always raised in both modes with the finding as `what` and the worker's recommendation (the text after `BLOCKED:`) as the first option's label when present | — |
| 5 | dispatch cap (`tick_dispatch`) | `mechanical:dispatch_cap` / `dispatch_cap` | "cap N reached" | `report:<task_id>` of the last failed increment | `report_present` | n/a (fact). **FR-010**: `what` = `dispatch cap N reached — last failure: <gate or command>: <first error line>`; "review the open PRs" only when an increment delivered | — |
| 6 | done-gate `needs_human` / `stalled` (`tick_donegate`) | `needs_answer` / `done_gate` | the evaluator's question | `clause:<pinned id>` when an unsatisfied clause exists; else `report:<review task id>` (a design conflict, FR-016) | `clause_pinned` · the active pin; `report_present` | n/a (design when `report:`); with `clause:` proven by the pin | — |
| 7 | done-gate churn park (`tick_donegate`) | `donegate_churn` / `churn_park` | "N rounds flat" | `clause:<first unsatisfied pinned id>` | `clause_pinned` · the active pin | n/a (fact): after US2 a round with every pinned clause satisfied returns `achieved`, so a churn park always has an unsatisfied clause to cite; a malformed round never charges churn (spec 035) | — |
| 8 | done-gate structural axis (`evaluator.validate`) | was: `off_track` under `strict` | "structural: concerns / poor" | — | — | **removed (US2)**: with every clause satisfied the verdict is `achieved` in both modes; concerns ride the close as follow-ups | `done_gate_structural` (one row per close that carried follow-ups; shows the path fires) |
| 9 | CI definition absent / broken (`tick_donegate._block_on_ci_definition`) | `env` / `done_gate` | "no CI to read" | `manifest:ci` (the rollup state `no_workflows` / `broken` on the head) | `manifest_declared` · `remote_checks` result already read | n/a (fact) | — |
| 10 | pointer contract closed while the gate refuses (`tick.py`, `closed_contract`) | `needs_answer` / `closed_contract` | "the contract's source is closed" | `clause:<first unsatisfied pinned id>` | `clause_pinned` | n/a (fact): the gate's last refusal names the clause; if the pin has none unsatisfied the shortcut re-proposes done instead (unchanged first pass) | — |
| 11 | admission lint undecided choice (`service.create_goal`) | `admission` / `admission_lint` | the lint's question | `decision:<admission decision id>` when a rewrite was recorded; else `report:grade:<repo>#<n>` for a pointer goal; else `owner` (the caller's own `done_when` text is the input) | `decision_recorded` / `report_present` / `owner` | n/a (design before dispatch — spec 031 class c) | — |
| 12 | goal `verify_cmd` vs manifest `verifyCmd` (`goal/admission.py`, `service.set_verify_cmd`) | a **refusal**, not a stop | the caller's command | `manifest:verifyCmd` | `manifest_declared` · `load_manifest_at_base` | n/a: refused with the manifest command named, nothing persisted; a bare tool name refused on every project; clearing to empty stays allowed (#897) | — |
| 13 | change_class declared scope (`task_change.build_paths`) | a gate **fact**, not a stop | "gate-input edit" | the ticket's own backticked path | exact / glob match of the declared token against the changed path, whatever source classified it (#895) | n/a: an undeclared gate-input edit still fails closed | — |
| 14 | pre-PR review refusal (`task_queue._review_failure`, `quality.filter_blockers`) | `strict`-consulted gate failure | each blocking issue | `path:` — the issue's `location` prefix | `path_in_tree` · span paths (`ChangeSet.paths`) ∪ pre-run tree (`git ls-tree -r --name-only <base_sha>`) | an issue citing neither is moved to `dropped`; no surviving blocker ⇒ approve with advisories (PR body + task); any surviving blocker ⇒ fail closed as today with the dropped ones named (US3) | `review_refusal` |
| 15 | lost ref / corrupt doc / merge failed / env_cap (`tick_guards`, `tick_donegate`) | `mechanical:*` in `HUMAN_GATED_MECHANICAL_KINDS` | a destroyed ref, an unreadable plan, a failed merge, a heal budget spent | `report:<task_id>` or `owner` | `report_present` | n/a (fact) — these raise no Problem today; FR-001 says every stop is a Problem. **Scope decision**: they gain one in US1 with fixed options (`SUPPLY`/`CANCEL`, `CORRECT`/`CANCEL`) and `timebox_s=0`, so `resume_goal` stays the exit but the stop is visible in the Problem history and the console banner; the existing kinds and heals are untouched | — |

## Invariants every row obeys

- A **pass** at any seam with no citation still fails closed (FR-004): the
  intake `ready` verdict, the review `approve`, the done-gate `achieved`
  keep their evidence requirements; this table only governs stops.
- The check is existence and shape, never truth (spec edge case 1).
- The snapshot is one the seam already holds or one bounded read inside a
  seam that already spent its call; no seam gains a cognition call, and no
  idle or blocked tick runs a check (constitution III).
- Two facts disagree ⇒ the mechanical snapshot wins: the probe over the
  report (row 3), the tree over the grader (row 1), the span over the
  reviewer's memory (row 14).
- The owner's text (`decide`, `correct_implementation`, `steer_goal`, a
  label pull) is `check="owner"` and never checked.
