# AGENTS.md — a thin pointer, kept honest

AGENTS.md is a THIN, BOUNDED pointer file (~1 page): what the repo is (one
line), the exact build/run/test/verify commands, layout pointers, and links
out to ARCHITECTURE.md, `.agent/skills/`, and `specs/`.
Update it only when the change you shipped makes it wrong — correct the stale
line as part of the change. NEVER append to it: no learnings, no feature
notes, no session
history. NEVER create AGENTS.md if the repo doesn't have one — authoring it
from scratch is onboarding work, not part of a feature or fix task. If an
edit would grow the file rather than correct it, it belongs in a split
target instead: repeatable learnings in `.agent/skills/<topic>.md`, feature
knowledge in the feature's `specs/NNN-*/` artifacts.
