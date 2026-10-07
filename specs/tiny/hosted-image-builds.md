# TinySpec: build devclaw's images on hosted arm64; the box pulls by SHA

**Branch**: fm/dc-hosted-image-builds
**Date**: 2026-10-07
**Status**: done
**Complexity**: small
**Issue**: VPS compute audit 2026-10-06, measure 2 / limit L3 (no image builds on the box)

## North-star case

- **Failure moved**: *stopped when it shouldn't*. On 2026-10-06 12:38–12:51
  the deploy job's two on-box `docker build`s took part in a global OOM
  (load1 207). The deploy failed after 13.2 min and a worker browser was
  killed. CI shared the same runner and queued behind deploys at a p90 of 10.9 min.
- **Number that shows it**: devclaw's self-hosted job-minutes per week
  (104 in the audit: CI plus image build) and its queue-wait p90. With this
  change the box runs only the arm call and a pull-and-recreate, which takes
  about as long as finance-sentry's 1.4 min p50 deploy.
- **Cut when**: the box stops being the deploy target, or GitHub-hosted arm64
  runners stop being free for this public repo.

## What

This copies finance-sentry's 2026-10-01 pattern. `docker-build.yml` builds
`devclaw-mcp` and `devclaw-sandbox` on `ubuntu-24.04-arm` and pushes
`ghcr.io/lifekit-hq/<image>:<sha>`. It also moves `:latest`, but only while
that SHA is still the head of main. `deploy.yml` arms the self-deploy on that
workflow's success (`workflow_run`) instead of on the push. Its dispatch lane
waits for both images on a hosted runner, then the self-hosted job pulls those
exact tags and runs `deploy-devclaw(-auto).sh`, which is unchanged. CI (tests,
lint, frontend, gitleaks) moves to hosted runners. Services, ports, volumes,
the compose project and container names do not change.

## Context

| File | Role |
|---|---|
| `.github/workflows/docker-build.yml` | Build on PRs (no push), publish on push to main |
| `.github/actions/build-image/action.yml` | One image: buildx, the `:buildcache` registry cache, push by SHA, move `:latest` |
| `.github/workflows/deploy.yml` | `arm` after a published build; `images` (hosted wait); `deploy` (self-hosted, pull only) |
| `.github/workflows/ci.yml` | Hosted runners; Python 3.13 matches the image |
| `devclaw/goal/self_deploy.py` | Unchanged: it dispatches `deploy.yml -f auto=true` (blank tag = main's head) |

## Requirements

- No self-hosted job runs `docker build`.
- The box pulls with the deploy job's own `GITHUB_TOKEN` (`packages: read`),
  the login it already used. There is no new secret, token or host change.
- The console stage's `npm_token` is the job's `GITHUB_TOKEN`
  (`packages: read`), the same credential the frontend CI job uses for `npm ci`.
- A dispatch whose commit has no images yet waits for its Docker Build run. The
  reconcile path can fire before the build ends. The dispatch fails before
  touching the box if that run failed, if no push run exists, or after 45 min.
  A named tag (a rollback) must already exist.

## Rejected alternatives

- **Keep the arm on `push`**: the instance would fire a deploy as soon as it
  is quiescent, which is often before the hosted build ends, and the wait
  would then always be paid. Arming on the published build makes the wait the
  exception: only the reconcile path hits it.
- **Wait on the self-hosted runner**: that holds the box's one devclaw runner
  for the whole build. The wait job is hosted for that reason.
- **Trivy gate as in finance-sentry**: a new failure mode on the first
  post-merge deploy. It is out of scope and named as a follow-up.

## Tasks

- [x] `docker-build.yml` + `build-image` composite action
- [x] `deploy.yml`: `workflow_run` arm, hosted `images` wait, pull-only `deploy`
- [x] `ci.yml` on hosted runners
- [x] Runbook, compose header, `deploy-devclaw.sh` header, INDEX tag

## Done When

- [x] Suite green with no global git identity, as on a hosted runner; ruff,
  mypy, lint-imports, yamllint and actionlint clean
- [ ] This PR's `Docker Build` builds both images on `ubuntu-24.04-arm`
- [ ] Live: the first post-merge self-deploy pulls `:<merge sha>` and `/health` reports it
