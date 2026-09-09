# TinySpec: a credential gap names its own fix, at the hop that can still act on it

**Branch**: `fix/credentials-say-the-exact-fix`
**Date**: 2026-09-09
**Status**: IMPLEMENTED 2026-09-09
**Complexity**: small

## North-star case

- **Failure moved**: ran but needed the owner. A missing or mis-scoped credential
  reaches the owner as a dead goal days later, and he then has to work out which
  credential, which scope, and which command.
- **Number that shows it**: owner `resume` verbs spent on credential gaps —
  **5 of the 12 resumes in the 14 days to 2026-09-09** were the same missing
  `NODE_AUTH_TOKEN` reaching five goals (fs-553, fs-554, fs-557, issue-493,
  scanner-broad-universe) as a `mechanical:env` hold. A resume is not a decision;
  the 2026-09-07 ruling says the owner should never have to press one.
- **Cut when**: a credential gap has not reached a goal for a month. Then the
  check is guarding nothing and can go.

## Root cause

Three mechanisms already sat at this boundary — `boot_guard` (refuses to start),
`deploy-devclaw.sh` `_resolve_secret` (dies before touching the box), and doctor
(presence/shape/liveness on the running instance). None of them told the owner
**what to type**, and none of them looked at the hop where the gap actually
lives: the repository Actions secrets the deploy resolves from.

So the failure path was: secret missing → deploy dies, or instance runs on a
stale value → a worker burns a session on a 401 → `mechanical:env` hold → owner
diagnoses it by hand. The remedy doctor did print was "redeploy through the
Deploy workflow", which is exactly what cannot work when an Actions secret is
absent: the deploy dies at `_resolve_secret` and the owner loops.

Underneath that, a second defect made the situation self-inflicting. Doctor's
`instance.delivery.token` asserted the scope **`actions:read`**. That is a
FINE-GRAINED PAT permission name, never a classic-PAT scope — a classic or
OAuth token never states it. So the check's OK branch was **unreachable for
exactly the token its own remedy told the operator to issue**, and the test
hid it by fabricating a scope set (`("repo", "actions:read")`) GitHub does not
return. Verified against the live box on 2026-09-09: a token with
`gist, read:org, repo, workflow` returns HTTP 200 on `/actions/runs/{id}/jobs`.
`repo` is what grants a classic token the Actions API.

## Requirements

1. Every credential finding carries the EXACT provisioning command, rendered from
   the registry (`devclaw/credentials.py`) — the var and its least privilege are
   already declared there and must not be restated in the check.
2. Doctor checks the hop upstream of the box: every required credential exists as
   an Actions secret on `DEVCLAW_SELF_REPO`. Registry-driven, so a credential
   added later is covered without touching the check.
3. Reading secret NAMES needs the delivery token, so an absent or unreadable one
   degrades to UNKNOWN — never a false FAIL that sends the owner chasing a
   credential that is already set.
4. `instance.delivery.token` asserts `repo`, not `actions:read`, and every doc,
   script and prompt repeating the `actions:read` claim is corrected in the same
   change.
5. No fourth surface. There are already three mechanisms at this boundary; this
   extends doctor rather than adding a `devclaw credentials` command.

## Rejected alternatives

- **A new `devclaw credentials` subcommand** (the shape first proposed). Rejected
  on the standing "two or more mechanisms at a boundary means REPLACE, never add
  a third" ruling — it would have been the fourth thing that knows about
  credentials, and doctor is already the place an owner looks.
- **Shell out to `gh secret list`.** `doctor/checks_instance.py` declares "no
  cognition call, no subprocess, no write, ever". The probe is urllib, the same
  contract as `probe_github_scopes`.
- **Relax the scope check to a warning.** The check is not too strict, it was
  asserting the wrong name. Fixing the name keeps it fail-loud.
- **Merge `NODE_AUTH_TOKEN` and `GH_TOKEN` into one credential.** Fewer secrets
  to set, but `NODE_AUTH_TOKEN` crosses into the sandbox and `GH_TOKEN` must
  never (`sandbox=False, agent=False`): merging them hands the worker agent a
  push-and-merge-capable token. Ruled by Denys 2026-09-09 — keep the fence.

## Plan / Tasks

- [x] `env_cap.probe_repo_secret_names` — names only, never a value, never raises
- [x] `_provision_remedy(var)` in doctor, rendered from the registry; the generic
      `_SECRETS_REMEDY` and its orphaned import removed with it
- [x] `instance.credentials.secrets` check + seeded faults (missing secret ⇒ FAIL
      with the exact command; unreadable ⇒ UNKNOWN, never a false FAIL)
- [x] `instance.delivery.token` asserts `repo`; the fabricated-scope test case
      replaced with the scope set a real `gh auth login` token carries
- [x] `actions:read` claim corrected in `credentials.py`, `deploy-devclaw.sh`,
      `README.md`, `docs/reference/env-vars.md` and the sibling tinyspec

## Done-When

A missing credential is a doctor FAIL naming the credential, its least privilege,
and the `gh secret set …` line — before it reaches a goal. `instance.delivery.token`
returns OK for a real classic/OAuth token. Suite, ruff, mypy, lint-imports green.
