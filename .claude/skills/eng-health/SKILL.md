---
name: eng-health
description: The recurring engineering-health ratchet for the devclaw repo — measures software-engineering and AI-engineering health mechanically (function sizes, layer violations, duplicate bodies, silent excepts, narration noise, spec-header drift, prompt token budgets, schema-vs-parser gaps, eval-fixture coverage, sandbox hardening), reports only what moved since the last run, judges ONE rotating lens deeply, and disposes of every finding into exactly one bin (fix now / promote to guard / spec / accept with a regrade date). Use whenever Denys says "/eng-health", "how healthy is the codebase", "run the health check", "has the code rotted", "score the repo again", after a spec lands, or when /morning-status reports "eng-health due" (15+ merged PRs since the last report). Not for auditing docs (that is /docs-audit) and not for a single bug (that is /root-cause).
---

# eng-health — measure, delta, one lens, four bins

The audit of 2026-09-06 found that every invariant the constitution names
held (a test or an AST guard holds it) and everything it does not name had
drifted (700-line functions, five JSON extractors, prompt schemas that
disagree with their parsers), because nothing measured it. This skill is the
measurement. Its job is to turn judgment findings into mechanical guards
until the review itself is small — **a metric graduates out of the script
the day a tripwire test pins it.**

Two rules that make it a ratchet and not a re-read:

- **Measure first, judge second.** The script's numbers are the evidence;
  the deep read argues from them, never instead of them.
- **A finding reported twice has failed.** Every finding leaves in a bin
  (below). The report's success metric is the count of metrics that
  graduated into guards, not the count of findings.

## 1. Measure (zero LLM, seconds)

```bash
.venv/bin/python evals/measure_eng_health.py --out docs/audits/eng-health.json \
  --compare <(git show HEAD:docs/audits/eng-health.json)
```

The JSON on stdout is the full picture; the delta table on stderr is the
narrative. Metrics and where each comes from:

| Family | Metrics | Source |
|---|---|---|
| Structure | functions over 80 / 200 lines, modules over 1000, signatures over 8 params, private cross-package imports, layer-rule violations | AST over `devclaw/` + `runner/`; the allow-graph in the script mirrors the CLAUDE.md layer map |
| Duplication | AST-normalised duplicate bodies across modules, broad excepts with no raise/log/trace in the handler | AST |
| Noise | issue refs (`#NNN`) in source, legacy phrases, spec headers that still say Draft or are absent while every task is checked, INDEX rows over 1,500 chars, CLAUDE.md length | grep + `specs/*/tasks.md` |
| Prompts | static tokens per template, Python string literals ≥400 chars in each caller (the prompt text that is NOT in the markdown), keys the parser reads that the template never mentions, templates missing the grounding clause, templates with no eval fixture | `devclaw/prompts/`, `quality/prompts/`, their callers, `tests/cognition/fixtures/` |
| Harness | sandbox docker flags versus the hardening set, network mode, `DEVCLAW_*` count | `engine/sandcastle.py`, `config.py` |

Not in the script because it needs the live instance: worker tasks with
recorded usage as a share of all tasks, and brief-size distribution. Read
those from `get_scorecard_metrics` / `get_trace` when the harness lens is up.

## 2. Delta

Only metrics that regressed, crossed a threshold, or are new get a sentence.
Absolute numbers stay in the table. If the delta table says "no metric
moved", say so and skip to step 4 for any finding carried over as `accept`
whose regrade date has passed.

## 3. Judge — one lens per run, rotating

Read the previous report's "Lens" line and take the next one. Each lens has
a fixed question list; answer from the numbers first, then from code, citing
`file:line`. Fan the reading out to Explore subagents when a lens spans many
files and keep only the verdicts.

| Lens | Questions |
|---|---|
| **layering** | Does any layer reach through another (server → queue/engine, goal → docker/gh, quality → queue privates)? Are the mixin splits decompositions or file-splits of one procedure? Which of the over-200-line functions has more than one responsibility? |
| **duplication & noise** | Which duplicate-body groups should collapse to one helper, and where does it live? Which issue-ref comments are provenance rather than a reversal a reader would otherwise reinvent? Which spec headers, INDEX rows or CLAUDE.md sentences narrate history instead of stating the contract? |
| **prompts** | Is each template's schema complete for what its parser reads? Does any caller append prompt text from Python literals that belongs in the markdown? Does every parse failure land on the declared fail direction (closed for gates and admission, best-effort only where the rules allow)? Which prompt has no calibration fixtures and what would three fixtures be? |
| **harness & context** | What does the sandbox pass in (env, mounts, network, caps)? Is the worker's assembled prompt and token usage recoverable host-side? Are budgets measured or char-capped, and what truncates first? Which brake fires on a hung or looping run, and how? |

## 4. Dispose — every finding leaves in exactly one bin

- **fix now** — mechanical and tinyspec-sized (a duplicate helper, a dead
  reference, a header). Done in this session, in this branch. Never an
  issue: issues are for decisions and scheduling.
- **promote to guard** — write the tripwire test that makes the metric
  unable to regress (`tests/`, named after the invariant, extending an
  existing class test where one exists), then **delete the metric from the
  script**. This is the bin the skill exists for.
- **spec** — a class-level design change (a layer boundary, a prompt
  reunification, a sandbox hardening). Open `/speckit-specify` with the
  finding as the problem statement; record the spec id in the report.
- **accept** — carries an owner and a regrade date on the same line
  (`accepted — Denys, regrade by 2026-10-15`). A bare accept is refused.

A finding that touches one of the five software domains (safety, money,
state, the verdict of record, the protocol) is never `accept`; it is `fix
now` or `spec`. Anything else that would add Python outside those five
domains must say which fact or instruction could not close it first
(constitution IX).

## 5. Capture

Overwrite `docs/audits/eng-health.md` — keep-latest, git is the series:

```
# Engineering health — <date> · head <sha> · lens: <name>
## Delta          (the table from step 2, or "no metric moved")
## Findings       (one line each: finding → bin → where/when)
## Graduated      (metrics removed from the script this run, with the test that replaced each)
## Metrics        (the full table, numbers only)
```

`docs/audits/eng-health.json` is written by the script in step 1 and
committed alongside. One dated line in `~/memory/log.md` with the lens, the
delta headline and the graduation count. Nothing else in the vault.

Branch per run (`docs/eng-health-<date>`), one PR carrying the report, the
JSON, any fix-now edits and any new guard tests. Docs-only unless a guard
was promoted; a promoted guard is a tripwire test and ships in the same PR.

## Trigger

Not a calendar. `/morning-status` prints "eng-health due" when merged PRs
since the last report exceed 15 (`git log --oneline <last-report-sha>..main
--merges | wc -l` or the squash count) or when any spec header flips to
Implemented. Denys invokes the skill; never cron — the spec and accept bins
need a human.

## First-run baseline

The 2026-09-06 engineering audit (`docs/audits/2026-09-06-engineering-audit.html`)
is the seed. Candidates for the first promote-to-guard batch, each a rule
the repo already states somewhere: no private import across packages, no
function over 200 lines, every prompt schema key the parser reads is
declared in the template, every cognition caller's parse failure lands
closed, the sandbox flag set is exactly the hardening allowlist.
