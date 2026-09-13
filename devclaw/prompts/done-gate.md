Read-only review: decide whether this repository satisfies the contract. You produce the evidence and the verdict; the host validates it mechanically. Be specific and honest, not generous.

{untrusted_note}

## The contract

{contract}

## Procedure

1. Decompose the contract into atomic clauses (independent requirements joined by AND; "X with Y, including Z" is three). Drop delivery ceremony — how it ships, which branch, who merges, which issues close — those are never clauses.
2. For EACH clause find specific evidence in the repository: file path + symbol + test name. "The code handles it" is not evidence. For a clause about tests, run the suite or cite the run in `.devclaw/verify`; a file that merely exists proves nothing. A matching name, a stub, a `not implemented` payload, a skipped test — none of these satisfy a clause.
3. A clause the repository deliberately contradicts (an intentional, working behaviour the code explains) is a question for the owner, not a failure: say so in `question`.
4. Judge structural health as a senior engineer: would you hand this codebase to a new hire? Name concerns with file:line.

## Answer

Your final message must end with a fenced ```json block and nothing after it:

```json
{{
  "achieved": true | false,
  "clauses": [
    {{"clause": "<text>", "satisfied": true | false, "evidence": "<file:symbol / test, or 'missing — should live in <path>'>"}}
  ],
  "question": "<only when a clause is a deliberate design contradiction; else empty>",
  "structural_health": "clean" | "concerns" | "poor",
  "concerns": ["<file:line — what — the fix>"],
  "summary": "<2-3 sentences>"
}}
```

`achieved` is true only when EVERY clause is satisfied with non-empty evidence. Nothing else counts.
