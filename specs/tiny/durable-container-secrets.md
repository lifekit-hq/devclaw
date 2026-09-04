# TinySpec: Durable container secrets — one home, loud on absence

**Branch**: `feat/033-durable-container-secrets`
**Date**: 2026-09-04
**Status**: implemented
**Complexity**: small (bug fix — ruled by Denys 2026-09-04: "this is a bug
fix, not a feature"; all three recommended options applied)

## What

Make container creation and deployment bulletproof for the two credentials
the instance cannot run without — `CLAUDE_CODE_OAUTH_TOKEN` (the `claude
setup-token` subscription credential: host cognition + sandbox auth) and
`NODE_AUTH_TOKEN` (`read:packages`, in-sandbox `npm ci`). They get **exactly
one durable home on the VPS**, declared by the compose file and written by the
deploy from the repository's Actions secrets, so every path that creates or
recreates the container (the workflow's manual and auto lanes, a rollback, a
hand `docker compose up` from the box) yields the identical container or
fails loudly. Both are **required**: a missing or blank value fails the deploy
before the container is touched, fails a hand `up`, and makes the container
refuse to start — it never answers healthy without them.

## Context

2026-09-03 (UTC): 12:49 the workflow deployed `08ecef3` with both secrets
present in the deploy step's environment (the deploy log says so). 12:58:55
`/srv/devclaw/.env` was edited on the box; 12:58:56 the container was
recreated from the box with only the env file. Compose resolved
`${NODE_AUTH_TOKEN:-}` and `${CLAUDE_CODE_OAUTH_TOKEN:-}` to blank and, since
the config "changed", replaced the container. It answered `/health`; doctor
reported 0 fail. 14:22–17:01 a finance-sentry worker hit `npm ci` → 401,
reported the gap, and a project-wide `mechanical:env` hold parked six goals;
the nightly cycle settled zero runs. The GitHub secret (set 2026-08-31) was
intact the whole time; all twelve deploys since had succeeded. Nothing was
flaky: the container's contents depended on which path ran `up`, and one
path was silently wrong.

Root, not symptom: (1) the secrets' only home was the workflow step's process
environment ("nothing written to disk on the VPS" — #645,
`specs/tiny/sandbox-registry-read-token.md`), so the container's real config
was spread over three sources, one of which exists for ~60 s per deploy; (2)
missing required config defaulted to blank at every layer (`${VAR:-}`,
doctor "unset ⇒ OK", a service that starts without its credential). The
"never on disk" premise was never true for this box — it already mounts the
interactive Claude login, the gh config and the gitconfig from disk. A vault
rule "never hand-recreate" existed and was broken the same day it was
written; a rule is not a mechanism.

| File | Role |
|------|------|
| `deploy/docker-compose.devclaw.yml` | Modified — `env_file:` declares the one home (`${DEVCLAW_SECRETS_FILE:-/srv/devclaw/secrets.env}`); the two `${VAR:-}` lines leave the `environment:` block (they would override the file with blank) |
| `deploy/deploy-devclaw.sh` | Modified — the credential preflight becomes the file's only writer: env (workflow) else existing file (hand run) else `die`; unset Actions secret under the workflow ⇒ `die` before touching the box; shape check before write; absent/unwritable/not-0600 file ⇒ `die` naming the one-time provisioning |
| `.github/workflows/deploy.yml` | Modified — comments state the new contract (secrets still ride in from the repo secrets as the source of truth) |
| `devclaw/boot_guard.py` | New — the production engine refuses to start (non-zero, names only) without both credentials; dev/test engines need neither |
| `devclaw/server/lifecycle.py` | Modified — `assert_required_env()` first thing in `main()`, before crash recovery and before either transport serves |
| `devclaw/doctor/checks_instance.py` | Modified — `instance.auth.setup_token` and `instance.registry.token` FAIL on absence in production (agreeing with the boot rule); the "unset is a supported posture unless a project declares the capability" branch is gone |
| `tests/test_boot_guard.py` | New — fail-closed tripwire: refuses on unset/blank, names the var and the fix, never echoes a value; dev engines exempt; structural guard that the entrypoint runs it first |
| `tests/test_doctor.py` | Modified — fixture pins the stub engine; seeded faults for both checks in production vs dev |
| `tests/test_env_vars_doc_sync.py` | Modified — docstring: the only `env_file` is the secrets file, which carries no `DEVCLAW_*` dial |
| `deploy/.env.example`, `docs/reference/env-vars.md`, `docs/runbooks/devclaw-self-deploy.md`, `docs/INDEX.md` | Modified — the "never on disk" contract is retired; §1 gains the one-time provisioning; currency tags |

## Requirements

1. The compose file declares the secrets file as an `env_file` so no
   invocation of the compose command can omit it; a missing file fails `up`.
2. The two credentials are NOT interpolated in the `environment:` block
   (an `environment:` entry overrides `env_file`, and `${VAR:-}` resolves to
   blank without a shell environment).
3. `deploy-devclaw.sh` is the file's only writer. Under the workflow
   (`GITHUB_ACTIONS=true`) each value must come from the environment — an
   unset/blank Actions secret dies BEFORE the file or the container is
   touched, leaving the previous value intact. On a hand run a value absent
   from the environment is read back from the file; absent from both ⇒ die.
4. Set-but-malformed `NODE_AUTH_TOKEN` stays fatal (the 2026-08-31 class),
   checked against the value about to be written.
5. The file must pre-exist, be writable by the deploy user and be mode 0600
   (`/srv/devclaw` is root-owned; the deploy writes through the inode); any
   other state dies naming the one-time provisioning command.
6. The container refuses to start when `DEVCLAW_ENGINE` is unset (production)
   and either credential is unset or blank — `SystemExit` with a message
   naming the variable(s) and the fix, never a value — before crash recovery
   or any transport serves. `host`/`stub` need neither.
7. Doctor agrees with the boot rule: in production, absence of either
   credential is FAIL with the same remedy; under a dev/test engine it is OK
   with the reason named. Set-but-malformed / rejected / unreachable keep
   their verdicts.
8. No stage — deploy output, container log, doctor finding, error — echoes
   a credential value.
9. Docs honest: env-var rows, the compose/workflow/env-example comments and
   the runbook state the one-home contract and retire "nothing written to
   disk"; INDEX currency tags bumped.

## Rejected alternatives

- **Detect an out-of-band recreate** (a deploy marker doctor checks): a
  detector, not a fix — rejected by Denys 2026-09-04.
- **Keep the transient home, enforce "workflow only" by rule**: the rule
  existed in the vault and was broken the same day.
- **A second `--env-file` flag on the compose command**: the declaration
  would live in the command, which a hand run can forget.
- **Lines inside the hand-managed `.env`**: a machine writer and a human
  editor on one file clobber each other (a hand edit at 12:58:55 preceded
  this incident). Dedicated 0600 file — Denys, "all recommended".
- **Registry token conditional per spec 030** (OK unless a project declares
  the capability): finance-sentry declares nothing and burned the session.
  Required instance-wide — Denys, "all recommended". The spec-030 probe for
  a declared capability keeps its semantics (unset was already red there).
- **OAuth token recommended-only with the mounted-login fallback**: that
  fallback is the 2026-08-22 overnight-outage class. Required — Denys.

## Plan

1. Compose: `env_file` declaration; drop the two interpolated lines.
2. Deploy script: replace the two preflight blocks with the one-home writer.
3. `boot_guard.py` + the `main()` call; doctor semantics.
4. Tests (fail-closed + doctor seeded-faults); docs; tinyspec.
5. Live: provision `/srv/devclaw/secrets.env` (0600, deploy user) once, deploy
   through the workflow, `doctor` shows both credentials present, the six
   env-held finance-sentry goals heal on the new SHA.

## Tasks

- [x] `env_file` declaration in `docker-compose.devclaw.yml`; credentials out of `environment:`
- [x] `deploy-devclaw.sh`: one-home writer, workflow-vs-hand resolution, all `die` paths
- [x] `deploy.yml` comment contract
- [x] `devclaw/boot_guard.py` + `lifecycle.main()` call
- [x] doctor: both credential checks FAIL on absence in production
- [x] `tests/test_boot_guard.py`; `tests/test_doctor.py` seeded faults
- [x] `.env.example`, `env-vars.md`, self-deploy runbook §1, `INDEX.md`
- [ ] **Denys / live**: `sudo install -m 0600 -o lifekit -g lifekit /dev/null /srv/devclaw/secrets.env`, then deploy through the workflow

## Done When

- [x] All code/doc tasks checked off; full suite + `ruff check .` + `mypy` green
- [ ] A hand `docker compose up` from the box after a workflow deploy yields a container with both credentials present (fingerprints identical to the workflow's)
- [ ] `doctor` on the live instance reports `instance.auth.setup_token` and `instance.registry.token` OK with the credentials present
- [ ] The six `mechanical:env`-held finance-sentry goals resume on the new SHA without a burned session
- [x] No credential value appears in any deploy output, finding, test fixture or error
