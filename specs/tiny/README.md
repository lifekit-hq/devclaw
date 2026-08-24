# specs/tiny/ — the tinyspec lane

Single-file specs for work below spec size (bug fixes, mechanical refactors,
small bounded changes) — see `.claude/rules/speckit-workflow.md`, "Below spec
size". Written by `/speckit-tinyspec-tinyspec`, executed by
`/speckit-tinyspec-implement`, each file self-contained: What / Context /
Requirements / Plan / Tasks / Done-When. Deliberately OUTSIDE the `NNN-`
sequential numbering: tiny specs never collide with, block, or masquerade as
pipeline features. A tiny spec that grows scope graduates to
`/speckit-specify` — it does not accrete here.
