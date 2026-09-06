# Engineering health — 2026-09-06 · head 8bc7e59 · lens: all four (seed run)

Seeded from the 2026-09-06 engineering audit (`2026-09-06-engineering-audit.html`); the first run of `/eng-health`. Keep-latest: each run overwrites this file, git is the series. Next lens: **layering**.

## Delta

First run — no previous `eng-health.json` to diff against.

## Findings

- Admission lint fails open on a malformed model reply (`devclaw/goal/admission_lint.py`) → **spec** — protocol/verdict domain; fail-closed is a constitution V matter, not a tinyspec
- Sandbox runs `--network host` with none of pids-limit / cap-drop / no-new-privileges / read-only (`engine/sandcastle.py`) → **spec** — safety domain; `sandbox_hardening_missing` = 4 stays a metric until the flag set is a guard
- Done-gate parser requires `flip_cause`, template schema never declares it; review-gate parser reads `blocking`, template never mentions it → **fix now** candidate for the prompts lens (reunite the Python-appended blocks into the markdown first — `prompt_python_literal_chars` = 22512)
- Two duplicate bodies across modules: `extract_json` (evaluator ↔ triage), `_parse_retry_after` (loom/limits ↔ runner) → **fix now** — one helper each; the four near-identical extractors the audit found collapse here
- Worker token usage never recorded; the assembled worker prompt never emitted host-side → **spec** — telemetry for the expensive path (live-instance metric, not in the script)
- 13 functions over 200 lines, 37 signatures over 8 params, 47 private cross-package imports, 1 layer violation (`quality/task_gates.py → queue`) → **accept — Denys, regrade by 2026-10-15**: structural; candidates for the layering lens next run; `private_cross_imports` is the first promote-to-guard candidate
- 270 issue refs and 133 legacy phrases in source → **accept — Denys, regrade by 2026-10-15**: apply the prompt comment rule to Python as a rule change first, then the number is a guard
- 5 of 6 prompts have no eval fixtures (only the evaluator has any) → **spec** — the done-gate calibration set is the named highest-value unbuilt measurement; fixtures for the other prompts ride the same spec
- Seven spec headers said Draft (018–022, 025, 029) and three carried none (026–028) after shipping → **fixed now** in this run; `spec_status_mismatches` = 0 — promote-to-guard candidate (a test over `specs/*/tasks.md` vs the header)

## Graduated

None yet. First-batch candidates: `private_cross_imports`, `functions_over_200`, `parser_keys_missing_from_schema`, `spec_status_mismatches`, `sandbox_hardening_missing`.

## Metrics

| Metric | Value |
|---|---|
| `source_loc` | 47966 |
| `functions_over_80` | 55 |
| `functions_over_200` | 13 |
| `modules_over_1000` | 6 |
| `signatures_over_8_params` | 37 |
| `private_cross_imports` | 47 |
| `layer_violations` | 1 |
| `duplicate_body_groups` | 2 |
| `silent_broad_excepts` | 79 |
| `issue_refs_in_source` | 270 |
| `legacy_phrases` | 133 |
| `spec_status_mismatches` | 0 |
| `index_bytes` | 13875 |
| `index_rows_over_1500` | 0 |
| `claude_md_lines` | 338 |
| `prompt_static_tokens_total` | 4864 |
| `prompt_python_literal_chars` | 22512 |
| `parser_keys_missing_from_schema` | 2 |
| `prompts_missing_grounding` | 0 |
| `prompts_without_eval_fixtures` | 5 |
| `sandbox_network` | host |
| `sandbox_hardening_missing` | 4 |
| `config_env_vars` | 84 |

| Prompt | est. tokens | schema keys | py-literal chars in caller | fixtures |
|---|---|---|---|---|
| `devclaw/prompts/goal-evaluator.md` | 1593 | 14 | 9065 | 5 |
| `devclaw/prompts/admission-lint.md` | 223 | 4 | 1348 | 0 |
| `devclaw/prompts/intake-readiness.md` | 1160 | 8 | 3131 | 0 |
| `devclaw/prompts/self-triage.md` | 512 | 5 | 2124 | 0 |
| `devclaw/quality/prompts/review-gate.md` | 799 | 7 | 4786 | 0 |
| `devclaw/quality/prompts/browser-reachability.md` | 577 | 2 | 2058 | 0 |
