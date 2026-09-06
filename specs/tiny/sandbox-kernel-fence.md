# TinySpec: the sandbox's kernel-side fence — pids-limit, cap-drop ALL, no-new-privileges

**Issue**: none (2026-09-06 engineering audit, "close the two fail-open seams"; eng-health `sandbox_hardening_missing`)
**Branch**: harden/sandbox-kernel-fence
**Date**: 2026-09-06
**Status**: implemented — awaiting the live shakedown on the box (Denys's button)
**Complexity**: small

## What

`engine/sandcastle.py`'s `docker run` carried a memory and CPU ceiling and ran
the worker as `agent`, and nothing else. Three flags close what a non-root
process inside can still reach, all in constitution IX's **safety** domain:

| flag | closes |
|---|---|
| `--pids-limit 4096` | a fork bomb or runaway test-worker pool — the memory ceiling bounds bytes, not process count |
| `--cap-drop ALL` | the capability bounding set — no file capability or setuid binary can hand a cap back |
| `--security-opt no-new-privileges` | setuid/setgid escalation outright |

Kept on purpose: `--network host` (claude's OAuth refresh needs egress; an
allowlist is a later, separate change) and a writable root (mise and npm
provision under `/home/agent` and `/tmp`; `--read-only` is not in this spec).
The eng-health metric keeps `--read-only` on its list, so
`sandbox_hardening_missing` goes 4 → 1, not 0 — accepted, the flag is a
deliberate non-goal until provisioning targets a tmpfs.

## Why nothing breaks

- Toolchain provisioning is unprivileged (mise under `/home/agent`); no skill
  or hook calls `sudo`/`apt-get` at task time (image build does, as root, at
  build time).
- `oom-shield.sh` and the runner raise their own `oom_score_adj` — an
  unprivileged write.
- Playwright launches Chromium with its setuid sandbox OFF by default
  (`chromiumSandbox: false`), so browser E2E does not need new privileges.
- The devclaw-mcp container itself already runs `no-new-privileges:true`
  (`deploy/docker-compose.devclaw.yml`); this brings the per-task sandbox to
  the same posture.

## Context

| File | Role |
|------|------|
| `devclaw/engine/sandcastle.py` | `SANDBOX_PIDS_LIMIT` constant; the three flags in `_build_docker_args` |
| `tests/test_sandbox_isolation.py` | `test_docker_args_posture` extended (sandbox-fence tripwire class — extends, never a sibling) |
| `docs/flows/task-execution.md`, `docs/architecture.md`, `docs/INDEX.md` | the `docker run` trace + the layer-4 box contract name the fence |

## Requirements

1. Every sandbox `docker run` carries the three flags; the posture test pins
   them and pins that the network is still `host` and `--read-only` absent.
2. `--pids-limit` is a constant, not a `DEVCLAW_*` dial: nothing has needed
   to move it, and a dial costs a compose-forwarding line, a doc row and a
   doctor surface (tinyspec `sandbox-dials-not-plumbed`). Rejected: a
   per-project override beside `sandbox_memory` — no project has asked.
3. No image rebuild is needed — the flags are on the host's `docker run`, so
   the change goes live with the next devclaw-mcp deploy.
4. Live proof (Denys's button): after deploy, one L1 shakedown task on the
   box (`/live-shakedown`), then one real browser-E2E-bearing task
   (finance-sentry's `verifyCmd` runs Playwright) — the flags are proven by
   the real pipeline, never by the stubbed suite.

## Plan

1. Constant + three flags with the reasoning in the launcher comment.
2. Extend the posture test.
3. Docs: the trace diagram, the layer-4 box paragraph, INDEX tags.
4. Suite, ruff, mypy; PR. Deploy + shakedown on Denys's button.

## Tasks

- [x] Write tinyspec
- [x] `SANDBOX_PIDS_LIMIT` + `--pids-limit` / `--cap-drop ALL` / `no-new-privileges`
- [x] `test_docker_args_posture` pins the fence and the two deliberate non-goals
- [x] Docs + INDEX
- [x] Full suite + `ruff check .` + `mypy` green
- [ ] Deploy devclaw-mcp; L1 shakedown + one Playwright-bearing task green on the box (Denys)

## Done When

- [x] All code tasks checked off, suite green
- [ ] Live: a sandboxed task with browser E2E completes under the fence (no `EPERM`/`clone` failures in the worker log)
- [ ] eng-health next run: `sandbox_hardening_missing` = `["--read-only"]`
