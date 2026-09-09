# TinySpec: a verify gate narrower than CI declares the gap instead of hiding it

**Branch**: `fix/verify-gate-is-never-narrower-than-ci`
**Date**: 2026-09-09
**Status**: IMPLEMENTED 2026-09-09 — reviewed and admitted by Denys; it stayed inside the brief, so it stayed a tinyspec
**Complexity**: small, but it touches spec 032's verdict-of-record seam — reclassify to a full spec if the fix grows past the brief

## North-star case

- **Failure moved**: ran and produced garbage. The worker's gate says PASSED, CI says red, and the worker ships increment after increment onto a branch that has been red since the increment that broke it.
- **Number that shows it**: rounds-to-close on a goal whose branch is red. fs-431 is at 10 increments and 9 days, against a rounds median of 2 and a max of 12.
- **Cut when**: a goal branch goes red on a category the verify gate does run. Then the gate was not the blind spot and the class is elsewhere.

## Root cause, grounded on fs-431

fs-431's verify gate is:

```
dotnet test FinanceSentry.sln --no-build -c Release -m:1 --filter 'Category!=Integration'
```

The failing CI check is `build-and-test`, and per the log Denys pasted by hand on 2026-09-08 the failures are `Hangfire.BackgroundJobClientException` and `ObjectDisposedException` thrown at host startup by **integration** tests booting the API through `WebApplicationFactory`.

The gate excludes exactly the category that is failing. So the gate is not a weak check, it is a check of a different thing, and it returns PASSED with full confidence every round.

Three walls stand behind it, and each alone is fatal:

| Wall | Evidence |
|---|---|
| The gate does not run the failing category | `--filter 'Category!=Integration'` in the goal's own verify_cmd |
| The sandbox cannot host the failing surface | Worker, 2026-09-07: "the sandbox is aarch64 with no Docker daemon and no Postgres, which are exactly the three CI-only surfaces the failure must live on" |
| The failure cannot be read either | Worker: "job logs return 403 (Must have admin rights), and every UI log endpoint 404s unauthenticated" |

With all three closed, the only remaining lever that touches CI's behaviour is the CI file, so the worker edited five workflow files and the always-hard `change_class` gate correctly killed it. That is not a worker-quality failure. Three separate workers filed accurate, honest reports, one of them explicitly asking for "a human with repo admin to read that log".

`main` is green (Backend CI success on 2026-09-09 00:39 and 2026-09-08 11:51), so the red belongs to the goal branch alone and has been carried forward across increments.

## Requirements

1. When a project's verify command narrows the suite (a category filter, a skipped tier, a tag exclusion), that narrowing is a declared fact in the dispatch brief, not a silent property of a string. The worker is told, in one line, which tier its gate does not run.
2. A `verify PASSED` on a branch whose CI rollup is red never reads as agreement. The brief states that the gate passed a strict subset and CI remains the verdict of record (spec 032, unchanged).
3. The sandbox's own capability gaps that a project's CI depends on (no Docker daemon, no Postgres, a different architecture) are declared once per project, so a worker never spends a session rediscovering them. Two workers wrote them into REPO NOTES independently; that is the fact arriving too late and in the wrong place.
4. No new gate, no new brake. This is a fact reaching the actor. Constitution IX order.

## Rejected alternatives

- **Run integration tests in the sandbox.** It cannot host them. aarch64, no Docker daemon, no Postgres. Building that is a second CI, which is the thing spec 032 deliberately stopped doing.
- **Drop the category filter from the verify command.** The filter is there because the tier cannot run. Removing it turns a silent pass into a guaranteed red, which is worse.
- **A brake that fails a task when verify is narrower than CI.** That punishes every project with a Docker-gated tier, which is most of them. The gap is normal; hiding it is the defect.

## The open question, decided: DECLARED, never derived

The spec left one question open — *where the narrowing is computed without
devclaw learning a test runner's flags.* Only one of the two answers survives
its own constraint:

- **Derive it** from `verifyCmd` + the CI check names. Devclaw would have to
  read `--filter 'Category!=Integration'`, and the day it knows dotnet's filter
  grammar it owes the same for pytest, jest, go test and every runner after.
  That is exactly the project-tooling knowledge constitution IX keeps out of
  the harness. Rejected.
- **Declare it.** The project states, in its own words, what `verifyCmd` does
  not run. Devclaw carries the sentence verbatim — unparsed, unjudged — into
  the dispatch brief. One `environment.verifyExcludes` list.

Requirement 3 needed no new schema at all. Spec 032 US4 already declares
`environment` (image, services, tools, registries) and left it *"consumed by
nothing"*. This tinyspec is what consumes it: the block renders into the brief,
so a worker learns what CI verifies in before it spends a session
rediscovering that the sandbox is not that environment. Both halves are one
declaration read from the default-branch tip — a worker can write
`devclaw.json` on its own branch (the #358 class).

## What was deliberately NOT built

**The `_ci_correction` line (plan step 3).** The correction already opens with
*"The sandbox gate is not CI"* and forbids editing the CI definition; the new
declaration reaches the worker on **every** dispatch, the red-CI correction
round included. A second home for one fact is the smell, and the steering
section is tail-capped, so an added sentence competes with the job log for the
budget. Requirement 2 is met by the brief line, which says the same thing
earlier and once.

**Any gate or brake.** Requirement 4, held. Nothing new fails, holds or
retries; this is a fact reaching the actor, per IX's fact-before-brake order.

## Note on wall 3 and PR #886

The root cause named three walls. The third — *"job logs return 403, the
failure cannot be read either"* — is fixed by tinyspec
`github-credential-in-the-registry` (PR #886), which puts `actions:read` on a
declared credential and checks it in doctor. That was the load-bearing wall:
with the log arriving, a worker can often see the failing tier itself. This
spec still earns its place, because a log tells you what broke *after* a red
round while the declaration tells you what your gate cannot see *before* you
spend one — but if the two ship together and the case stops appearing, the
honest read is that #886 did the work. Watch the number, not the pair.

## Plan

1. Derive the narrowing from the verify command and the project's CI check names, and render one line into the brief. Where it lives is the open design question for review: the brief builder is the natural home, but it must not become project-tooling knowledge inside devclaw (IX).
2. Add the per-project sandbox capability declaration, sourced from the project's `devclaw.json` rather than inferred.
3. Extend the existing red-CI correction (`_ci_correction`) to say the gate passed a subset, on the red path only.

## Tasks

- [x] Decided: DECLARED, never derived — `environment.verifyExcludes`, carried verbatim (see above)
- [x] Brief line, in `devclaw/goal/repo_brief.py` beside the two existing pointers; red-CI correction line deliberately NOT added (see *What was deliberately NOT built*)
- [x] Per-project verification-environment declaration — spec 032 US4's `environment` block, previously parsed and consumed by nothing, is now read into the brief
- [x] Docs (`devclaw-manifest.md`, the JSON schema) + INDEX currency tag
- [x] No test: the brief's content is not a tripwire class, and a project that declares no `environment` renders a byte-identical brief (verified by hand against a seeded repo)

## Done-When

A goal whose verify command excludes a tier CI runs shows that fact in its dispatch brief. No worker writes a sandbox capability gap into REPO NOTES that the brief could have told it. fs-431's branch red is diagnosed from the brief rather than from a hand-pasted log.
