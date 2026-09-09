# TinySpec: the GitHub credential joins the registry, and its scope is checked

**Branch**: `fix/github-credential-in-the-registry`
**Date**: 2026-09-09
**Status**: IMPLEMENTED 2026-09-09 — reviewed and admitted by Denys; the two open tasks below need the deployed instance
**Complexity**: small

## North-star case

- **Failure moved**: ran and produced garbage. A red CI verdict reaches the worker with no log, because the host's `gh` may lack `actions:read` and the read degrades to a `log unavailable` note. The worker then edits the CI definition to make red go away, the always-hard `change_class` gate fails the task, and the dispatch budget burns.
- **Number that shows it**: `change_class` gate-input-edit wedges per cycle. Present in 3 of the last 3 non-clean cycles (2026-09-06, 09-07, 09-08). Secondary: `mechanical:dispatch_cap` idle, 29,205s over 14 days, 68% of all devclaw-caused idle.
- **Cut when**: a worker edits a gate input on a branch whose correction demonstrably carried the failing job's log. Then the missing fact was never the cause and the class is instruction, not environment.

## Root cause

`devclaw/credentials.py` (spec 042 US1) registers exactly two credentials: `CLAUDE_CODE_OAUTH_TOKEN` and `NODE_AUTH_TOKEN`. The GitHub credential is not one of them, and it is the one devclaw leans on hardest. It authenticates delivery (push, PR, merge), intake, the issue doorway, the self-issue filer, and `remote_checks.failed_job_log_tail`, the read that feeds a red-CI correction.

It reaches the container as a bind mount of a human's home directory:

```yaml
- ${LIFEKIT_GH_CONFIG:-/home/lifekit/.config/gh}:/home/node/.config/gh
```

Nothing declares it, so nothing checks it. The AST guard skips it because it is not in `_NAMES`. The boot guard skips it because `required_vars()` never names it. Doctor has no presence, shape or scope check for it. Its scope is whatever a human last logged in with, so whether `gh run view --log-failed` can read a job log is a coin flip that fails quietly into `rc.log_note`.

This is the exact shape the boot guard's own docstring names as the failure it exists to prevent, applied to the credential spec 042 forgot: *"cognition rode the revocable mounted login... absence was supported at every layer, so a wrong container was indistinguishable from a right one."*

Evidence it is live, not theoretical:

| Fact | Where |
|---|---|
| Worker edited 5 workflow files, task failed on `change_class` | fs-431, task `fd4a7ac9`, 2026-09-08 20:16 |
| Owner pasted the failing job log by hand into a `correct_implementation` | fs-431, `dec_0926cd8c32344aed8fa7`, 2026-09-08 |
| Worker reported no token with `actions:read`, 10 terminal blocks | machine issue #870 |
| `red-ci-log-to-worker` live-proof task never checked off | `specs/tiny/red-ci-log-to-worker.md`, last task |

Spec 042 US1's cut condition reads: *"the day a third credential is added and its author has to touch more than the registry entry and the deploy secret."* This tinyspec is that test. Verified on the source: the four existing `GITHUB_TOKEN` mentions in `devclaw/` are all docstrings, and the AST guard exempts docstrings, so the registration really is one entry plus the deploy plumbing.

## Requirements

1. The GitHub credential is ONE registry entry in `devclaw/credentials.py`, with `required=True`, `sandbox=False`, `agent=False`. It stays host-side. The sandbox fence carries no GitHub credential, and #867's design is that the host does the privileged read and hands over the log, never the token.
2. Its declared `scope` names `actions:read` alongside the repo scope delivery already needs, so least privilege is stated rather than assumed.
3. The deploy writes it to the one secrets file beside the other two, and the compose reads it from `env_file`, never from `environment:`.
   The bind mount of `~/.config/gh` is **not** removed in this change. `devclaw/delivery/__init__.py:749` is a plain `git push`, which authenticates through the `gh` credential helper the mount carries. `gh auth git-credential` does prefer `GH_TOKEN`/`GITHUB_TOKEN` when set, so the env path should subsume the mount, but "should" is not a thing to test in production on the path that ships every PR. The mount is removed in a follow-up, after a live push proves the env path, and this spec names that follow-up rather than leaving two homes undeclared.
4. Doctor gains an instance check: the credential is present, well-formed against `GH_TOKEN_PREFIXES`, and its live scope set includes `actions:read`. A seeded-fault test covers the missing-scope case. This is the check that would have caught the whole chain.
5. `failed_job_log_tail` keeps degrading gracefully, but a degrade caused by a scope gap is reported loudly through doctor rather than only as a `log_note` inside a steering line.

## Plan

1. Add the registry entry. Confirm `boot_guard.REQUIRED_PRODUCTION_ENV` and the sandbox set derive from it with no edit, which is spec 042's whole claim.
2. Add the secret to `deploy/deploy-devclaw.sh` `_resolve_secret` and to the `printf` that writes `secrets.env`, plus the workflow `env:` block. Leave the `LIFEKIT_GH_CONFIG` volume in place.
3. Add the doctor instance check plus its seeded-fault test (`devclaw/doctor/checks_instance.py`).
4. Update `docs/reference/env-vars.md` and the currency tag in `docs/INDEX.md`.

## Rejected alternatives

- **Put a GitHub token in the sandbox.** The fence carries no GitHub credential by design, and #867 already ruled the host does the read. Rejected on constitution grounds, not convenience.
- **Keep the bind mount and only add a doctor check.** Two homes for one credential is the smell spec 042 exists to delete. A check over an unmanaged mount tells you it broke, not why, and cannot be fixed by a redeploy.
- **A brake that fails a task when the log is unreadable.** That is a third mechanism at a boundary that already has the gate, the skill line and the correction renderer. Constitution IX puts a fact before a brake, and this gap is a fact.

## Tasks

- [x] Registry entry + confirm no second edit is needed — the 042 cut-condition holds: `boot_guard.REQUIRED_PRODUCTION_ENV` and `sandcastle`'s sandbox/agent sets derive from `required_vars()`/`sandbox_vars()`/`agent_vars()` and needed no edit. The credential is spelled `GH_TOKEN`, not `GITHUB_TOKEN`: GitHub refuses an Actions secret with a `GITHUB_` prefix, and `gh` ranks `GH_TOKEN` higher anyway
- [x] Deploy script (`_resolve_secret` + shape check + the one `printf` into the home), workflow `env:`, compose comment — mount stays
- [x] Doctor `instance.delivery.token` (presence, shape, liveness, `actions:read`) + `env_cap.probe_github_scopes` + seeded faults; a fine-grained/app token states no scopes ⇒ UNKNOWN, never a false red
- [x] Docs + INDEX currency tag (README's prerequisite line too)
- [ ] Live proof after deploy: one red rollup whose next brief carries the job tail, closing `red-ci-log-to-worker`'s open task
- [ ] Follow-up, owner Denys, by 2026-09-23: once a live push has run on the env path, remove the `LIFEKIT_GH_CONFIG` mount so the credential has one home

## Done-When

`gh run view --log-failed` succeeds from the container on a red rollup, the resulting correction carries the tail, and doctor reports the credential present with `actions:read`. Machine issue #870 closes. No new `change_class` gate-input wedge appears for two subsequent cycles. `ruff check .`, `mypy`, `lint-imports` and the suite green.
