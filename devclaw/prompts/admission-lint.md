You are checking a goal's completion contract (`done_when`) at the moment it
is filed, before any work is dispatched. Your only input is the contract text
below. Do NOT infer repository facts — language, framework, layout, tooling,
what exists — from your own working directory, the host process, or any
repository you have seen before; the contract is the sole source, and absent
means unknown.

Find each clause whose satisfaction depends on a DESIGN CHOICE the contract
does not make — a clause a reasonable engineer could implement two or more
materially different ways, where the author would care which. Ignore clauses
that are merely vague about detail but not about direction; ignore wording
you would merely phrase differently. Report nothing when the contract is
decided.

For each undecided clause give the clause text verbatim, the choice in one
sentence, and two to four concrete options the author could pick.

Respond with JSON only:

{{
  "undecided": [
    {{"clause": "<verbatim>", "choice": "<the decision that is missing>", "options": ["<option>", "<option>"]}}
  ]
}}

Contract:

{done_when}
