# Quickstart: proving the read side against a stub instance

Prerequisites: a venv with `pip install -e ".[dev]"`, `console/` built (`npm ci && npm run build`).

```bash
export DEVCLAW_TRANSPORT=http DEVCLAW_ENGINE=stub DEVCLAW_TOKEN=t DEVCLAW_PORT=18765 \
       DEVCLAW_HOST=127.0.0.1 DEVCLAW_DB=$PWD/.qs/devclaw.db DEVCLAW_STATE_DIR=$PWD/.qs
python -m devclaw.server &
```

Seed rows through the store (a short script: one project, one goal, three tasks — a BLOCKED
session whose `result_json` carries `question/options/default/recommended` and a `usage` block, a
REVIEW session whose `agent_output` ends with a three-clause verdict JSON with one clause
unsatisfied, and a DELIVERED session with no usage block). Then:

## P1 — needs-you

```bash
curl -s "localhost:18765/goals.json?token=t" | jq '.[0].attention'
# → kind "session", question, options[2..4], recommended ≥ 0, answered null
curl -s -XPOST "localhost:18765/goals/<id>/decide?token=t" -d '{"text":"<options[recommended]>"}' -H 'content-type: application/json'
curl -s "localhost:18765/goals.json?token=t" | jq '.[0].attention.answered'
# → { text: <the option's full text>, waitingOn: "hold" | "window" | "pause" | "lane busy" | "tick" }
```

Console: `/console/needs-you?token=t` shows the goal with buttons, the recommended one first and
marked; after a click it moves to the "answered, waiting for the tick" section.

## P2 — drill-down

```bash
curl -s "localhost:18765/projects/<pid>.json?token=t" | jq '.goals'
curl -s "localhost:18765/tasks/<tid>.json?token=t" | jq '{usage, block, verify, delivery, change}'
curl -s "localhost:18765/tasks/<tid>/events.json?token=t" | jq '.count'
```

Console: Projects → project → goal → session, three clicks; the session page shows events and
"not recorded" for the absent parts.

## P3 — usage

```bash
curl -s "localhost:18765/goals/<id>.json?token=t" | jq '.usage'
# → sums equal the seeded blocks; sessions_total 3, sessions_reported 2
curl -s "localhost:18765/metrics" | grep devclaw_tokens_total
# → four kinds, no token needed
```

## P4 — verdicts

```bash
curl -s "localhost:18765/verdicts.json?token=t" | jq '.verdicts[0] | {achieved, satisfied, total, head}'
# → achieved false, satisfied 2, total 3, head = the review task's pre_run_sha
```

Console: `/console/verdicts?token=t` lists it; expanding shows the three clauses with evidence.

## Tripwires

```bash
TMPDIR=$(mktemp -d) python -m pytest -q tests/test_runner_blocked.py tests/test_deadman_metrics.py \
    tests/test_route_shadowing.py tests/test_goal_layer_stays_readable.py
ruff check . && mypy && lint-imports
```
