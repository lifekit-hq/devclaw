# Onboarding mode (produce AGENTS.md + README.md + ARCHITECTURE.md + .devcontainer/Dockerfile)

You are ONBOARDING this repository: produce the standardized artifacts so a future engineer (and an automated agent) can start work already informed AND in a real environment. The doc set is three scoped documents — a thin AGENTS.md pointer for agents, a README.md for humans, an ARCHITECTURE.md for design — plus the project's **dev-environment boilerplate** (`.devcontainer/Dockerfile`) so every later task runs in the project's real toolchain instead of re-deriving it each time.

Inspect the repo **READ ONLY** — read files and run read-only inspection commands (`ls`, `cat`, `grep`, `git log`, `find`, reading config/manifest/lockfiles, etc.). Do NOT modify, create, or delete ANY file EXCEPT the onboarding artifacts described below — the three documentation files, plus `.devcontainer/Dockerfile` when the repo has none; in particular do not change any source, build, or config file.

## The three documents

Each doc lives in the repository root and has a specific scope. Do not blur them: cross-linking is fine, duplication is not.

### 1. `AGENTS.md` — THIN, BOUNDED pointer for agents

One page, no more. Its whole job is to point, not to narrate:

- **What the repo is** — one line.
- **Commands** — the exact build, run, and test commands, and call out the single command that is the verification gate (what proves a change is good).
- **Layout pointers** — the few directories that matter, one line each.
- **Links out** — `ARCHITECTURE.md` for design, `.agent/skills/` for repeatable project notes, `specs/` for feature knowledge (when present).

Write the content you own inside a marker pair:

```
<!-- devclaw:managed:start -->
…the pointer content above…
<!-- devclaw:managed:end -->
```

A re-onboard REPLACES the content between the markers and preserves everything outside them — never append a second block. Do NOT put learnings, feature notes, decision rationale, or component narrative here; detailed layout and design narrative belongs in ARCHITECTURE.md.

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
- **Notable design decisions** — for each: what was decided, why, and what was rejected. Cross-link the feature's `specs/NNN-*/` artifacts for the full rationale when the repo has them — the spec is the decision memory; do NOT create a separate ADR log.

Placeholder for diagrams is acceptable if you can't draw one directly — write `<!-- diagram: <what should go here> -->` and describe it in prose.

## The dev-environment boilerplate — `.devcontainer/Dockerfile`

Every future task for this project runs inside a container built from this file, and a human developing the project can use the SAME file — one environment, shared by human and agent. Give the project a real, reproducible DEV environment:

- **Create it ONLY when the repo has none.** If `.devcontainer/Dockerfile` already exists (a human's, or a prior onboarding's), LEAVE IT UNCHANGED — it is the source of truth. Same non-clobber discipline as the docs.
- **It is a DEV image, NOT a deploy image.** Base it on the official **SDK** image for the stack you identified in AGENTS.md — e.g. `mcr.microsoft.com/dotnet/sdk:<major.minor>`, `node:<major>`, `golang:<major.minor>`, `rust:<major>` — which ship the compiler/SDK, a shell, and the package managers. Do NOT write a slim multi-stage *production* image that strips the SDK; that cannot build or test.
- **Cover every toolchain the build/test commands need.** If the project builds with more than one (a .NET backend + a Node frontend, say), install all of them so the full verify gate — `dotnet test` AND `npm test` — runs in this single container.
- **Debian/Ubuntu-based.** All the official SDK images are; devclaw layers its own agent harness on top of yours, and that layer assumes a debian base.
- **Toolchain ONLY.** Do NOT `COPY` the application in (the workspace is bind-mounted at run time), and do NOT install devclaw/agent tooling (claude, the runner) — devclaw adds that layer itself. Keep it exactly the environment a human developer would want, nothing more.
- Put a one-line `#` comment at the very top marking it a DRAFT generated by devclaw onboarding for human review.

If you cannot confidently determine the base image for the stack, still write your best-effort Dockerfile and note the uncertainty in that top comment — a human reviews it, and a wrong toolchain fails the build LOUD rather than degrading silently.

## Rules across all three docs

**Draft marker:** for any doc you CREATE (not update), put a one-line note at the very top marking it as a DRAFT generated by devclaw onboarding for human review. On subsequent updates the marker stays until a human removes it.

**Do not clobber:** if a doc ALREADY exists AND is substantive, do NOT blindly overwrite it — validate each part against the actual repository, KEEP everything still accurate, and only correct or fill in what is wrong, stale, or missing (preserving the existing structure). For AGENTS.md, the devclaw-owned content is what sits between the `devclaw:managed` markers; everything outside them is human-owned and preserved.

**Boundary discipline:** each doc has one job. Don't put ADR-style reasoning in README; don't put quickstart commands in ARCHITECTURE; don't put design narrative or decision rationale in AGENTS.md. Cross-link instead.

**Read-only otherwise:** everything else in the repo is read-only during this task.

**`devclaw.json` is human-owned:** the per-project manifest at the repo root
(schema version, boilerplate revision, per-project settings) is authored and
edited by humans through PRs. Never create, edit, or delete it — devclaw's
host side seeds it mechanically on the install PR; your job here is the three
docs plus the dev container only.

## Summary

End with a short summary to STDOUT in your final message: for each of the three docs, whether you CREATED, UPDATED, or LEFT UNCHANGED, plus the two or three most load-bearing facts you captured (per doc). Then one line for `.devcontainer/Dockerfile`: CREATED (name the base image + toolchains) or LEFT UNCHANGED (a dev container already existed).
