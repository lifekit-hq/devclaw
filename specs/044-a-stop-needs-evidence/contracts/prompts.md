# Contract: prompt and skill changes

Three files, each ruled by `.claude/rules/cognition-prompts.md`: state the
rule once, imperatively, no incident history in the template, presence AND
absence asserted where a test exists.

## `devclaw/prompts/intake-readiness.md`

**The staleness check** section gains one sentence and the schema gains one
field:

> Report `stale` as true only with `stale_evidence`: the repository path,
> with a line number or symbol, at which the described condition is already
> resolved. A stale claim without that path is treated as not stale.

```
"stale": true | false,
"stale_evidence": "<path:line or path#symbol in the Repository context, or empty>",
```

**Grounding** section gains the FR-017 line (the design-stop feedback
edge), placed after the existing "never infer" sentence:

> Ground the ask against the world it depends on — a provider's documented
> capabilities, prior findings recorded in the repository's own notes or
> issues — not only against the code.

Nothing else in the template changes; the existing `test_prompt_asks_the_staleness_question_and_declares_the_output_field`
is extended to assert `stale_evidence` is present in the schema block.

## `devclaw/prompts/goal-evaluator.md` (US2)

Line ~148, "the host applies the goal's strictness dial to the structural
axis", becomes:

> the host records the structural axis as follow-ups on the close; it never
> holds a met contract open.

The matching sentence in `evaluator.build_prompt` (the `(B) STRUCTURAL`
paragraph) is corrected the same way. No new field.

## `~/.claude/skills/dispatch-ready/SKILL.md` (FR-017 — a user-level skill on the PC, outside this repo; edited by hand at implement time, not shipped in the PR)

The grounding step gains the same one line as the intake prompt, so a
hand-graded issue and a machine-graded one are held to the same rule:

> Ground the ask against the world it depends on (provider capabilities,
> prior findings in the repo's own notes and issues), not only against the
> code; a design stop later is a miss at this step.

## What does NOT change

- `runner/skills/` — the worker's `STATUS: BLOCKED: env — <item>` /
  `BLOCKED: <conflict>` protocol is read as-is; the host, not the skill,
  now decides what a report is worth (constitution II, one home).
- `devclaw/quality/prompts/review-gate.md` — it already demands
  `location: <file path and function/area or line>`; US3 enforces it in
  Python rather than adding a second instruction.
