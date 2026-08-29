# DevClaw — live shakedown runbook

Everything in the test suite runs against **stubs** (no `claude`, no docker). This
runbook exercises the real pipeline against the **actual engine**: a logged-in
the runner driving `claude` (via claude-agent-acp) inside a real docker sandbox. Work top-to-bottom — each
layer builds on the last, so a failure tells you exactly which seam broke.

> **Cost note.** Every real run spends your Claude Pro/Max session (no API key —
> that's the design). Keep the shakedown goals *tiny*. A full goal build (L4) can
> run for a long time; do L1–L3 first.

---

## 0. Prerequisites

| Need | Check | Fix |
|---|---|---|
| Docker running, socket reachable | `docker info` exits 0 | start docker / add user to `docker` group |
| A logged-in `claude` CLI | `claude --version` and a non-empty `~/.claude` | `claude` then log in (Pro/Max OAuth) |
| Python ≥ 3.11 | `python3 --version` | — |
| `git` | `git --version` | — |

**No `ANTHROPIC_API_KEY` in the environment** — DevClaw refuses it on purpose. Verify:

```bash
echo "${ANTHROPIC_API_KEY:-<unset, good>}"   # must print <unset, good>
```

---

## 1. Build the host + the sandbox image

```bash
cd <repo root>
python -m venv .venv && source .venv/bin/activate
pip install -e .

# the per-task sandbox image (python3.13 + claude CLI + claude-agent-acp)
docker build -t devclaw-sandbox:latest -f .sandcastle/Dockerfile .
docker image ls devclaw-sandbox:latest   # confirm it exists
```

The image bakes a pinned `claude` CLI + `claude-agent-acp`; the host mounts your
`~/.claude` read-only into it at runtime, so auth flows without an API key.

---

## 2. Start the server (HTTP, so the console works)

```bash
export DEVCLAW_DB=$PWD/.shakedown/devclaw.db        # keep state out of the repo
export DEVCLAW_GOALS_DIR=$PWD/.shakedown/goals      # goal-view files out of ~/memory
export DEVCLAW_TRANSPORT=http DEVCLAW_PORT=8000
devclaw-mcp          # logs to stderr; leave running in this terminal
```

You should see: `devclaw v… ready (http://0.0.0.0:8000/mcp, db=…, recovered=0)`.

In a second terminal:

```bash
curl -s localhost:8000/health      # {"ok":true,"name":"devclaw","version":"…"}
open http://localhost:8000/console     # (or just browse it) — empty for now
```

### A tiny MCP driver

Tools are MCP, not REST. Save this helper and reuse it for every step below
(no `DEVCLAW_TOKEN` set → no auth needed):

```python
# drive.py
import asyncio, json, sys
from fastmcp import Client

async def call(tool, **args):
    async with Client("http://127.0.0.1:8000/mcp") as c:
        res = await c.call_tool(tool, args)
        print(res.content[0].text)

asyncio.run(call(sys.argv[1], **json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")))
```

```bash
python drive.py list_tasks            # [] — confirms the client works
```

---

## 3. L1 — a single real task (smallest end-to-end)

Prove one agent run works in a sandbox before anything fancy.

```bash
mkdir -p /tmp/sc-l1 && cd /tmp/sc-l1 && git init -q && cd -
python drive.py implement_feature \
  '{"workspace_dir":"/tmp/sc-l1","goal":"create a file hello.txt containing the text: hello from devclaw"}'
# → {"task_id":"…","status":"pending"}
```

Watch it (poll, or use the console):

```bash
python drive.py get_status '{"task_id":"<the id>"}'   # pending → running → done
python drive.py get_events '{"task_id":"<the id>"}'   # the live worker event stream
ls /tmp/sc-l1/hello.txt                               # the artifact, on success
```

**This is the make-or-break step.** If it reaches `done` and the file exists, the
whole engine seam (host → docker → runner → ACP agent → claude → back) works.

---

## 4. L2 — a one-shot goal (the advance loop)

```bash
mkdir -p /tmp/sc-l2 && cd /tmp/sc-l2 && git init -q && cd -
python drive.py register_project \
  '{"project_id":"sc-l2","name":"L2 shakedown","workspace_dir":"/tmp/sc-l2"}'
python drive.py create_goal \
  '{"goal_id":"sc-l2-mathx","project_id":"sc-l2","mode":"one_shot","objective":"create a Python package mathx with an add() and a mul() function, each in its own module, plus a tests/ file that imports both","done_when":"mathx exposes add() and mul() in separate modules with a tests/ file importing both"}'
# → {"goal_id":"…","mode":"one_shot",…}   (the start_program alias was retired by spec 022 US3)
```

```bash
python drive.py get_goal '{"goal_id":"<id>"}'         # goals are born executing; watch phase idle → running
python drive.py tail_goal '{"goal_id":"<id>"}'        # the log + deliveries tail
python drive.py list_tasks '{}'                       # each dispatched advance appears as a task
```

The heartbeat dispatches one advance at a time with a mechanical brief (zero
host planning — spec 008); the worker plans in-sandbox with speckit (`specs/*/`
in the workspace). After a settled advance the one-shot goal proposes done —
confirm the goal closes through its grounded done-gate.

---

## 5. L3 — crash recovery (the durability proof)

Start a goal (L2), then **kill the server mid-run** and restart it:

```bash
# while a task is 'running':
#   Ctrl-C the devclaw-mcp terminal   (or: kill <pid>)
#   then restart it with the SAME DEVCLAW_DB:
devclaw-mcp
```

On restart the log shows `recovered=N` and the heartbeat resumes the goal with
**no new submission** — orphaned `running` tasks are reset to `pending` and
re-run. Confirm the goal still closes:

```bash
python drive.py get_goal '{"goal_id":"<id>"}'
```

(In-flight sandbox containers from the dead process: `docker ps` to spot any, they
should self-`--rm`; `docker rm -f` stragglers.)

---

## 6. L4 — goal filed against a ticket (the issue is the contract)

The prose scope-grill porch was removed by the 2026-08-29 prune (spec 024:
the ticket is the contract). To exercise L4, file an issue on the project's
repo (the issue template carries the acceptance criteria and saga sections),
then file the goal against it:

```bash
python drive.py register_project \
  '{"project_id":"sc-l4","name":"L4 shakedown","workspace_dir":"/tmp/sc-l4"}'
python drive.py create_goal \
  '{"goal_id":"jyq","objective":"ship the cli","project_id":"sc-l4","issues":[1]}'
```

The build is now a durable goal — watch it on the console / `get_goal` /
`tail_goal`. It may run a while; that's the point.

---

## 6b. L5 — abort a running build (the kill switch)

Crash recovery (L3) is automatic; this is the *deliberate* stop. Start any goal
(L2 or L4), let its advance task reach `running`, then abort it:

```bash
# abort one task (its sandbox is torn down; the task goes terminal 'cancelled'):
python drive.py cancel_task '{"task_id":"<id>"}'        # → {"cancelled":true,"status":"cancelled"}

# or abort the whole goal (terminal 'cancelled'; tears down the in-flight advance):
python drive.py cancel_goal '{"goal_id":"<id>"}'
```

Confirm the abort holds:

```bash
python drive.py get_goal '{"goal_id":"<id>"}'         # phase: cancelled
docker ps --filter name=devclaw-                      # the sandbox container is gone (rm -f)
```

**The recovery interplay is the point.** `cancelled` is terminal, and startup
`recover()` only revives `running` rows — so kill the server right after a cancel
and restart it: the cancelled work stays cancelled (it is NOT resurrected, unlike
an orphaned `running` task). `cancel_goal` on an already-terminal goal is a
graceful no-op — safe to call more than once.

---

## 7. What to watch

- **Console** `http://localhost:8000/console` → open the goal for the live event tail + per-task drill-ins.
- **`get_events`** — the raw worker events per task/program (messages, tool calls, verify).
- **`$DEVCLAW_GOALS_DIR/<goal-id>/`** — `goal.yaml` + the generated views (`STATUS.md`, `log.md`, `deliveries.md`); state itself lives in SQLite (`get_goal`/`tail_goal` are the truth).
- **Server stderr** — `recovered=N`, notify attempts, `reaped` logs, sandbox spawn errors.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| task → `failed`, error `failed to spawn docker` | docker not reachable from the host process | `docker info`; check socket perms |
| task → `failed`, `sandbox exited N without a result line` | runner crashed inside the container | run the image by hand: `docker run --rm -v /tmp/sc-l1:/workspace -v ~/.claude:/home/agent/.claude:ro devclaw-sandbox:latest '{"kind":"implement_feature","workspace_dir":"/workspace","goal":"touch x"}'` and read stderr |
| runner error `acp_client.py not loadable` | image built wrong (acp_client.py not copied beside runner.py) | rebuild the sandbox image (§1) |
| agent can't auth / 401 from claude | `~/.claude` not logged in, or mounted empty | log in on the host; confirm `~/.claude` has session files |
| server won't start, `ANTHROPIC_API_KEY` complaints | a key is set in the env | `unset ANTHROPIC_API_KEY` |
| many containers pile up | global cap too high for the box | lower `DEVCLAW_MAX_CONCURRENT` |

---

## 9. Teardown: archive the run, then clean

Every run's DB is a metrics artifact (`eval_outcomes` rows, the full event log,
goal/task timings, retries). Runs are archived under `~/.devclaw/shakedown-runs/`
so future improvements can be measured against past behavior — never delete the
DB without archiving it first (ruled 2026-08-16).

```bash
# stop the server (Ctrl-C) FIRST — a live server writes the DB you're archiving
docker ps -a --filter name=devclaw- -q | xargs -r docker rm -f   # any stragglers

RUN_DIR=~/.devclaw/shakedown-runs/$(date -u +%Y-%m-%d)-$(git rev-parse --short HEAD)
mkdir -p "$RUN_DIR"
sqlite3 .shakedown/devclaw.db "PRAGMA wal_checkpoint(TRUNCATE);" || true
cp .shakedown/devclaw.db .shakedown/server.log "$RUN_DIR"/
cp -r .shakedown/goals "$RUN_DIR"/goals 2>/dev/null || true
# write $RUN_DIR/manifest.md: date, commit, sandbox-image id, scope, verdicts, finds
# (see the /live-shakedown skill for the template)

rm -rf .shakedown /tmp/sc-l1 /tmp/sc-l2 /tmp/sc-l4
# the sandbox image is reusable; remove only if you want: docker rmi devclaw-sandbox:latest
```

Replay an archived run in the console any time:
`DEVCLAW_DB=<archive>/devclaw.db DEVCLAW_TRANSPORT=http devclaw-mcp` → console, read-only.

---

## Note on CI

CI Lint is red on every PR because the GitHub Actions account is billing-locked —
no job starts, regardless of code. That's infrastructure, not a code failure; this
runbook is how you actually validate behavior until Actions is restored.
