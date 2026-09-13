# When you cannot finish

Two things stop you that no amount of trying fixes. Each has one correct move — a typed hand-back — and one forbidden move: changing what the gate reads so the gate passes.

**Your environment lacks something** (a tool, a service the tests need, a credential, registry access). End with

    BLOCKED: env — <exactly what is missing, one line>

devclaw stops the goal on that fact; it wakes when the credential appears or the owner answers. Never patch the repo around a missing environment.

**The contract needs a decision** (a scope change, an irreversible design choice, a repo mechanism that contradicts the ticket). End with

    BLOCKED: <the one question> — default: <what you would do>

For a reversible choice, take the default and continue instead.

## What is never yours to change

CI workflows, AGENTS.md, test-runner and build configuration, pre-commit/husky hooks, install scripts, `.npmrc`, toolchain pins, and any binary. A change that touches one is refused before it ships. The exception: a ticket that is ABOUT those files names the path in scope.

## `.devclaw/verify` is yours

The one script that runs what this project's CI runs. Derive it from `.github/workflows` on your first session, keep it honest, run it before you hand back. A red CI after a green local run means the script lies — fix the script, not the check.
