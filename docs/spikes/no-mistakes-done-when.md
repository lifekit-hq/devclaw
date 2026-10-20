# Spike: does no-mistakes' intent review judge a goal's `## Done when`?

Issue #951 (part of #343, task T1), run 2026-10-21. The question: when a goal's
issue text is passed to `no-mistakes axi run` as the intent, does the review
step judge each `## Done when` clause? The answer decides how the done-gate is
wired on the new engine (#954). The done-gate is kept either way.

**Answer: yes, it judged all five clauses.** It reported both unmet clauses as
blocking `ask-user` findings that quote the clause and the code. It named the
three met clauses only in a free-text summary. It produced no per-clause
evidence for them.

## Setup

- **Tool:** no-mistakes v1.99.0 (`12ad62f`). The review agent was `claude`,
  model `claude-sonnet-5-5`. It ran for 36 s and read 154k input tokens.
- **Branch:** a throwaway goal branch in a treehouse worktree, one commit
  (`06f588b`) on `main` @ `d9ed56e`. The commit adds a `devclaw version`
  subcommand to `devclaw/cli.py` and names it in the README's CLI sentence.
- **Run:** `no-mistakes axi run --intent-file <goal> --skip push,pr,ci`, run
  `01M5DSPN7DC1HKARM7GGWKV6NZ`. The intent step recorded "Using intent supplied
  by the agent". Review parked at its gate and the run was aborted there. The
  branch never reached the remote and was deleted afterwards.
- **The intent file** was the goal issue text, verbatim:

```markdown
Add a `devclaw version` command to the CLI

Operators need a quick way to see which devclaw build is installed on a host without starting the server.

## Done when

1. `devclaw version` prints exactly one line, `devclaw <version>`, where `<version>` is the installed distribution version read through `importlib.metadata`.
2. `devclaw version --json` prints `{"version": "<version>"}` and nothing else.
3. `devclaw version` touches no state: it never opens the SQLite state file or the project registry.
4. The README's sentence that lists the CLI commands names `devclaw version` next to `doctor`, `schedule` and `projects`.
5. When the distribution metadata is missing (`PackageNotFoundError`), `devclaw version` prints `devclaw unknown` and still exits 0.
```

Clauses 2 and 5 were left unmet on purpose. Clause 2 is a missing feature: the
`version` parser takes no `--json`. Clause 5 is a subtle contradiction: the
`PackageNotFoundError` branch prints the right text but returns 1.

## Per-clause result

| # | Clause (short) | Met in the change? | Judged by the review? | Evidence from the run |
|---|---|---|---|---|
| 1 | prints `devclaw <version>` from `importlib.metadata` | yes | **yes**, held (summary only) | `testing_summary`: "Criteria 1, 3 and 4 hold: it prints `devclaw <version>`, …" |
| 2 | `--json` prints `{"version": …}` | **no** | **yes**, flagged | `review-1` (error, ask-user, `devclaw/cli.py:115`): "Criterion 2 is not implemented. … `devclaw version --json` fails in argparse with `unrecognized arguments: --json` and exits 2. `_cmd_version` never reads `args.json`." |
| 3 | touches no state | yes | **yes**, held (summary only) | `testing_summary`: "… it opens no state, …" |
| 4 | README lists `devclaw version` | yes | **yes**, held (summary only) | `testing_summary`: "… and the README names `devclaw version`." |
| 5 | missing metadata prints `devclaw unknown` and exits 0 | **no** | **yes**, flagged | `review-2` (error, ask-user, `devclaw/cli.py:101`): "Criterion 5 is contradicted. … The `except PackageNotFoundError` branch returns 1, and `main()` returns that value as the process exit code." |

The review's risk rationale counted both gaps: "it misses two of the five
required acceptance criteria: `--json` is not implemented, and the
missing-metadata path exits 1 instead of 0". It also said: "No tests were run,
per the review-phase rules. I traced the argparse parser and `_cmd_version` by
reading them." So the review judged by reading the code. It never executed it.

## Why it behaves this way

The review prompt in v1.99.0 makes intent conformance a required check. An
explicit `--intent` is labelled "AUTHORITATIVE acceptance criteria", and the
prompt says:

> If the change contradicts it - it removes or omits a source-verifiable
> behavior the criteria mark as REQUIRED, or adds a behavior they mark as
> FORBIDDEN - you MUST emit an "ask-user" finding that quotes the specific
> criterion and the contradicting diff hunk …

That contract only asks for contradictions. Nothing in the findings schema
reports a clause that is satisfied. That is why clauses 1, 3 and 4 appear only
in the free-text `testing_summary`.

## What this means for the kept done-gate (#954)

no-mistakes judges the clauses, but in a different shape from what
`devclaw/goal/donegate.py` treats as a verdict. The done-gate stays as the
verdict of record. The intent review becomes an earlier, cheaper signal in
front of it.

- **It reports only failures.** `parse_verdict` grants `achieved` only when
  every clause is `satisfied` with non-empty `evidence`. The review gives
  structured findings for broken clauses only, so its silence cannot be read
  as "satisfied with evidence".
- **Its intent is frozen.** The intent is fixed when the run starts, and
  reattaching keeps it. The done-gate reads the issue live (pillar 3), so a
  clause edited or added after the run started is something only the
  done-gate sees.
- **The run's driver can approve past it.** An `ask-user` finding parks the run
  until the driver answers it, and `axi respond --action approve` lets it
  through. If the session drives the run, an approval there is the agent's own
  word. Pillar 7 rules that out, so the done-gate cannot be skipped because the
  review passed.
- **Cheap wiring that comes for free.** Pass the goal issue text as
  `--intent-file` when a session hands off to `no-mistakes axi run`. An unmet
  clause then surfaces before push, as a finding on the run that the host can
  read. On this run that took one 36-second review.

## Limits of this spike

- This was one run on a small, single-file change, and every clause could be
  checked by reading the source. Vaguer clauses ("the console looks right"),
  clauses that need a run to check, and a multi-session goal branch were not
  tried.
- The run was aborted at the review gate. The test step, whose prompt also
  derives scenarios "from the user intent" and returns `go` or `no-go`, never
  ran. Whether it judges clauses on its own was not observed.
