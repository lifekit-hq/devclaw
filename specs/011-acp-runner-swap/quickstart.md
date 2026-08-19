# Quickstart: validating the ACP-direct runner (011)

Ordered cheapest-first; each rung assumes the previous passed.

## 1. Stubbed suite (no docker, no claude)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q          # full suite
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_acp_client.py tests/test_runner_acp.py tests/test_runner_usage.py
```

Expected: green at or above the current baseline; the named regressions
(workspace vendor-neutrality, fake-agent drive path, usage declared-absent)
all present. In a worktree, verify the import path FIRST per
`.claude/rules/testing.md`.

## 2. Fake-agent smoke through the real runner process

The fake agent (`tests/acp_fake_agent.py`) is runnable as a program, so the
seam can be proven end-to-end without claude (SC-006):

```bash
DEVCLAW_ACP_COMMAND=".venv/bin/python tests/acp_fake_agent.py --script ok" \
  .venv/bin/python openhands-runner/runner.py <<'EOF'
{"kind": "feature", "goal": "smoke", "workspace_dir": "/tmp/acp-smoke", "task_id": "t-smoke"}
EOF
```

Expected: `event:` lines then one `result:` line with `status: "ok"` and the
fake's final message as `agent_output`. Zero runner-code modification for a
different agent = US2 proven.

## 3. Image build check (SC-004)

```bash
docker build -f .sandcastle/Dockerfile -t devclaw-sandbox:acp .
docker run --rm --entrypoint pip devclaw-sandbox:acp list | grep -i openhands ; test $? -eq 1
```

Expected: build succeeds; grep finds nothing.

## 4. Live L1 (logged-in claude + docker)

Follow `docs/runbooks/live-shakedown.md` L1 (single dispatch_task). Extra
check for D5: set a model override on the task and confirm the agent ran on
it (transcript header / `claude` model line). Then L2–L3 and L5 (abort —
exercises teardown escalation).

## 5. The #538 scenario replay (SC-001)

Re-run the spec-008 Tier-B proof shape on a scratch repo: intake → ready →
goal → in-sandbox speckit → one-slice PR → done-gate close. Compare the
result payloads field-for-field against a pre-swap run — the diff must be
empty at the contract level (values like timestamps/SHAs differ, fields and
statuses may not).

## Rollback

The swap is one PR touching only the worker layer: revert the PR, rebuild
the image. No host state migrates, so rollback is mechanical.
