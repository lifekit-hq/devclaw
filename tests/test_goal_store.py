"""Goal-layer durable-mind round-trips and cadence math (folded from goalclaw)."""

from __future__ import annotations

import pytest

from devclaw.goal.models import GoalStatus, InFlight
from devclaw.goal.store import GoalStore, parse_duration
from tests.goal_fakes import Clock, seed_goal


def test_parse_duration():
    assert parse_duration("90s") == 90
    assert parse_duration("30m") == 1800
    assert parse_duration("6h") == 21600
    assert parse_duration("1d") == 86400
    with pytest.raises(ValueError):
        parse_duration("nonsense")


def test_load_goal(tmp_path):
    seed_goal(tmp_path, "g1", backlog=["x", "y"])
    store = GoalStore(tmp_path)
    g = store.load_goal("g1")
    assert g.id == "g1"
    assert g.engine == "devclaw"
    assert g.workspace_dir == "/repos/demo"
    assert g.backlog == ["x", "y"]
    assert g.open_pr is True


def test_create_goal_writes_and_rejects_dupes(tmp_path):
    store = GoalStore(tmp_path)
    g = store.create_goal(
        "newg", objective="ship the thing", workspace_dir="/ws",
        done_when="it works", backlog=["a", "b"], cadence="6h",
    )
    assert g.objective == "ship the thing"
    assert store.exists("newg")
    assert store.load_goal("newg").backlog == ["a", "b"]
    with pytest.raises(FileExistsError):
        store.create_goal("newg", objective="dup", workspace_dir="/ws")


def test_create_goal_rejects_unparseable_cadence_at_creation(tmp_path):
    # An unparseable cadence (e.g. "urgent") must fail LOUD at creation, not
    # write a goal that throws a tick error every heartbeat forever
    # (fs-monitoring-outage-refile-2026-07-19 wedged this way). The message
    # names the goal + the accepted shape so the caller can fix and re-file.
    store = GoalStore(tmp_path)
    with pytest.raises(ValueError, match="cadence must be a duration"):
        store.create_goal(
            "badcad", objective="x", workspace_dir="/ws", cadence="urgent",
        )
    # And the broken row must NOT have been written.
    assert not store.exists("badcad")


def test_create_goal_persists_and_defaults_strictness(tmp_path):
    # ADR 0007: strictness round-trips through goal.yaml; default is "trust".
    store = GoalStore(tmp_path)
    store.create_goal("g_strict", objective="x", workspace_dir="/ws", strictness="strict")
    assert store.load_goal("g_strict").strictness == "strict"
    store.create_goal("g_default", objective="x", workspace_dir="/ws")
    assert store.load_goal("g_default").strictness == "trust"


def test_set_strictness_mutates_one_field_and_preserves_the_rest(tmp_path):
    # ADR 0007: the narrow single-field toggle rewrites strictness and nothing
    # else — objective/cadence/backlog survive the atomic goal.yaml rewrite.
    store = GoalStore(tmp_path)
    store.create_goal(
        "g", objective="keep me", workspace_dir="/ws", cadence="6h",
        backlog=["a", "b"], done_when="dw",
    )
    g = store.set_strictness("g", "strict")
    assert g.strictness == "strict"
    reloaded = store.load_goal("g")
    assert reloaded.strictness == "strict"
    assert reloaded.objective == "keep me"
    assert reloaded.cadence == "6h"
    assert reloaded.backlog == ["a", "b"]
    assert reloaded.done_when == "dw"
    # and it flips back
    assert store.set_strictness("g", "trust").strictness == "trust"


def test_set_strictness_rejects_bad_value(tmp_path):
    store = GoalStore(tmp_path)
    store.create_goal("g", objective="x", workspace_dir="/ws")
    with pytest.raises(ValueError, match="want 'trust' or 'strict'"):
        store.set_strictness("g", "urgent")
    # the goal is untouched (still the default)
    assert store.load_goal("g").strictness == "trust"


def test_load_goal_defaults_strictness_to_trust_when_absent(tmp_path):
    # A legacy goal.yaml written before the field must load advisory ("trust"),
    # never wedge — mirrors the mode/stub_acceptable legacy-load contract.
    import yaml

    d = tmp_path / "legacy-strict"
    d.mkdir()
    (d / "goal.yaml").write_text(
        yaml.safe_dump({"objective": "x", "workspace_dir": "/ws", "cadence": "1d"})
    )
    assert GoalStore(tmp_path).load_goal("legacy-strict").strictness == "trust"


def test_set_verify_cmd_mutates_one_field_and_preserves_the_rest(tmp_path):
    # Issue #711: set_verify_cmd is a narrow single-field override — it must
    # update verify_cmd and leave every other goal.yaml key byte-identical.
    store = GoalStore(tmp_path)
    store.create_goal(
        "g", objective="keep me", workspace_dir="/ws", cadence="6h",
        backlog=["a", "b"], done_when="dw", verify_cmd="pytest -x",
    )
    g = store.set_verify_cmd("g", "pytest -x --timeout=60")
    assert g.verify_cmd == "pytest -x --timeout=60"
    reloaded = store.load_goal("g")
    assert reloaded.verify_cmd == "pytest -x --timeout=60"
    assert reloaded.objective == "keep me"
    assert reloaded.cadence == "6h"
    assert reloaded.backlog == ["a", "b"]
    assert reloaded.done_when == "dw"


def test_set_verify_cmd_accepts_none_to_clear(tmp_path):
    # Clearing verify_cmd lets the manifest tier take effect on next dispatch.
    store = GoalStore(tmp_path)
    store.create_goal("g", objective="x", workspace_dir="/ws", verify_cmd="pytest")
    assert store.load_goal("g").verify_cmd == "pytest"
    g = store.set_verify_cmd("g", None)
    assert g.verify_cmd is None
    assert store.load_goal("g").verify_cmd is None


def test_create_goal_persists_stub_acceptable(tmp_path):
    # The owner's explicit opt-in for which tools may ship as stubs must
    # survive a round-trip through yaml — the done-gate reads it on every
    # evaluation, so silent loss = silent policy bypass.
    store = GoalStore(tmp_path)
    store.create_goal(
        "g", objective="ship mcp", workspace_dir="/ws",
        done_when="3 tools live, 1 stub", backlog=["scaffold"],
        stub_acceptable=["get_cashflow_report", "get_tax_lots"],
    )
    g = store.load_goal("g")
    assert g.stub_acceptable == ["get_cashflow_report", "get_tax_lots"]


def test_load_goal_defaults_stub_acceptable_to_empty_when_absent(tmp_path):
    # Legacy goals (written before this field existed) must load with an
    # empty list, which the done-gate treats as "no stubs allowed" — the
    # safe default.
    seed_goal(tmp_path, "legacy")
    g = GoalStore(tmp_path).load_goal("legacy")
    assert g.stub_acceptable == []


def test_goal_yaml_with_legacy_skills_required_key_still_loads(tmp_path):
    # The skill-library feature (Goal.skills_required) was removed 2026-07-13,
    # but goal.yaml files written before the removal — deployed VPS goals —
    # may still carry the key. Loading must tolerate it silently: ignore the
    # key, never crash.
    import yaml

    d = tmp_path / "legacy-skills"
    d.mkdir(parents=True)
    (d / "goal.yaml").write_text(
        yaml.safe_dump(
            {
                "objective": "Drive the demo repo to done.",
                "cadence": "1d",
                "engine": "devclaw",
                "workspace_dir": "/repos/demo",
                "repo_url": "https://example.com/demo.git",
                "verify_cmd": "pytest -q",
                "open_pr": True,
                "done_when": "all backlog items merged",
                "backlog": ["add a /health endpoint"],
                "skills_required": ["tdd"],  # the legacy shape
            }
        )
    )
    g = GoalStore(tmp_path).load_goal("legacy-skills")
    assert g.objective == "Drive the demo repo to done."
    assert g.backlog == ["add a /health endpoint"]
    assert not hasattr(g, "skills_required")  # the field is gone, key ignored


def test_status_roundtrip_with_eval_and_done_check(tmp_path):
    store = GoalStore(tmp_path, now=Clock())
    s = GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "t9", "task", "verify", is_done_check=True),
        next="verifying done",
        last_plan_at="2026-06-06T12:00:00+00:00",
        last_eval_verdict="on_track",
        last_eval_note="progressing",
    )
    store.save_status("g1", s)
    back = store.load_status("g1")
    assert back.phase == "verifying"
    assert back.in_flight is not None
    assert back.in_flight.id == "t9"
    assert back.in_flight.is_done_check is True
    assert back.last_eval_verdict == "on_track"


def test_missing_status_is_default(tmp_path):
    store = GoalStore(tmp_path)
    s = store.load_status("never")
    assert s.phase == "idle"
    assert s.in_flight is None


def test_goal_status_merge_fields_roundtrip(tmp_path):
    # spec 025: pending_merge_pr / merge_heal_attempted persist through the
    # store, and a row that never set them reads back the defaults.
    store = GoalStore(tmp_path, now=Clock())
    s = GoalStatus(
        phase="blocked",
        blocked_on="merge failed: conflict after heal",
        blocked_kind="mechanical:merge_failed",
        pending_merge_pr="https://github.com/o/r/pull/7",
        merge_heal_attempted=True,
    )
    store.save_status("g1", s)
    back = store.load_status("g1")
    assert back.pending_merge_pr == "https://github.com/o/r/pull/7"
    assert back.merge_heal_attempted is True
    assert back.blocked_kind == "mechanical:merge_failed"
    # defaults on an untouched goal
    fresh = store.load_status("never")
    assert fresh.pending_merge_pr == ""
    assert fresh.merge_heal_attempted is False


def test_log_append_and_recent(tmp_path):
    store = GoalStore(tmp_path, now=Clock())
    store.append_log("g1", "first")
    store.append_log("g1", "second")
    recent = store.recent_log("g1")
    assert "first" in recent and "second" in recent
    assert recent.index("first") < recent.index("second")  # newest at bottom


def test_deliveries_roundtrip(tmp_path):
    store = GoalStore(tmp_path, now=Clock())
    store.append_delivery(
        "g1", "add /health", "PR: #7\nAgent summary: added endpoint\nVerify: PASSED", ref_id="t1",
    )
    store.append_delivery("g1", "add logging", "PR: #8", ref_id="t2")
    d = store.recent_deliveries("g1")
    assert "add /health" in d and "#7" in d and "add logging" in d


def test_steering_sources_and_consumption(tmp_path):
    """PR5: steering is consumed by exact goal_steering row id (the mechanism
    GoalStore.transition(consume_steering=...) uses), not the retired
    file-line cursor — mechanically adapted from the pre-PR5 cursor-slicing
    version. Same intent: fresh steering reads back, consuming it clears it
    from unread, and a later append (e.g. an evaluator correction) is
    independently fresh regardless of what was already consumed."""
    store = GoalStore(tmp_path, now=Clock())
    store.append_steering("g1", ["focus on auth first"], source="denys")
    assert "focus on auth first" in store.unread_steering("g1")
    rows = store.unread_steering_rows("g1")
    assert len(rows) == 1 and "focus on auth first" in rows[0][1]

    # consume exactly that row — the mechanism GoalStore.transition() uses —
    # and it disappears from unread, same observable effect the old cursor
    # bump produced.
    store._goal_state.consume_steering_rows("g1", [rid for rid, _ in rows], 1)
    assert store.unread_steering("g1") == ""

    # evaluator appends a correction → becomes fresh steering, independent of
    # the already-consumed row. The [auto-eval] source marker must survive
    # into the planner-visible text — prompts/goal-planner.md documents
    # evaluator corrections as "marked [auto-eval]".
    store.append_steering("g1", ["redo the rate limiter per-user"], source="auto-eval")
    fresh = store.unread_steering("g1")
    assert "rate limiter" in fresh and "auto-eval" in fresh


def test_cadence_due(tmp_path):
    clock = Clock()
    store = GoalStore(tmp_path, now=clock)
    seed_goal(tmp_path, "g1", cadence="6h")
    goal = store.load_goal("g1")
    assert store.cadence_due(goal, GoalStatus(last_plan_at=None)) is True
    just_now = store.now_iso()
    assert store.cadence_due(goal, GoalStatus(last_plan_at=just_now)) is False
    clock.advance(6 * 3600 + 1)
    assert store.cadence_due(goal, GoalStatus(last_plan_at=just_now)) is True


def test_spec_roundtrip(tmp_path):
    store = GoalStore(tmp_path)
    seed_goal(tmp_path, "g")
    assert store.read_spec("g") == ""
    store.write_spec("g", "Build X")
    assert "Build X" in store.read_spec("g")
