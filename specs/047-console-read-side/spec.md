# Feature Specification: the read side of v2 — the console shows what devclaw knows

**Feature Branch**: `047-console-read-side`

**Created**: 2026-09-14

**Status**: SHIPPED — 2026-09-14: US1 needs-you (#915), US2 drill-down (#917), US3 usage (#918), US4 verdicts. Remainder is a live read, not a build: SC-002/SC-003 after 14 nights and the cut condition — issue #919 (owner Denys, by 2026-09-28); the Grafana token panel is lifekit-stack#153.

**Input**: Denys, 2026-09-14: "what I don't like about the recent demolishing of devclaw is
that we removed evals and the problems page. And we removed drill-down from project to goal
to task. We removed token usage and so on. Can we return those back? It's just a UI, isn't
it? And when the goal needs me I want to get those options for me to unblock it, with a
recommended option from the agent, just like you would ask me there."

Ruled the same day, after grounding against the code: two of the five were only a UI
(drill-down, usage — their producers and JSON survived 046); three were not. The **problems
catalog** (host-typed failure kinds with lifetime counts) and the **eval corpus**
(`eval_outcomes`, `cycle_reports`) were producers 046 deleted on purpose, and Denys reaffirmed
both deletions today. They come back as their v2 equivalents — a *needs-you* page over blocked
goals and a *verdicts* page over done-gate reviews — never as the v1 tables.

## Clarifications

### Session 2026-09-14

- Q: For blocks the host writes itself (done-gate refusal, red CI), should the review session hand back options too? → A: No — host-authored blocks stay free-text only; `done-gate.md` and its parser are untouched.
- Q: After the owner clicks an option, how should the goal appear until a session picks the decision up? → A: An "answered, waiting for the tick" row on the needs-you page, showing the answer and why the tick has not run (hold, window, running session); derived from decision time newer than the block time, no new state.
- Q: Should usage be its own console page or inline numbers? → A: Inline — tokens on the session page, totals on the goal page and in the projects list; no usage route, no usage page. The instance history is the `/metrics` counter in Grafana.
- Q: Should verdicts be an instance-wide page, a per-goal section, or both? → A: Both, page primary — a verdicts page newest first across goals, and the same rows filtered on each goal page.

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: **ran but needed the owner.** Today a block costs the owner a read of
  a truncated row, a click into the thread, and a written sentence. The session already
  hands back `BLOCKED: <question> — default: <what you would do>`; the host throws the
  structure away and the console offers a blank box. After P1 the owner's cost is one click
  on the session's recommended option. P2–P4 move nothing by themselves; they are the
  read side the owner uses to *decide well* (what happened, what it cost, what the gate
  found) and they add no verb.
- **Number that shows it**: **time-to-decide** per blocked goal (block posted → decision
  made, both timestamps exist today) and **decisions typed vs. clicked**. Baseline on v2:
  every decision typed; time-to-decide unread. Target after 14 nights: ≥ 70% of decisions
  on session-authored blocks are a click on an offered option.
- **Cut when**: after 14 nights, fewer than half of the session-authored blocks carry
  usable options (the session cannot produce them honestly) → P1's structured hand-back is
  cut back to question + default and the buttons go. A read page nobody opened in 30 days
  (console access log) is deleted with its route.

### north-star verdict (2026-09-14, at specify): SHRINK to P1, P2, P4 and the per-row half of P3

- **P1 ADMIT** — removes an owner action (the typed sentence, the thread visit) and adds
  no verb; a click is `decide`. The boundary already has ONE parser of the block line
  (`runner/runner.py`: `_BLOCKED_LINE_RE`, `_classify_block`, the `env` sub-form,
  `blocked_payload`); the options EXTEND that parser and payload. A second parser in the
  host's settle step would be a third mechanism at the seam and is refused at plan.
- **P4 ADMIT** — the only live read of the "ran and produced garbage" axis left after 046
  deleted the scorecard; without it the north star's failing number cannot be read.
- **P3 SHRINK** — per-session, per-goal and per-project totals stay: row facts in the
  money domain, the fact the quota brake lacks. The per-day instance history is a time
  series, and the box already runs Prometheus + Grafana on `/metrics`; a console page
  re-implementing a series over rows is the v1 `Usage.tsx` regrowth. The history becomes
  one token counter on `/metrics`; Grafana holds the series.
- **P2 ADMIT as substrate** — moves no number by itself; it is what a P1 decision reads
  (deciding blind is the sentence-typing being removed). First cut if the 30-day rule fires.
- Weight: 0 kinds, 0 env vars, 0 tables, ~3 computed routes, 5 pages, one payload field.
  Reversible one PR per story.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Needs you: unblock with one click (Priority: P1)

The owner opens the console and sees every goal that is waiting on them: the question the
session asked, the options the session laid out, and which one the session recommends. One
click on an option posts it as the decision on the issue (the existing `decide` verb, the
one owner verb); the next tick reads it. Free text stays for an answer the session did not
foresee. The host never adds an opinion: the options and the recommendation are the
session's own words, carried back in its hand-back line and parsed mechanically.

**Why this priority**: it is the only story that moves the north-star number. Everything the
owner does with devclaw is a decision; making the common decision one click is the whole
"owner acts only on decisions" ruling made concrete.

**Independent Test**: seed a goal whose last session exited `BLOCKED` with a question, two
options and a recommendation; open the needs-you page; click the recommended option; the
issue thread shows a devclaw decision comment with that option's text; the goal's
decisions list shows it; the page no longer lists the goal as needing the owner.

**Acceptance Scenarios**:

1. **Given** a session ends with a block that names a question, options and a default,
   **When** the host settles the session, **Then** the goal's row exposes the question, the
   ordered options and which one is recommended, verbatim from the session.
2. **Given** a blocked goal with options, **When** the owner opens the console, **Then**
   the needs-you page lists it first, showing the question, each option as a button, the
   recommended one marked and placed first, and a free-text field.
3. **Given** the owner clicks an option, **When** the click completes, **Then** exactly one
   `decide` is posted carrying the option's full text (never a bare letter or index), and
   the goal moves from the needs-you section to the "answered, waiting for the tick"
   section, which shows the answer and why no session has read it yet (operator hold, run
   window closed, a session running on the lane); it leaves the page once a newer session
   exists.
4. **Given** a session ends with a block that names a question and a default but no
   options, **When** the owner opens the page, **Then** it shows the question, one button
   "take the default: <text>", and free text.
5. **Given** a block the host wrote itself (a red CI on a delivered head, a done-gate
   refusal, an environment gap naming a credential), **When** the owner opens the page,
   **Then** it shows the host's fact (the CI log link, the gate's clauses, the credential
   name) with free text and cancel, and no buttons (ruled 2026-09-14: host-authored blocks
   stay free-text only; `done-gate.md` and its parser are unchanged by this spec).
6. **Given** a session hands back a malformed block line (no question, or options that
   cannot be read), **When** settled, **Then** the goal still blocks with the raw line as
   its question and no buttons — never a dropped block, never a fabricated option.
7. **Given** a blocked goal waiting on a credential the registry can probe, **When** the
   credential arrives, **Then** the goal leaves the needs-you list on the next tick without
   any owner action (today's behaviour, now visible).

---

### User Story 2 - Drill down: project → goal → session (Priority: P2)

From a project the owner reaches its goals; from a goal its sessions; from a session
everything the host recorded about it: the exit line, the events in order, what the verify
run said, what was delivered, the change span, and the agent's final output. Every link is
a page, every page is reachable by URL.

**Why this priority**: the JSON for all of it already exists; only the pages were deleted.
Without them the owner reads sessions in a truncated row or in raw JSON.

**Independent Test**: from the projects page, reach a specific session's events in at most
three clicks; the session page shows the same task and events the JSON routes serve.

**Acceptance Scenarios**:

1. **Given** a project with goals, **When** the owner opens it, **Then** each goal is a
   link showing its state word and last exit.
2. **Given** a goal with sessions, **When** the owner opens a session, **Then** the page
   shows kind, exit, exit detail, timestamps, the verify result, the delivery result, the
   change span, the events in order, and the agent output, or an honest "not recorded"
   for an absent part — never an empty value that looks like a result.
3. **Given** an unknown session id in the URL, **When** opened, **Then** the page says so.

---

### User Story 3 - Usage: what a session, a goal, a project cost (Priority: P3)

Each session shows the tokens it spent, as the runner summed them from the agent's own
transcript. A goal shows its sessions' total; a project its goals' total — inline on the
pages that already exist, never a usage page of its own (clarified 2026-09-14). Nothing is
stored for this: every number is a live read over the session results that already exist,
and a session that reported no usage is shown as "not reported", never as zero. The
instance-wide history over days is NOT a console page: it is one token counter on
`/metrics`, and the box's Grafana holds the series (north-star verdict, 2026-09-14).

**Why this priority**: the producer survived 046 (the transcript sum, spec 039 US3); the
owner is paying with a subscription's daily allowance and today cannot see what a goal
consumed.

**Independent Test**: seed sessions with usage blocks and one without; the goal total
equals the sum of the reported ones; the one without reads "not reported"; `/metrics`
exposes the instance total as a counter that equals the sum over all reported sessions.

**Acceptance Scenarios**:

1. **Given** a session whose result carries usage, **When** the owner opens it, **Then**
   input, output, cache-read and cache-creation tokens are shown.
2. **Given** a goal, **When** opened, **Then** its usage total is the sum over its
   sessions, with the count of sessions that reported nothing stated next to it.
3. **Given** the projects list, **When** opened, **Then** each project shows its usage
   total as the sum over its goals, computed live; no new stored counter exists and no
   usage page or route exists.
4. **Given** the `/metrics` page, **When** scraped, **Then** it carries one instance-wide
   token counter computed over reported sessions, so the per-day history is a Grafana
   panel and never a console page.

---

### User Story 4 - Verdicts: what the done-gate found (Priority: P4)

Every done-gate review the host ran is listed per goal and delivered head: achieved or
not, each clause with its evidence, the structural-health word, the concerns, and the
question if the reviewer raised one. The verdict is read from the review session's own
output, parsed by the same mechanical rule the gate uses.

**Why this priority**: the gate's finding is the fact a refusal stops on; today the owner
reads it as a PR comment. A list across goals shows whether the gate is refusing on the
same kind of clause repeatedly — the calibration signal the closed corpus spec wanted,
without a corpus.

**Independent Test**: seed a review session with a verdict of three clauses, one
unsatisfied; the verdicts page shows the goal, the head, "not achieved", and the three
clauses with evidence; an unreadable verdict is shown as unreadable.

**Acceptance Scenarios**:

1. **Given** review sessions across goals, **When** the owner opens the verdicts page,
   **Then** each shows goal, head, achieved/not, and clause count satisfied/total, newest
   first.
2. **Given** a verdict, **When** expanded, **Then** every clause shows its text, satisfied
   or not, and its evidence string verbatim.
3. **Given** a review whose output holds no readable verdict, **When** listed, **Then** it
   reads "unreadable" with the reason — the same word the gate posted.

---

### Edge Cases

- A goal blocked twice: only the latest session's question and options are offered; older
  blocks are visible on the goal page as history.
- The owner clicks an option while a newer decision already exists on the thread: the
  click still posts (a decision is a comment; the newest wins on the next tick) and the
  page shows both.
- Options longer than a button can carry: the button shows the first line, the full text
  in the expanded card; the posted decision is the full text.
- A session reports usage for a run that was interrupted and resumed: each session's own
  sum stands; the goal total sums sessions, never de-duplicates.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The session hand-back MUST be able to carry, in its block line, the question,
  an ordered list of two to four options, and which option the session recommends; the
  prompt template and its parser change in the same increment (cognition-prompts rule).
- **FR-002**: The host MUST parse the block line mechanically into question, options and
  recommended option, keep them as the session's words on the session's own record, and
  MUST NOT add, rank, reword or invent an option.
- **FR-003**: A block line without options MUST still block the goal with its question and
  default; a block line the parser cannot read MUST block the goal with the raw line as its
  question. No block is ever dropped for being malformed.
- **FR-004**: The console MUST show a needs-you page listing every open goal whose last
  session exited blocked, ordered oldest block first, with the question, the options as
  buttons (recommended first and marked), a free-text field, and cancel.
- **FR-004a**: A blocked goal with a decision newer than its blocking session MUST be shown
  in a separate "answered, waiting for the tick" section with the decision text and the
  reason no session has read it (hold, window, lane busy), with no buttons; this is derived
  from existing decision and session timestamps, never stored.
- **FR-005**: Clicking an option MUST post exactly one decision through the existing decide
  verb, carrying the option's full text; the page MUST reflect the posted decision without
  reload.
- **FR-006**: Host-authored blocks (red CI on a delivered head, done-gate refusal,
  environment gap) MUST be listed on the needs-you page with the host's fact and free text;
  the host MUST NOT offer options it authored itself.
- **FR-007**: A project page MUST list its goals as links; a goal page MUST list its
  sessions as links; a session page MUST show the task record, its events in order, the
  verify, delivery and change-span results, and the agent output, each absent part stated
  as not recorded.
- **FR-008**: A session page MUST show the usage the runner reported for it (input, output,
  cache-read, cache-creation tokens) or "not reported"; goal and project totals MUST be
  computed live over session records, with the count of unreported sessions shown, inline
  on the session, goal and projects pages (no usage page or route); the instance-wide
  total MUST be exposed as one counter on `/metrics` and MUST NOT be a console page or a
  stored series.
- **FR-009**: No new persisted counter, aggregate, classification or verdict table MAY be
  introduced (constitution IV); every number on the read side is a live read.
- **FR-010**: A verdicts page MUST list every done-gate review across goals, newest first,
  with goal, delivered head, achieved/not, satisfied/total clauses, and on expansion every
  clause with its evidence, the structural-health word, concerns and the reviewer's
  question; a review with no readable verdict MUST read "unreadable" with the gate's
  reason. Each goal page MUST show the same rows filtered to that goal.
- **FR-011**: Every page MUST be reachable by URL and MUST survive an empty instance
  (no projects, no goals) with a plain empty state.
- **FR-012**: The v1 problems catalog and the v1 eval corpus MUST NOT be reintroduced in
  any form (tables, routes, pages, host-side failure kinds).

### Key Entities

- **Block**: the session's hand-back when it needs the owner — question, optional ordered
  options, the recommended option; authored by the session, stored verbatim on its
  session record, never edited by the host.
- **Decision**: the owner's answer, posted on the issue thread as the one owner verb;
  already exists (`decisions`); a clicked option becomes one.
- **Session record**: an existing task row — kind, exit, exit detail, result (verify,
  delivery, change span, agent output, usage), events.
- **Verdict**: the done-gate's parsed JSON — achieved, clauses with evidence, question,
  structural health, concerns — read from the review session's output at display time.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A session-authored block is answerable in one click from the needs-you page,
  with no typing, and the decision appears on the issue thread within the same page load.
- **SC-002**: After 14 nights on this spec, at least 70% of decisions on session-authored
  blocks are clicked options rather than typed text (read from decision texts vs. offered
  options).
- **SC-003**: Time-to-decide per blocked goal is readable on the goal page and on the
  needs-you page for every block since the spec shipped.
- **SC-004**: Any recorded session's events are reachable from the projects page in at
  most three clicks.
- **SC-005**: For every session that reported usage, the session page shows it; for every
  goal, the total equals the arithmetic sum of its reported sessions; no page shows a zero
  for an unreported session.
- **SC-006**: Every done-gate review that ran since the spec shipped appears on the
  verdicts page with all its clauses; unreadable verdicts are listed, not hidden.
- **SC-007**: Zero new persisted tables or columns holding aggregates, classifications or
  verdicts (constitution IV), verified by the schema.

## Assumptions

- The block line stays a single trailing line the session writes; its structured form is
  a plan-level decision (an extended line or a small fenced block), chosen so a session
  that ignores the structure still blocks correctly (FR-003).
- The recommended option is mandatory whenever options are given; the session states it
  in its own words, as today's "default" already does.
- A clicked option posts the option's full text as the decision, prefixed by nothing; the
  thread already shows the question in the block record above it.
- The needs-you surface is its own page, first in the navigation, with a count badge; the
  goal page keeps its decide box.
- Usage is tokens only. Cognition runs on a Pro/Max subscription; there is no money
  figure to show and none is estimated.
- Usage covers worker sessions and review sessions alike; v2 has no host cognition, so
  there is no other spender.
- Verdicts are done-gate reviews only; a session's own pre-DONE self-review is not a
  verdict and is not listed.
- Goals blocked on a probeable credential (`BLOCKED: env — needs <name>`) appear on the
  needs-you page with the credential name and no buttons; they leave on their own when the
  probe turns green (existing behaviour).
- No confirmation dialog on an option click: a decision is a comment and a wrong one is
  corrected by another decision; cancel keeps its confirmation.
- Existing JSON routes for projects, goals, tasks and events are reused and gain the usage
  fields; new reads (the needs-you list, the verdicts list) are routes computed on request.

## Not in scope (ruled 2026-09-14)

- The v1 problems catalog: host-typed failure kinds, lifetime counts, the problem
  lifecycle. The v2 problem is a blocked session with one question, and that is P1.
- The v1 eval corpus and calibration views (`eval_outcomes`, `cycle_reports`,
  `/calibration.json`): closed by ruling 2026-09-06; the verdicts page is the honest read.
- Loop-health, scorecard and ratchet reads (spec 039): superseded by 046; time-to-decide
  and clicked-vs-typed above are the only numbers this spec adds, read live.
- A per-day usage history page (v1 `Usage.tsx`, "cap-pressure history"): shrunk by the
  north-star verdict to one `/metrics` counter; Grafana is the series.

## Rejected alternatives

- **A second parser of the block line in the host** (settle reading options out of
  `exit_detail`): the runner already parses the line into a typed payload; a second reader
  drifts from the first. Extend the one parser.
- **Host-authored options for host-authored blocks** (the host proposing "accept the
  finding as a follow-up" on a done-gate refusal): host cognition by another name;
  excluded regardless of the open clarification.
- **A stored decisions-clicked counter or a time-to-decide column**: both derivable from
  `decisions.made_at` and the blocking task's `completed_at`; constitution IV.
- **The v1 problems catalog and eval corpus**: see Not in scope; reaffirmed 2026-09-14.
- Answering a block from the Telegram ping: the ping keeps carrying the question and the
  default as text; the click lives in the console.
