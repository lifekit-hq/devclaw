# Feature Specification: Universal Issue Adoption

**Feature Branch**: `009-universal-issue-adoption`

**Created**: 2026-08-18

**Status**: Draft

**Input**: User description: "Universal issue adoption — extend the intake readiness pipeline (spec 006) so any existing GitHub issue on a registered project can be graded and admitted through the same pipeline as intake-filed issues. Today regrade_intake rejects issues without devclaw's `## What` section, so hand-written backlogs (e.g. finance-sentry's) can never earn a devclaw-ready/needs-refinement label and would be invisible to the future autonomous pickup (spec 007). Make the grader format-tolerant: no `## What` section ⇒ treat issue title + body as the ask; no verifiable completion criteria found ⇒ grade needs-refinement (fail-closed, as today). Possibly add a bulk 'grade all open issues on this project' verb for onboarding existing backlogs."

## Context & Motivation *(informative)*

Spec 006 made asks *gradeable*: every ask filed through the intake doorway lands
a durable `devclaw-ready` / `needs-refinement` label, and that label is the
single admission ticket the whole 006→007→008 arc converges on — humans dispatch
by it today, the autonomous heartbeat (007) will claim by it tomorrow.

But the grader only understands issues the doorway itself wrote. An issue
authored by a human in ordinary GitHub style — a project's pre-existing backlog,
an external contributor's report — has no `## What` section, so the re-grade
verb rejects it ("is it a devclaw intake issue?"). Those issues can never earn a
readiness label, which means they are permanently invisible to the pipeline and
to the future pickup mechanism, even though human-filed work is precisely the
provenance the 007 wall considers safest.

This is a class bug, not a finance-sentry bug (Principle VII): the pipeline's
unit of work is "a GitHub issue on a registered project," and the grader must
accept the unit in the wild shapes it actually comes in — not only the shape
devclaw itself writes. Fixing it makes the pipeline universal: **one pipeline,
multiple entrances** (intake-filed, hand-written, self-filed), all converging on
the same label, with dispatch semantics untouched.

## Clarifications

### Session 2026-08-18

- Q: Should adoption be the existing re-grade verb made format-tolerant, or a separate "adopt" verb? → A: **One verb.** The existing re-grade verb becomes format-tolerant; "adopting" a foreign-format issue and "re-grading" an amended intake issue are the same operation — read the issue as it is today, grade it, land the label. No second tool on the MCP surface.
- Q: Is bulk backlog grading ("grade every open ungraded issue on this project") part of the first slice? → A: **Yes, in P1.** Onboarding an existing backlog is the motivating use case; issue-by-issue adoption alone would make onboarding finance-sentry a dozen manual calls. Bulk is a thin loop over the single-issue path with loud, bounded reporting.
- Q: How much cognition may one bulk invocation spend? → A: **Fixed per-invocation batch cap** (on the order of ~20 issues, priority-first). The ungraded remainder is loudly reported; continuing is an explicit re-invocation by the operator — never an automatic background continuation. Quota spend stays in chunks the operator triggered knowingly.
- Q: On a bulk sweep, does every graded issue get the verdict mirror comment, or only `needs-refinement` ones? → A: **Every graded issue** — identical to the single-issue path. One uniform grade behavior regardless of how it was triggered; no bulk-only special case.
- Q: Is the bulk action its own named tool, or the single-issue verb with the issue URL omitted? → A: **A separate, explicitly named bulk verb** taking only the project. A batch of cognition spend must be requested by name; a forgotten argument must never silently escalate a one-issue grade into a backlog sweep.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Adopt a hand-written issue into the pipeline (Priority: P1)

The operator points devclaw at any existing open issue on a registered project —
regardless of who wrote it or in what format — and it is graded through the same
readiness evaluation as an intake-filed ask, landing the same durable
`devclaw-ready` / `needs-refinement` label with the same fail-closed semantics.
An issue with no recognizable intake sections is read as-is: its title and body
are the ask. If no verifiable completion intent can be found in it, it grades
`needs-refinement` with a concrete missing element named — telling the author
exactly what to sharpen.

**Why this priority**: This is the universality fix itself — without it,
pre-existing backlogs are structurally excluded from the pipeline.

**Independent Test**: On a registered project, take one hand-written issue that
names a locatable surface, a concrete change, and verifiable intent, and one
hand-written issue that is a vague wish; adopt both; verify the first lands
`devclaw-ready`, the second lands `needs-refinement` with an actionable reason,
and neither required reformatting or re-filing.

**Acceptance Scenarios**:

1. **Given** an open issue written in ordinary GitHub style (no intake sections) whose content is groundable against the project's repo, **When** the operator triggers adoption on it, **Then** it is graded and labeled `devclaw-ready` — no re-filing, no edit to the issue body required.
2. **Given** an open hand-written issue with no locatable surface or no verifiable completion intent, **When** adopted, **Then** it is labeled `needs-refinement` and the surfaced reason names at least one concrete missing element.
3. **Given** an issue in devclaw's own intake format, **When** the same verb is triggered on it, **Then** behavior is identical to today's re-grade — the structured sections are honored (no regression for intake-filed issues).
4. **Given** an adopted issue that later lands `devclaw-ready`, **When** the operator dispatches work referencing it, **Then** the dispatch path behaves exactly as for an intake-filed ready issue — downstream, adopted and intake-filed issues are indistinguishable.

---

### User Story 2 - Onboard an entire existing backlog in one action (Priority: P1)

The operator triggers a bulk grade on a registered project; every open,
ungraded issue is run through the same single-issue adoption path. The result
reports, loudly and completely, what was graded ready, what needs refinement,
and what was skipped and why. Already-graded issues are skipped (no cognition
re-spend); the explicit single-issue verb remains the way to force a re-grade.

**Why this priority**: The motivating use case is pointing devclaw at a project
whose backlog already exists (finance-sentry). One call per issue makes
onboarding a chore; the arc's value is "point devclaw at a repo and its backlog
becomes pipeline-visible."

**Independent Test**: On a registered project with a mix of open issues — some
groundable, some vague, some already labeled — run the bulk verb; verify each
ungraded issue received exactly one grade, already-labeled issues were skipped,
and the returned report accounts for every open issue by name.

**Acceptance Scenarios**:

1. **Given** a project with N open issues of which K are ungraded, **When** the bulk verb runs, **Then** exactly the K ungraded issues are graded and the report lists every issue with its outcome (graded-ready / graded-needs-refinement / skipped-already-graded / skipped-with-reason).
2. **Given** a bulk run interrupted partway (e.g. cognition becomes unavailable), **When** it stops, **Then** every issue graded so far keeps its label, every ungraded issue is reported as not-yet-graded (never silently dropped), and re-running the verb later completes the remainder — the operation is resumable by construction.
3. **Given** a project whose open issues are all already graded, **When** the bulk verb runs, **Then** it spends zero cognition and reports "nothing to grade."
4. **Given** more ungraded issues than one invocation's batch cap, **When** the bulk verb runs, **Then** it grades one batch priority-first, reports the exact remainder as ungraded, and does nothing further until explicitly invoked again.

---

### Edge Cases

- **Pull requests**: the bulk sweep considers issues only; PRs are never graded.
- **Closed issues**: out of scope for grading — adoption targets open work. A closed issue passed to the single-issue verb is rejected loudly with the reason.
- **devclaw-self-filed issues**: adoption grades them like any other issue, but grading MUST NOT alter provenance — a self-filed issue that grades `devclaw-ready` still requires the human promotion the 007 provenance wall demands before it could ever be auto-claimed. Grading is not promotion.
- **An issue that is only a title** (empty body): the title alone is the ask; in practice this grades `needs-refinement` unless the title names surface + change + verifiable intent.
- **Very large issue bodies**: the grade proceeds on a bounded excerpt and the bound is stated in the outcome (loud truncation, Principle VI) — never a silent partial read.
- **Usage-limit pause during a bulk run**: grading stops, already-landed labels stay, the remainder is reported ungraded, and the standard pause semantics apply — the bulk verb introduces no new pause behavior.
- **Unregistered project / unreachable issue**: loud synchronous rejection, exactly as the existing verbs behave.
- **Label mutation races** (a human edits labels mid-grade): last-writer-wins on the GitHub label as today; the grade never removes non-readiness labels.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The readiness grade MUST accept an issue in any format. When devclaw's structured intake sections are present they are honored as today; when absent, the issue's title and body together are treated as the ask. Format MUST never be a reason for rejection of an open issue on a registered project.
- **FR-002**: An adopted issue MUST land the same durable readiness label (`devclaw-ready` / `needs-refinement`) as an intake-filed issue, on the issue itself as source-of-truth state, with 006's fail-closed rule fully preserved: any condition preventing a confident ready verdict — including "no verifiable completion intent found in the issue" — lands `needs-refinement`, never `devclaw-ready`.
- **FR-003**: A `needs-refinement` outcome on an adopted issue MUST surface at least one concrete, author-actionable missing element (006 FR-004 extended to adopted issues).
- **FR-004**: Once graded, an adopted issue MUST be indistinguishable downstream from an intake-filed issue: dispatch referencing it, re-grading it after amendment, and any future label-keyed automation (007) operate identically regardless of the issue's origin or format.
- **FR-005**: Adoption MUST NOT create, edit, or re-file the issue body — the issue is read as it is. The only mutations are the readiness label and the grade's verdict mirror comment, recorded exactly as 006 records them — identically for single-issue and bulk grading (no bulk-only special case).
- **FR-006**: Grading MUST NOT alter or fabricate provenance. Filed-by attribution remains whatever GitHub records; in particular, a devclaw-self-filed issue that grades ready still requires explicit human promotion under 007's provenance wall. Adoption is admission to *grading*, never admission to *execution*.
- **FR-007**: A bulk verb — separate and explicitly named, taking only the target project — MUST grade open, not-yet-graded issues on one registered project through the identical single-issue path, skipping already-graded issues without cognition spend, and MUST return a complete per-issue accounting (graded / skipped / not-yet-graded) — bounded coverage is stated, never silent (Principle VI).
- **FR-007a**: One bulk invocation MUST grade at most a fixed batch of issues (on the order of ~20), selected priority-first. When more ungraded issues remain, the report MUST state the remainder explicitly; continuation happens ONLY via a fresh explicit invocation — never automatically.
- **FR-008**: Bulk grading MUST be resumable: an interrupted run leaves no issue in a corrupted or half-graded state, and re-running completes exactly the remainder. The pending set is derived from the labels themselves (006's one-source-of-truth rule), not from any separate progress record.
- **FR-009**: Adoption and bulk grading run ONLY on explicit operator/tool invocation. They MUST NOT run on the heartbeat tick, MUST NOT auto-dispatch, and MUST NOT change idle-tick token cost — the zero-token idle guarantee is untouched (006 FR-009; Constitution III).
- **FR-010**: Repository grounding rules are unchanged (006 FR-008): facts come from the target project's repo context only; absent context ⇒ unknown ⇒ not confidently ready.

### Key Entities

- **Adopted issue**: any pre-existing open GitHub issue on a registered project that has been run through the readiness grade. After grading, it is simply "an issue with a readiness label" — the adopted/intake-filed distinction ceases to exist downstream.
- **Ask (universal form)**: the graded content — structured intake sections when present, otherwise issue title + body verbatim.
- **Bulk grade report**: the complete per-issue accounting a backlog sweep returns (outcome or skip reason for every open issue considered).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of open, hand-written (non-intake-format) issues on a registered project can receive a readiness grade without any human reformatting, re-filing, or issue-body editing.
- **SC-002**: 0% false-ready on adopted issues across the induced failure cases (no completion intent found, empty body, evaluator error, no repo context, paused cognition) — identical to 006's SC-002 bar.
- **SC-003**: Grading an intake-format issue through the same verb produces byte-identical outcomes to the pre-change re-grade behavior in 100% of the existing regression cases (no regression for the intake doorway).
- **SC-004**: A project backlog of mixed-format open issues is fully graded in one bulk invocation (plus resumptions if interrupted), with every open issue accounted for by name in the report — 0 issues silently skipped.
- **SC-005**: Idle-tick cognition and subprocess cost remain zero after the change (the load-bearing zero-token guard tests stay green, untouched).
- **SC-006**: After adoption, dispatching against an adopted `devclaw-ready` issue succeeds through the existing dispatch path with no origin-dependent branching.

## Assumptions

- Adoption targets **open issues only**; closed issues are historical record, not work.
- Already-graded issues are **skipped by the bulk verb** (cognition is not re-spent on a backlog sweep); the single-issue verb is the explicit way to force a re-grade after an amendment — consistent with 006's manual-re-trigger ruling.
- The readiness bar for an adopted issue is 006's unchanged bar ("scoped enough to attempt execution": locatable surface, concrete change, verifiable intent). This spec changes what the grader can *read*, never what it takes to *pass*.
- Bulk sweep ordering follows the existing backlog convention (priority label band, then oldest first) so partial runs grade the most important work first; ordering is a convenience, not a contract.
- The 007 provenance wall and promotion mechanism are untouched; this spec neither builds nor weakens them.

## Out of Scope

- **Automatic pickup of `devclaw-ready` issues** — that is spec 007 (autonomous-issue-dispatch), unchanged and still flag-OFF-by-default.
- **Watching issues** — no polling, no webhooks, no auto-grade on issue creation or edit; grading remains explicit-trigger-only.
- **Grading issues on unregistered repos** — registration stays the perimeter.
- **Deriving `done_when` / checklists at grade time** — 006 FR-006 stands; readiness judges scope-sufficiency only.
- **Cross-repo or org-wide sweeps** — the bulk verb takes exactly one registered project.

## Rejected Alternatives

- **A separate `adopt_issue` tool alongside `regrade_intake`.** Rejected: two verbs whose behavior differs only in what the target issue happens to look like is a format distinction promoted to an API distinction — the exact non-universality being fixed. One verb, format-tolerant. (Ruled in clarify, 2026-08-18.)
- **A hand-applied "devclaw" label as the admission mechanism.** Rejected: a manual tag asserts readiness without grading it, so ungroundable asks would flow to (eventual) autonomous pickup ungated — it bypasses the entire 006 fail-closed gate. The `devclaw-ready` label already is the tag; it is earned, not applied.
- **Requiring backlog issues to be re-filed through `file_intake`.** Rejected: re-filing N issues loses history/comments/cross-references, sprays duplicate issues on the repo, and makes the doorway a format converter — the receipt property it exists for is meaningless for an issue that already exists.
- **Bulk as an optional-argument mode of the single-issue verb** (issue URL absent ⇒ sweep). Rejected: a forgotten argument silently escalating one grade into a multi-call quota burst is a foot-gun; the expensive action must be named to be triggered. (Ruled in clarify, 2026-08-18.)
- **A durable bulk-progress record for resumability.** Rejected for the same reason 006 rejected a pending-grade table: the labels are the one source of truth; deriving the remainder from "open ∧ ungraded" is idempotent and self-healing by construction.

## Constitution Alignment

- **III (zero-token idle)**: adoption/bulk run only on the explicit MCP call path; no tick change (FR-009).
- **V (fail-closed verification)**: the grade's fail-closed choke point is reused, not reimplemented; a foreign format widens what can be read, never what passes (FR-002).
- **VI (loud over silent)**: bulk reports every issue by name; truncation and skips are stated (FR-007, edge cases).
- **VII (fix the class)**: the fix is format-tolerance for the universal unit of work — not finance-sentry support.
