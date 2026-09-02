---
name: root-cause
description: How a devclaw problem gets fixed — at the root, never at the symptom. Use whenever Denys reports a problem, incident, smell, wedge, or "why did X happen" on devclaw or on a repo devclaw drives, asks to fix something, or when an audit/digest surfaces a recurring failure. Refuses instance fixes; traces symptom → mechanism → design decision → root, names the class, and proposes the fix at the root as a spec before any patch. Also the tool for reviewing an incoming fix PR for "is this the symptom or the cause".
---

# root-cause — fix the class, never the instance

Denys's standing rule (2026-07-18 doctrine, restated 2026-09-03): **if a problem
smells bad, fix its root cause.** A fix that only unwedges the case that hurt
today is a smell in itself. This skill is the procedure; the doctrine lives in
`CLAUDE.md` ("Design doctrine — systemic over specific").

## The procedure

1. **Collect the symptoms as facts.** Timestamps, log lines, file:line, goal
   ids, PR numbers. No adjectives. If the same symptom appears twice with a
   different surface, list both — two instances of one class is the first clue.
2. **Find the mechanism.** Read the code path that produced the symptom until
   you can point at the line that made the decision (`file:line`). Not the
   line that raised the error — the one that chose the wrong thing.
3. **Ask "why is that mechanism there?" until you hit a design decision or an
   ownership boundary.** Every mechanism was put there on purpose (an ADR, a
   spec's rejected-alternatives list, a skill's instruction, a defaulted
   parameter). Name the decision and the assumption it rests on. Stop when the
   next "why" is a question about the world, not about the code.
4. **Name the smell in one sentence.** The smell is the *shape* of the decision,
   stated so it would be recognisable in a different codebase: "two sources of
   truth for X", "the judge reads the defendant's diary", "policy encoded as a
   keyword list", "state of system A persisted in repo B", "done ends one hop
   before the user".
5. **Inventory the instance fixes already stacked on this root.** Grep the
   specs, ADRs, prompts and tests for earlier reactions to the same class. Each
   one is evidence for the class and a candidate for deletion once the root
   is fixed — a root fix that leaves the band-aids in place is half a fix.
6. **Propose the fix at the root, as a spec.** Behaviour-changing work starts
   at `/speckit-specify` → `/speckit-clarify` with Denys (rules/speckit-workflow.md).
   The spec names: the root, the class, the instance fixes it retires, the
   invariant it adds or amends (constitution), and the rejected alternatives —
   including "patch the instance", with the reason it was rejected.
7. **Only then stop the bleeding**, explicitly labelled as such. A hotfix for
   the live instance is fine (a broken production is a broken production) but
   its PR body says "instance fix; root tracked in spec NNN". Never let the
   hotfix close the conversation about the root.

## Smells that recur in this codebase (recognise them fast)

- **Two environments, one declared truth.** Verification runs in a place that
  is not where the code will run, and its verdict is treated as the verdict.
- **The judge reads the defendant's diary.** A gate whose only evidence is the
  tree written by the party being judged (tests, specs, tasks.md, AGENTS.md).
- **Policy as a keyword list.** A regex or word list standing in for a
  classification the system should make from typed facts.
- **State of system A persisted in repo B.** Sandbox lore, workarounds and
  "repo conventions" that describe devclaw's environment landing in a
  product's AGENTS.md, CI config or install scripts.
- **The only writable surface is the wrong one.** An actor that can only act
  on X will fix every problem by editing X, including problems that live in Y.
- **Done ends one hop before the user.** Merge, deploy, delivery and "it
  works" each owned by someone else — or no one.
- **A counter where a measurement belongs.** Brakes and budgets that count
  rounds/chars/attempts instead of the thing that matters (progress, value,
  product code changed).
- **Instance fixes stacking.** Three specs in three weeks that each answer one
  incident of the same class are the loudest signal that the root is untouched.

## What this skill refuses

- A fix whose PR body cannot state the class it closes.
- A test minted for the instance when a class test exists (rules/testing.md).
- A new keyword, special case, or `if project == …` branch as the fix.
- Closing an incident on a hotfix alone.

## Output shape

When Denys asks about a problem, answer in this order: the root in one
sentence, the mechanism with `file:line`, the design decision it came from,
the instance fixes already stacked on it, the proposed root fix (spec), and
the bleeding-stop if one is needed. Short. He will argue back; that is the
point (vault: agent-behavior.md → "Argue by default").
