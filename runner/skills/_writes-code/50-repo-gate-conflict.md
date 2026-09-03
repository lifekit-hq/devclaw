# When you cannot finish: the two typed blocks

Two things can stop you that no amount of trying fixes. Each has ONE correct
move — a typed hand-back — and one forbidden move: changing what the gate
reads so the gate passes.

**Your environment lacks something the work needs** (a tool, a service the
tests need, a credential, network or registry access). End your final
message with

    BLOCKED: env — <exactly what is missing, one line>

devclaw holds the whole project on that fact, files it as devclaw work, and
resumes every goal on the project when the environment changes. Still fill
CHANGED / VERIFIED / ACCEPTANCE with how far you got.

**A repo mechanism contradicts the ticket** (a pre-commit hook, lint
autofix, version-bump gate or CI check forces a change the ticket forbids).
End with

    BLOCKED: <the mechanism, the change it forces, the ticket line it contradicts>

The owner resolves it with a recorded decision; never appease the mechanism,
never sneak past it.

## What is never yours to change

Gate inputs — CI workflows, AGENTS.md, test-runner and build configuration
(Playwright, Jest, Karma, Angular, pytest, tox), pre-commit/husky hooks,
install and postinstall scripts, `.npmrc`, toolchain pins (`global.json`,
`.tool-versions`, `.mise.toml`) — and any binary file. A span that touches
one fails before review; skipping a hook, weakening a check or writing a
workaround into AGENTS.md are not options. The one exception is a ticket
that is ABOUT those files: it names the path in scope and the classifier
honours it. A new verify layer is declared in `devclaw.json` (`verifyCmd`),
never in a workflow you edit.
