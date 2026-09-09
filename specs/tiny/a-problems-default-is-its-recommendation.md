# Tiny spec — a Problem's default is shown as its recommendation

## North-star case

- **Failure moved**: *ran but needed the owner.* The owner acts only on
  decisions (2026-09-07), so a decision surface that hides which option the loop
  will take makes the one action he is supposed to take harder than it is.
- **Number**: `interventions.decisions` per achieved goal — **15 decisions of
  1.296 interventions/goal** in the 14 days to 2026-09-09. The observation this
  spec comes from: on 2026-09-09 the owner read a `mechanical:dispatch_cap`
  Problem on `issue-493-fix-hardcoded-test-d17bfc` and asked *"I can only
  continue or cancel… I remember we had the recommended option as well from the
  agent. Why is it not there anymore?"* — the default was there in the payload
  and not on the screen.
- **Cut when**: if the recommended badge is shown and owners still ask which
  option the loop prefers, the badge is not the missing thing and this is
  reverted rather than elaborated.

## What

Two display fixes in `console/src/pages/GoalDetail.tsx`. No server change, no
new state, no new verb.

1. A spec-031 Problem's `default` is passed to `BlockedBanner` as
   `recommended`, so the option the loop will take on timeout carries the
   **recommended** badge — the same treatment an ADR-0010 `needs_answer` block
   already gets. The `" (default)"` suffix is dropped from the option label so
   it is not said twice.
2. The free-text button is named for the verb it calls. While a Problem is open
   it invokes `correct_implementation`, so it reads **"Write a correction…"**;
   otherwise it stays "Write your own answer…".

## Context

`BlockedBanner` highlights an option when `o.key === recommended`, and
`recommended` was read exclusively from `data.blockOptions.recommended` — the
ADR-0010 structured-decision payload for a `needs_answer` block. A spec-031
Problem populates `problem.default` instead and never populates `blockOptions`,
so `recommended` was `""` and **no option was ever highlighted on any Problem**.
The default was not lost — it was folded into the option's label text as a
` (default)` suffix, which does not render as the badge and does not read as a
recommendation.

So the regression the owner noticed is real and is exactly one line wide: the
recommendation moved home when Problems arrived (spec 031) and the banner was
never repointed.

**Correcting the record on the same observation.** It was also reported in
conversation that the console does not expose the free-form decision path at
all. That is **wrong** — `onCustom` has been wired to a free-text box since
ADR 0010, and `doSteer` already routes it to `correct_implementation` when a
Problem is open. The button renders whenever the banner has options, which a
Problem always has. Only its label was misleading, which is fix 2. No new path
is added here, and the claim that one was missing should not survive in the
direction memory.

**Rejected — surface `decide(text=…)` as a third control.** The server accepts
free-form `decide` text as well as `correct_implementation`, so a "decide in
your own words" box could be added beside the correction box. Rejected: two
free-text boxes at one blocked banner is a worse decision surface than one, the
distinction between them is a devclaw-internal one the owner should not have to
hold, and `correct_implementation` restores the full budget which is what a
blocked goal wants. One box, correctly named.

**Rejected — make the timebox countdown visible too.** Genuinely useful and
genuinely a different change: it needs a live-updating relative time and a
decision about what to show once elapsed. Not smuggled into a label fix.

## Requirements

- **R1** When a goal has an open Problem, the option whose key equals
  `problem.default` renders with the recommended badge.
- **R2** No option label contains a `(default)` suffix — the badge is the one
  statement of which option the loop prefers.
- **R3** A `needs_answer` block with `blockOptions.recommended` is unchanged.
- **R4** The free-text button reads "Write a correction…" while a Problem is
  open and "Write your own answer…" otherwise; the verb it calls is unchanged.
- **R5** `tsc --noEmit` and `vite build` clean. The bundle itself is NOT
  committed: `devclaw/server/console_dist/` is gitignored and built inside the
  image (`deploy/Dockerfile`, `npm --prefix console ci && run build`), so the
  change reaches the running console on the next deploy and a local build is the
  only pre-merge verification available.

## Plan

1. `console/src/pages/GoalDetail.tsx` — derive `recommendedOption` from
   `problem.default`, falling back to `blockOptions.recommended`; drop the label
   suffix; thread a `customLabel` prop into `BlockedBanner`.
2. Verify with `tsc --noEmit` + `vite build`. Nothing to commit from the
   build — the image rebuilds the bundle at deploy time.

## Tasks

- [x] T1 recommended badge reads the Problem's default (R1, R2, R3)
- [x] T2 the free-text button is named for its verb (R4)
- [x] T3 `tsc --noEmit` + `vite build` clean; bundle is image-built, not
  committed (R5)

## Done when

- A blocked goal with an open Problem shows its default option badged
  **recommended**, and no label says "(default)".
- A `needs_answer` block still badges `blockOptions.recommended`.
- The free-text button names the verb it calls.
- `pytest`, `ruff check .`, `mypy`, `lint-imports` clean (no Python touched).
- Visible only after a deploy rebuilds the image's console bundle.

## No test

Display-only, and the suite is a tripwire net, not a coverage instrument
(`.claude/rules/testing.md`): this touches no tripwire class — no zero-token
guard, no fail-closed gate, no CAS path, no credential hop, no materialize span.
The console has no test harness at all, so a test here would mean building one
for a two-line prop change. The live console is its regression surface.
