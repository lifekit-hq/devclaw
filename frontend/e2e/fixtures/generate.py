"""Regenerates the e2e fixtures from the host's own feed routes.

Seeds a throwaway state store with one goal per console state, serves it with
the stub engine and writes each feed's JSON next to this file, so the
Playwright suite reads exactly what the routes return. Run from the repo root:

    .venv/bin/python frontend/e2e/fixtures/generate.py
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = "https://github.com/example/widgets"
HEAD = "4f2c9e1a7b3d5c8e0f1a2b3c4d5e6f708192a3b4"


def _usage(i: int, o: int, r: int, w: int) -> dict:
    return {"input_tokens": i, "output_tokens": o, "cache_read_tokens": r,
            "cache_creation_tokens": w, "source": "acp"}


def _seed(store, registry) -> None:
    registry.create(id="widgets", name="widgets", repo_url=REPO, workspace_dir="/work/widgets")
    for gid, objective, issues in (
        ("g-blocked", "Add CSV export to the reports page", [41]),
        ("g-running", "Speed up the search index rebuild", [52]),
        ("g-done", "Fix the timezone drift in daily digests", [37]),
    ):
        store.create_goal(id=gid, project_id="widgets", workspace_dir="/work/widgets",
                          repo_url=REPO, objective=objective, issues=issues,
                          branch=f"devclaw/{gid}")
        registry.link_goal("widgets", gid)

    # g-blocked: a delivered increment, then a session that stopped on a question.
    store.create_task(id="t-deliver-0001", kind="implement_feature", workspace_dir="/work/widgets",
                      goal="#41", parent_goal_id="g-blocked", project_id="widgets",
                      target_branch="devclaw/g-blocked", deliver=True, verify_cmd=".devclaw/verify")
    store.claim_pending("t-deliver-0001")
    store.set_task_pre_run_sha("t-deliver-0001", HEAD)
    store.mark_done("t-deliver-0001", json.dumps({
        "verify": {"ok": True, "rounds": 1, "cmd": ".devclaw/verify"},
        "delivery": {"pushed": True, "branch": "devclaw/g-blocked", "head": HEAD},
        "change": {"files": ["src/reports/export.py", "tests/test_export.py"], "insertions": 84,
                   "deletions": 3},
        "agent_output": "Added the CSV writer and its route.\n\nDELIVERED",
        "usage": _usage(182_000, 9_400, 1_250_000, 41_000),
    }), pr_url=f"{REPO}/pull/58", exit="DELIVERED", exit_detail="CSV export behind the reports route")
    for i, (etype, payload) in enumerate((
        ("session_started", {"branch": "devclaw/g-blocked"}),
        ("tool_call", {"tool": "Edit", "path": "src/reports/export.py"}),
        ("verify", {"ok": True, "rounds": 1}),
        ("session_exit", {"exit": "DELIVERED"}),
    )):
        store.append_event(task_id="t-deliver-0001", type=etype, source="runner",
                           payload_json=json.dumps(payload))
    store.create_task(id="t-blocked-0002", kind="implement_feature", workspace_dir="/work/widgets",
                      goal="#41", parent_goal_id="g-blocked", project_id="widgets",
                      target_branch="devclaw/g-blocked", deliver=True)
    store.claim_pending("t-blocked-0002")
    store.mark_done("t-blocked-0002", json.dumps({
        "question": "Should the export include archived reports?",
        "options": ["Exclude archived reports", "Include them behind a checkbox"],
        "recommended": 1, "default": "Exclude archived reports", "block_kind": "contract",
        "usage": _usage(64_000, 2_100, 410_000, 12_000),
    }), exit="BLOCKED", exit_detail="Should the export include archived reports?")

    # g-running: one session in flight.
    store.create_task(id="t-running-0003", kind="implement_feature", workspace_dir="/work/widgets",
                      goal="#52", parent_goal_id="g-running", project_id="widgets",
                      target_branch="devclaw/g-running", deliver=True)
    store.claim_pending("t-running-0003")

    # g-done: delivered, reviewed, merged.
    store.create_task(id="t-deliver-0004", kind="fix_bug", workspace_dir="/work/widgets",
                      goal="#37", parent_goal_id="g-done", project_id="widgets",
                      target_branch="devclaw/g-done", deliver=True)
    store.claim_pending("t-deliver-0004")
    store.mark_done("t-deliver-0004", json.dumps({"agent_output": "DELIVERED"}),
                    pr_url=f"{REPO}/pull/55", exit="DELIVERED", exit_detail="digest dates in UTC")
    store.create_task(id="t-review-0005", kind="review_repository", workspace_dir="/work/widgets",
                      goal="#37", parent_goal_id="g-done", project_id="widgets")
    store.claim_pending("t-review-0005")
    store.set_task_pre_run_sha("t-review-0005", HEAD)
    verdict = {
        "achieved": True, "summary": "Digests are computed in UTC and the drift test pins it.",
        "structural_health": "good", "concerns": [],
        "clauses": [
            {"clause": "Daily digests use the subscriber's timezone", "satisfied": True,
             "evidence": "tests/test_digest.py::test_tz_boundary"},
            {"clause": "No digest is sent twice across a DST change", "satisfied": True,
             "evidence": "tests/test_digest.py::test_dst_once"},
        ],
    }
    store.mark_done("t-review-0005", json.dumps({
        "agent_output": "```json\n" + json.dumps(verdict) + "\n```",
        "usage": _usage(40_000, 1_800, 220_000, 6_000),
    }), exit="REVIEW")
    store.close_goal("g-done", "achieved")

    store.set_run_schedule(True, "22:00", "07:00", "Europe/Dublin")


def main() -> None:
    scratch = tempfile.mkdtemp(prefix="devclaw-fixtures-", dir=os.environ.get("TMPDIR"))
    os.environ["DEVCLAW_DB"] = os.path.join(scratch, "devclaw.db")
    os.environ["DEVCLAW_ENGINE"] = "stub"
    os.environ.pop("DEVCLAW_TOKEN", None)

    from starlette.testclient import TestClient

    import devclaw.server.routes  # noqa: F401  (registers the routes)
    from devclaw.server._state import mcp, registry, store

    _seed(store, registry)
    client = TestClient(mcp.http_app(path="/mcp", stateless_http=True))
    feeds = {
        "goals": "/goals.json",
        "goal-blocked": "/goals/g-blocked.json",
        "goal-running": "/goals/g-running.json",
        "goal-done": "/goals/g-done.json",
        "projects": "/projects.json",
        "project-widgets": "/projects/widgets.json",
        "task-deliver": "/tasks/t-deliver-0001.json",
        "task-deliver-events": "/tasks/t-deliver-0001/events.json",
        "verdicts": "/verdicts.json?limit=200",
        "control": "/control.json",
    }
    for name, path in feeds.items():
        r = client.get(path)
        r.raise_for_status()
        (HERE / f"{name}.json").write_text(json.dumps(r.json(), indent=2) + "\n")
    (HERE / "userinfo.json").write_text(json.dumps(
        {"user": "owner", "email": "owner@example.com", "preferredUsername": "Example Owner"},
        indent=2) + "\n")


if __name__ == "__main__":
    main()
