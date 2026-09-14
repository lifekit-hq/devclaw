"""Seed a stub-engine instance for the quickstart (spec 047). Dev-only; never
# Run from the repo root: PYTHONPATH=. DEVCLAW_DB=... python specs/047-console-read-side/seed.py
imported by the package. Usage: DEVCLAW_DB=... python specs/047-console-read-side/seed.py"""

from __future__ import annotations

import json
import os
import uuid

from devclaw.project_registry import ProjectRegistry
from devclaw.state_store import StateStore

db = os.environ["DEVCLAW_DB"]
store = StateStore(db)
registry = ProjectRegistry(db)

pid, gid = "qs-project", "qs-goal"
if registry.get(pid) is None:
    registry.create(id=pid, name="Quickstart", repo_url="https://github.com/lifekit-hq/example.git",
                    workspace_dir="/tmp/qs-ws")
if store.get_goal(gid) is None:
    store.create_goal(id=gid, project_id=pid, workspace_dir="/tmp/qs-ws",
                      repo_url="https://github.com/lifekit-hq/example.git",
                      objective="Quickstart: a seeded goal", issues=[], branch="goal/qs")  # no issue: decide records locally, posts nothing


def task(kind: str, exit: str, exit_detail: str, result: dict, *, pre_run_sha: str = "") -> str:
    tid = str(uuid.uuid4())
    store.create_task(id=tid, kind=kind, workspace_dir="/tmp/qs-ws", goal="seeded",
                      parent_goal_id=gid, project_id=pid)
    if pre_run_sha:
        store.set_task_pre_run_sha(tid, pre_run_sha)
    store.mark_done(tid, json.dumps(result), exit=exit, exit_detail=exit_detail)
    return tid


usage = {"input_tokens": 1200, "output_tokens": 340, "cache_read_tokens": 5000, "cache_creation_tokens": 800}
verdict = {"achieved": False, "clauses": [
    {"clause": "a /metrics route answers", "satisfied": True, "evidence": "devclaw/server/routes/metrics.py:metrics"},
    {"clause": "the counter is monotonic", "satisfied": True, "evidence": "tests/test_deadman_metrics.py"},
    {"clause": "a Grafana panel exists", "satisfied": False, "evidence": "missing — should live in lifekit-stack"},
], "question": "", "structural_health": "clean", "concerns": [], "summary": "two of three clauses hold; the panel is absent."}

delivered = task("implement_feature", "DELIVERED", "landed the route", {"status": "ok", "agent_output": "DELIVERED: landed the route"})
review = task("review_repository", "REVIEW", "", {"status": "ok", "usage": usage,
              "agent_output": "review\n```json\n" + json.dumps(verdict) + "\n```"}, pre_run_sha="abc123def456")
blocked = task("implement_feature", "BLOCKED",
               "SQLite or Postgres for the ledger? — options: (a) SQLite | (b) Postgres — default: (a)",
               {"status": "blocked", "block_kind": "contract", "block_item": "",
                "question": "SQLite or Postgres for the ledger?", "options": ["SQLite", "Postgres"],
                "default": "SQLite", "recommended": 0, "usage": usage,
                "agent_output": "BLOCKED: SQLite or Postgres for the ledger? — options: (a) SQLite | (b) Postgres — default: (a)"})
print(json.dumps({"project": pid, "goal": gid, "delivered": delivered, "review": review, "blocked": blocked}))
