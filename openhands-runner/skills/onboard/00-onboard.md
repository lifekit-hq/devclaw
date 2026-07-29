# Onboarding mode (produce AGENTS.md + README + ARCHITECTURE.md + DECISIONS.md + .devcontainer/Dockerfile)

You are ONBOARDING this repository: produce the standardized artifacts so a future engineer (and an automated agent) can start work already informed AND in a real environment. Devclaw's C6 exit criterion (`~/memory/projects/devclaw/plan.md` §Production-ready) is that a new project ships with all four docs at init, not just AGENTS.md — because a project with only AGENTS.md is undocumented from a human's point of view. Onboarding also establishes the project's **dev-environment boilerplate** (`.devcontainer/Dockerfile`) so every later task runs in the project's real toolchain instead of re-deriving it each time.

Inspect the repo **READ ONLY** — read files and run read-only inspection commands (`ls`, `cat`, `grep`, `git log`, `find`, reading config/manifest/lockfiles, etc.). Do NOT modify, create, or delete ANY file EXCEPT the onboarding artifacts described below — the four documentation files, plus `.devcontainer/Dockerfile` when the repo has none; in particular do not change any source, build, or config file.

## The four documents

Each doc lives in the repository root and has a specific scope. Do not blur them: cross-linking is fine, duplication is not.

### 1. `AGENTS.md` — COMPREHENSION for agents

Describe **WHAT IS** so an agent can start work informed:

- **Stack & languages** — frameworks, runtimes, key dependencies + versions.
- **Layout** — the important directories/modules and what each is for.
- **How to build, run, and TEST it** — the exact commands, and call out the single command that should be used as the verification gate (what proves a change is good).
- **Conventions** — code style, naming, branching, commit/PR norms you can infer from the repo.
- **Setup prerequisites and gotchas** — toolchain versions, env vars, services, anything non-obvious that bites a newcomer.

Do NOT include project direction, roadmap, opinions about what to build next, or a decision log — those go in the other three docs.

### 2. `README.md` — HUMAN-FACING introduction

Describe **WHAT THIS PROJECT IS** so a first-time human reader understands the purpose in under five minutes:

- **Project purpose** — one paragraph. Why does this exist; who is it for; what problem does it solve.
- **Quickstart** — the minimum commands to clone, install, run, and see something work locally (three commands ideal, five max).
- **What's inside** — a high-level pointer at the directory structure (link to AGENTS.md for the detailed layout — don't duplicate).
- **Status** — one line: pre-release / stable / experimental / archived / etc. Truthful.

Keep it short. A README that reads like a novel loses its audience before the quickstart.

If a substantive README already exists AND is accurate, leave it unchanged. If a placeholder README exists (one line, "TODO", boilerplate from a generator), replace it.

### 3. `ARCHITECTURE.md` — DESIGN of how the pieces fit together

Describe **HOW IT WORKS** so a reader understands the boundaries + data flow without reading the source:

- **Component map** — the major components/modules/services and what each is responsible for. One paragraph per component. This is the level at which architectural decisions get made.
- **Data flow** — how a request / job / event flows through the components. Sequence diagram in prose is fine; ASCII / mermaid welcome.
- **Cross-cutting concerns** — auth, logging, error handling, tests, deploy, whichever apply.
- **Notable design decisions** — for each: what was decided, why, and what was rejected. Cross-link to DECISIONS.md for the full record.

Placeholder for diagrams is acceptable if you can't draw one directly — write `<!-- diagram: <what should go here> -->` and describe it in prose.

### 4. `DECISIONS.md` — ADR-STYLE log of choices made

Each entry is a short (paragraph-length) ADR:

- Date (approximate, from git log or "unknown").
- Title — "Chose X over Y for Z".
- Context — why this decision was in front of the team.
- Decision — what was picked.
- Consequences — what this bought and what it cost.
- Alternatives — what else was considered.

Reconstruct these from `git log`, comments in the code, and any prior README / ARCHITECTURE / doc content. When you honestly cannot infer the reasoning behind a design decision, mark the entry `(reconstructed; may need review)` — the human reviewer can then confirm or correct. Fewer high-quality entries beat many speculative ones.

If nothing significant is inferrable, DECISIONS.md should still exist with a header and a one-line note that no ADRs have been captured yet — an empty log with a heading is still doc infrastructure.

## The dev-environment boilerplate — `.devcontainer/Dockerfile`

Every future task for this project runs inside a container built from this file, and a human developing the project can use the SAME file — one environment, shared by human and agent. Give the project a real, reproducible DEV environment:

- **Create it ONLY when the repo has none.** If `.devcontainer/Dockerfile` already exists (a human's, or a prior onboarding's), LEAVE IT UNCHANGED — it is the source of truth. Same non-clobber discipline as the docs.
- **It is a DEV image, NOT a deploy image.** Base it on the official **SDK** image for the stack you identified in AGENTS.md — e.g. `mcr.microsoft.com/dotnet/sdk:<major.minor>`, `node:<major>`, `golang:<major.minor>`, `rust:<major>` — which ship the compiler/SDK, a shell, and the package managers. Do NOT write a slim multi-stage *production* image that strips the SDK; that cannot build or test.
- **Cover every toolchain the build/test commands need.** If the project builds with more than one (a .NET backend + a Node frontend, say), install all of them so the full verify gate — `dotnet test` AND `npm test` — runs in this single container.
- **Debian/Ubuntu-based.** All the official SDK images are; devclaw layers its own agent harness on top of yours, and that layer assumes a debian base.
- **Toolchain ONLY.** Do NOT `COPY` the application in (the workspace is bind-mounted at run time), and do NOT install devclaw/agent tooling (claude, the runner) — devclaw adds that layer itself. Keep it exactly the environment a human developer would want, nothing more.
- Put a one-line `#` comment at the very top marking it a DRAFT generated by devclaw onboarding for human review.

If you cannot confidently determine the base image for the stack, still write your best-effort Dockerfile and note the uncertainty in that top comment — a human reviews it, and a wrong toolchain fails the build LOUD rather than degrading silently.

## Rules across all four docs

**Draft marker:** for any doc you CREATE (not update), put a one-line note at the very top marking it as a DRAFT generated by devclaw onboarding for human review. On subsequent updates the marker stays until a human removes it.

**Do not clobber:** if a doc ALREADY exists AND is substantive, do NOT blindly overwrite it — validate each part against the actual repository, KEEP everything still accurate, and only correct or fill in what is wrong, stale, or missing (preserving the existing structure). If an existing doc is already fully accurate, leave it unchanged.

**Boundary discipline:** each doc has one job. Don't put ADR-style reasoning in README; don't put quickstart commands in ARCHITECTURE; don't put decision rationale in AGENTS.md. Cross-link instead.

**Read-only otherwise:** everything else in the repo is read-only during this task.

## Summary

End with a short summary to STDOUT in your final message: for each of the four docs, whether you CREATED, UPDATED, or LEFT UNCHANGED, plus the two or three most load-bearing facts you captured (per doc). Then one line for `.devcontainer/Dockerfile`: CREATED (name the base image + toolchains) or LEFT UNCHANGED (a dev container already existed).
