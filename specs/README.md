# `specs/` — the estate and its ledger

Direction memory for behaviour-changing work, one directory per spec, written
and executed through the pipeline in [`.claude/rules/speckit-workflow.md`](../.claude/rules/speckit-workflow.md).
Small work uses the [tiny lane](./tiny/README.md) instead.

## If it is in the tree, it is alive

This directory is read as a work queue — by the owner at `/devclaw-morning`, and
by anyone asking what is left. So a directory that describes work nobody intends
to do is not a harmless archive; it reads as schedulable. Spec 007 read as
schedulable for six weeks after it was parked, and spec 036's US3 stopped its own
clock by hiding `SPECIFIED, NOT IMPLEMENTED` inside a user-story heading where no
header sweep could see it.

One closed vocabulary, checked by `tests/test_spec_estate_is_alive.py`:

| Word | Where it lives | Means |
|---|---|---|
| `SHIPPED` | the tree | every user story built; the remainder, if any, is a live-verification task with an issue |
| `PARTIAL` | the tree | some stories built — **names an owner and a date**, always |
| `DRAFT` | the tree | specified, nothing built — **names an owner and a date**, always |
| `CUT` | this ledger | deliberately dropped; the history is the archive |
| `SUPERSEDED` | this ledger | replaced by another spec, named below |

`PARKED`, `SUSPENDED`, `DEFERRED` and `NOT IMPLEMENTED` are not in the
vocabulary. A parked spec is `PARTIAL` with a clock, or it is `CUT` — those two
are the only honest states, and picking one is the owner's call. Non-terminal
states carry an owner and a date because a label with no clock stops one
(`~/memory/README.md` rule 4).

**A number is an identity, never a position.** Numbers are never reused and
never renumbered: they are cited in `CLAUDE.md`, the constitution, code comments,
tinyspec bodies, PR titles, issues, the memory vault, and in merged history,
which cannot be renumbered at all. A gap is readable, not untidy — every number
ever issued resolves here or to a directory, and the test enforces exactly that.
A citation to a number in this ledger resolves to the row, and the row names why
the directory went away.

## Terminal ledger

| # | State | Name | Disposition |
|---|---|---|---|
| 002 | SUPERSEDED | Planning-strategy dial | By 008. The PLAN.md planning port was rejected 2026-08-15/16 for speckit-everywhere. Removed from the tree 2026-09-10; it was retained for three weeks as "the record of a rejected alternative", which is what the history is for. |
| 007 | CUT | Autonomous issue claim & dispatch | 2026-09-10. Its own gate — the spec 018 autonomy ratchet — read `pass: false` with a first-pass rate of 0.0 against a 0.70 threshold, and the scarcity it solved does not exist: `all_planned_done` idle was 37 minutes in 14 days. Feeding self-selected work into a loop closing 0-in-9 first-pass multiplies garbage rather than output. None of its machinery (operator flag, CAS'd claim marker, provenance wall, human promotion) was ever built. The ratchet keeps its meaning as the compounding-readiness signal; there is no longer a flip behind it. |
| 033 | VOID | — | Never issued. The number was skipped when 034 was created; no spec ever carried it. |
| 040 | SUPERSEDED | The contract reaches the actor | By 043, 2026-09-10. US2 (a red CI verdict carries its failing log) shipped as `tiny/red-ci-log-to-worker.md`; US1 (the pinned clause list at dispatch) is absorbed into 043 with its clarify answers; US3 (per-clause report, UNMET routing) was deleted at 040's own shrink on 2026-09-07. Merged rather than shipped beside 043 because five mechanisms had accumulated at the what-the-worker-knows boundary (021, 026, 029, 040 US1, 043) and the standing ruling allows one policy there, never a third mechanism. |

## Stories cut inside a living spec

| Story | Cut | Why |
|---|---|---|
| 032 US4 — the declared verification environment, provisioned or refused | 2026-09-10 | The `environment` declaration surface shipped 2026-09-03 and stays; the provisioning-or-refusal half is dropped. This is Q1's rejected option A, chosen a week later on evidence: the class it would own — an environment gap holding a project — already carries five mechanisms (030's capability check, 042's registry + probes, 038's filing honesty, and the two `env-hold-*` tinyspecs), and one policy is allowed at a boundary, never a sixth mechanism. Spec 042 US2 owns the class from the legibility side. Accepted consequence, named in the spec: the integration class stays CI-only — which spec 032 itself made the verdict of record. |
| 036 US3 — the sandbox reports the outage structurally | 2026-09-10 | Belt-and-suspenders behind US1+US2, which already classify host-side; no `server_error` recurred in the 14 days to 2026-09-10. The permanent assumption this leaves is stated in 036's Assumptions, along with what reopens the cut. |
| 040 US3 — per-clause worker report, UNMET routing | 2026-09-07 | Deleted at 040's own north-star shrink; not revived by 043 without a fresh case. |
