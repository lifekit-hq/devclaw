"""Doctor (spec 016 US1) — seeded-fault tests, one per drift class.

Every test seeds a real drift condition into stubbed stores and asserts the
named check reports it with the right remedy. Cross-cutting guards: zero
cognition calls, zero writes, deterministic output, affirmative health,
crashed checks reported (never omitted).
"""

from __future__ import annotations

import json
import time

import pytest

from devclaw.doctor import Verdict, run_doctor
from devclaw.goal.store import GoalStore
from devclaw.project_registry import ProjectRegistry
from devclaw.state_store import StateStore

from tests.goal_fakes import FakeClaude, register_tmp_project, seed_goal

NOW_MS = int(time.time() * 1000)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Stubbed instance: shared devclaw.db + goals dir + registry, with the
    host-machine surfaces (claude dir, skills dir, oauth env) isolated so the
    developer's real ~/.claude never leaks into assertions."""
    claude_dir = tmp_path / "claude-home"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"expiresAt": NOW_MS + 30 * 24 * 3600 * 1000}}))
    monkeypatch.setenv("DEVCLAW_HOST_CLAUDE_DIR", str(claude_dir))
    cfg_dir = tmp_path / "claude-cfg"
    cfg_dir.mkdir()
    (cfg_dir / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "x@y"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    # NODE_AUTH_TOKEN too: unset keeps the registry check on its no-probe
    # path, so the suite can never reach the network from a dev machine
    # that happens to carry a real token.
    monkeypatch.delenv("NODE_AUTH_TOKEN", raising=False)

    store = StateStore(str(tmp_path / "devclaw.db"))
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    goal_store = GoalStore(goals_dir, state=store)
    registry = ProjectRegistry(str(tmp_path / "devclaw.db"))
    yield {"store": store, "goal_store": goal_store, "registry": registry,
           "goals_dir": goals_dir, "tmp": tmp_path, "claude_dir": claude_dir}
    store.close()


def _run(env, **kw):
    return run_doctor(env["store"], env["goal_store"], env["registry"], **kw)


def _findings(report, check_id):
    return [f for f in report.findings if f.check_id == check_id]


# ---- instance: migrations / legacy shapes --------------------------------




def test_legacy_lifecycle_row_detected(env):
    db = env["store"]._db
    db.execute("INSERT INTO goal_status (goal_id, lifecycle) VALUES ('legacy-g', NULL)")
    db.commit()
    (f,) = _findings(_run(env), "instance.legacy.goal_status_lifecycle")
    assert f.verdict is Verdict.FAIL and "pre-008" in f.evidence


def test_nullable_ref_id_schema_and_null_rows_detected(env):
    db = env["store"]._db
    db.execute("DROP TABLE goal_deliveries")
    db.execute("CREATE TABLE goal_deliveries (id INTEGER PRIMARY KEY, goal_id TEXT NOT NULL, "
               "ref_id TEXT, instruction TEXT, body TEXT, created_at INTEGER NOT NULL)")
    db.execute("INSERT INTO goal_deliveries (goal_id, ref_id, created_at) VALUES ('g', NULL, 1)")
    db.commit()
    (f,) = _findings(_run(env), "instance.legacy.deliveries_ref_id")
    assert f.verdict is Verdict.FAIL
    assert "NULL ref_id" in f.evidence and "1 delivery row" in f.evidence


def test_dropped_shapes_still_present_detected(env):
    db = env["store"]._db
    db.execute("CREATE TABLE goal_docs (goal_id TEXT)")
    db.execute("ALTER TABLE goal_status ADD COLUMN inbox_ingest_cursor TEXT")
    # program-lane remnants (022 demolition tail): table + column + the
    # load-bearing zombie — a pending row the dead lane left behind, which
    # with the column dropped nothing filters out of the pending scan.
    db.execute("CREATE TABLE programs (id TEXT PRIMARY KEY)")
    db.execute("ALTER TABLE tasks ADD COLUMN program_id TEXT")
    db.execute(
        "INSERT INTO tasks (id, kind, status, workspace_dir, goal, created_at, program_id) "
        "VALUES ('z1', 'implement_feature', 'pending', '/ws', 'g', 0, 'prog-1')"
    )
    db.commit()
    report = _run(env)
    (docs,) = _findings(report, "instance.legacy.goal_docs_table")
    (cursor,) = _findings(report, "instance.legacy.inbox_cursor_column")
    (lane,) = _findings(report, "instance.legacy.program_lane")
    assert docs.verdict is Verdict.FAIL and cursor.verdict is Verdict.FAIL
    assert lane.verdict is Verdict.FAIL
    assert "zombie pending" in lane.evidence and "programs" in lane.evidence


def test_program_lane_dropped_reports_ok(env):
    (lane,) = _findings(_run(env), "instance.legacy.program_lane")
    assert lane.verdict is Verdict.OK


# ---- instance: auth (mechanical, never invokes claude) -------------------


def test_missing_credentials_file_fails(env):
    (env["claude_dir"] / ".credentials.json").unlink()
    (f,) = _findings(_run(env), "instance.auth.credentials_file")
    assert f.verdict is Verdict.FAIL and "relogin" in f.remedy


def test_expired_credential_fails(env):
    (env["claude_dir"] / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"expiresAt": NOW_MS - 1000}}))
    (f,) = _findings(_run(env), "instance.auth.credentials_file")
    assert f.verdict is Verdict.FAIL and "expired" in f.evidence


def test_expiring_soon_credential_warns(env):
    (env["claude_dir"] / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"expiresAt": NOW_MS + 3600 * 1000}}))
    (f,) = _findings(_run(env), "instance.auth.credentials_file")
    assert f.verdict is Verdict.WARN and "expires in" in f.evidence


def test_invisible_claude_dir_is_unknown_not_ok(env, monkeypatch):
    monkeypatch.setenv("DEVCLAW_HOST_CLAUDE_DIR", str(env["tmp"] / "nope"))
    (f,) = _findings(_run(env), "instance.auth.credentials_file")
    assert f.verdict is Verdict.UNKNOWN


def test_missing_claude_json_warns(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env["tmp"] / "empty-cfg"))
    (f,) = _findings(_run(env), "instance.auth.claude_json")
    assert f.verdict is Verdict.WARN


def test_active_usage_pause_warns_with_clear_verb(env):
    env["store"].set_global_pause(NOW_MS + 30 * 60000, "usage limit hit")
    (f,) = _findings(_run(env), "instance.auth.pause")
    assert f.verdict is Verdict.WARN and "clear_usage_pause" in f.remedy


# ---- instance: skills bundle ---------------------------------------------


def test_missing_skills_bundle_fails_loud(env, monkeypatch):
    import devclaw.engine.host as host
    monkeypatch.setattr(host, "SKILLS_DIR", str(env["tmp"] / "no-skills"))
    (f,) = _findings(_run(env), "instance.skills.bundle")
    assert f.verdict is Verdict.FAIL and "skills_missing" in f.evidence


def test_real_repo_skills_bundle_resolves_all_kinds(env):
    # the checkout's actual runner/skills is the default SKILLS_DIR — it must
    # resolve every kind, or the repo itself is shipping a broken bundle.
    (f,) = _findings(_run(env), "instance.skills.bundle")
    assert f.verdict is Verdict.OK, f.evidence


# ---- instance: run schedule ----------------------------------------------


def test_schedule_key_absent_warns_lost_window(env):
    (f,) = [x for x in _findings(_run(env), "instance.schedule.raw_key")]
    assert f.verdict is Verdict.WARN
    assert "absent" in f.evidence and f.remedy == "set_run_schedule"


def test_schedule_key_corrupt_fails_instead_of_silent_default(env):
    env["store"].set_meta("run_schedule", "{not-json")
    fs = _findings(_run(env), "instance.schedule.raw_key")
    assert any(f.verdict is Verdict.FAIL and "corrupt" in f.evidence for f in fs)


def test_schedule_key_valid_reports_ok_with_window(env):
    env["store"].set_run_schedule(True, "22:00", "05:00", "Europe/Kyiv")
    fs = _findings(_run(env), "instance.schedule.raw_key")
    assert any(f.verdict is Verdict.OK and "22:00" in f.evidence for f in fs)


def test_corrupt_per_goal_window_detected(env):
    env["store"].set_run_schedule(True, "22:00", "05:00", "Europe/Kyiv")
    env["store"].set_meta("run_schedule:g1", "][")
    fs = _findings(_run(env), "instance.schedule.raw_key")
    assert any(f.verdict is Verdict.FAIL and "run_schedule:g1" in f.evidence for f in fs)


# ---- project section ------------------------------------------------------


def test_dangling_goal_link_produces_finding_with_link_goal_remedy(env, tmp_path):
    pid = register_tmp_project(env["registry"], str(tmp_path / "ws1"))
    env["registry"].link_goal(pid, "goal-that-was-refiled")
    (f,) = _findings(_run(env), "project.links.dangling")
    assert f.verdict is Verdict.WARN
    assert "goal-that-was-refiled" in f.evidence
    assert f.remedy.startswith("link_goal") and f.project_id == pid


def test_unstamped_goal_on_project_workspace_detected(env, tmp_path):
    pid = register_tmp_project(env["registry"], str(tmp_path / "ws2"))
    project = env["registry"].get(pid)
    seed_goal(env["goals_dir"], "g-unstamped", workspace_dir=project.workspace_dir,
              project_id=None)
    (f,) = _findings(_run(env), "project.links.unstamped_goals")
    assert f.verdict is Verdict.WARN and "g-unstamped" in f.evidence


def test_undispatchable_workspace_reason_surfaced(env, tmp_path):
    pid = register_tmp_project(env["registry"], str(tmp_path / "ws3"), git_init=False)
    (env["tmp"] / "unused").mkdir(exist_ok=True)  # keep tmp layout deterministic
    (f,) = _findings(_run(env), "project.workspace.preflight")
    assert f.project_id == pid
    # ws3 exists (register_tmp_project creates it) but has no .git
    assert f.verdict is Verdict.FAIL and ".git" in f.evidence


def test_project_id_scoping_limits_project_section(env, tmp_path):
    a = register_tmp_project(env["registry"], str(tmp_path / "wsA"), project_id="proj-a")
    register_tmp_project(env["registry"], str(tmp_path / "wsB"), project_id="proj-b")
    report = _run(env, project_id=a)
    assert {f.project_id for f in report.findings if f.project_id} == {a}


# ---- cross-cutting guards -------------------------------------------------


def test_doctor_spends_zero_tokens_and_writes_nothing(env, tmp_path):
    register_tmp_project(env["registry"], str(tmp_path / "ws-z"))
    evaluator = FakeClaude()
    # settle any pending writes, then snapshot the DB bytes
    env["store"]._db.commit()
    db_file = env["tmp"] / "devclaw.db"
    before = db_file.read_bytes()
    _run(env)
    env["store"]._db.commit()
    assert evaluator.calls == 0  # doctor has no cognition seam at all
    assert db_file.read_bytes() == before  # read-only by construction


def test_doctor_is_deterministic_for_unchanged_state(env, tmp_path):
    register_tmp_project(env["registry"], str(tmp_path / "ws-d"))
    one = json.dumps(_run(env).to_dict(), indent=2)
    two = json.dumps(_run(env).to_dict(), indent=2)
    assert one == two


def test_doctor_reports_healthy_affirmatively(env):
    # green everything the fixture doesn't already: the run window
    env["store"].set_run_schedule(True, "22:00", "05:00", "Europe/Kyiv")
    report = _run(env)
    assert report.healthy, [f for f in report.findings if f.verdict is not Verdict.OK]
    assert report.findings  # every check listed as ok — never empty output
    assert all(f.verdict is Verdict.OK for f in report.findings)


def test_crashed_check_reports_unknown_never_omitted(env, monkeypatch):
    import devclaw.doctor as doctor_pkg

    def check_boom(ctx):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(doctor_pkg, "INSTANCE_CHECKS", (check_boom,))
    report = _run(env)
    (f,) = [x for x in report.findings if x.check_id == "instance.boom"]
    assert f.verdict is Verdict.UNKNOWN and "probe exploded" in f.evidence


# ---- US3: manifest / boilerplate drift ------------------------------------


def test_absent_manifest_warns_with_onboard_remedy(env, tmp_path):
    register_tmp_project(env["registry"], str(tmp_path / "ws-m1"))
    (f,) = _findings(_run(env), "project.manifest.presence")
    assert f.verdict is Verdict.WARN and "onboard" in f.remedy


def test_malformed_manifest_is_a_fail_finding(env, tmp_path):
    ws = tmp_path / "ws-m2"
    register_tmp_project(env["registry"], str(ws))
    (ws / "devclaw.json").write_text("{oops")
    (f,) = _findings(_run(env), "project.manifest.valid")
    assert f.verdict is Verdict.FAIL and "human-owned" in f.remedy


def test_schema_newer_than_instance_is_a_fail_finding(env, tmp_path):
    ws = tmp_path / "ws-m3"
    register_tmp_project(env["registry"], str(ws))
    (ws / "devclaw.json").write_text('{"schemaVersion": 999}')
    (f,) = _findings(_run(env), "project.manifest.valid")
    assert f.verdict is Verdict.FAIL and "instance too old" in f.evidence


def test_boilerplate_revision_behind_names_both_revisions(env, tmp_path, monkeypatch):
    import devclaw.project_manifest as pm

    ws = tmp_path / "ws-m4"
    register_tmp_project(env["registry"], str(ws))
    (ws / "devclaw.json").write_text('{"schemaVersion": 1, "boilerplateRevision": 1}')
    monkeypatch.setattr(pm, "BOILERPLATE_REVISION", 2)
    (f,) = _findings(_run(env), "project.manifest.revision")
    assert f.verdict is Verdict.WARN
    assert "revision 1" in f.evidence and "2" in f.evidence
    assert "onboard" in f.remedy


def test_unpaired_managed_marker_is_a_fail(env, tmp_path):
    ws = tmp_path / "ws-m5"
    register_tmp_project(env["registry"], str(ws))
    (ws / "AGENTS.md").write_text("x\n<!-- devclaw:managed:start -->\nowned\n")
    (f,) = _findings(_run(env), "project.markers.integrity")
    assert f.verdict is Verdict.FAIL and "unpaired" in f.evidence


def test_scaffold_drift_detected_against_packaged_source(env, tmp_path):
    from devclaw.speckit_setup import scaffold_specify

    ws = tmp_path / "ws-m6"
    register_tmp_project(env["registry"], str(ws))
    scaffold_specify(str(ws))
    # matches the packaged source ⇒ ok
    (ok,) = _findings(_run(env), "project.scaffold.drift")
    assert ok.verdict is Verdict.OK
    # mutate one canonical file ⇒ drift warn naming it
    tmpl = next((ws / ".specify" / "templates").glob("*.md"))
    tmpl.write_text(tmpl.read_text() + "\nlocal fork\n")
    (f,) = _findings(_run(env), "project.scaffold.drift")
    assert f.verdict is Verdict.WARN and tmpl.name in f.evidence


# ---- spec 030 FR-005a: undeclared capability advisory ---------------------


def test_undeclared_private_registry_dependency_is_advisory_only(env, tmp_path):
    """Seeded fault: the repo resolves against a private npm registry but its
    devclaw.json declares no ``registry:*`` capability — the write-and-forget
    cost of explicit-only declaration (spec 030 FR-005a). Doctor must SEE it
    (a WARN naming the file and the fix) and never escalate it to a FAIL: this
    advisory is a report line, never a dispatch hold."""
    ws = tmp_path / "ws-cap1"
    register_tmp_project(env["registry"], str(ws))
    (ws / "devclaw.json").write_text('{"schemaVersion": 1, "boilerplateRevision": 1}')
    (ws / ".npmrc").write_text("@lifekit-hq:registry=https://npm.pkg.github.com\n")

    (f,) = _findings(_run(env), "project.capabilities.undeclared")
    assert f.verdict is Verdict.WARN            # advisory — never FAIL
    assert ".npmrc" in f.evidence and "npm.pkg.github.com" in f.evidence
    assert "registry:npm-github" in f.remedy

    # Declaring the capability settles it — the same repo, one manifest key.
    (ws / "devclaw.json").write_text(
        '{"schemaVersion": 1, "boilerplateRevision": 1, '
        '"capabilities": ["registry:npm-github"]}'
    )
    (ok,) = _findings(_run(env), "project.capabilities.undeclared")
    assert ok.verdict is Verdict.OK


def test_no_private_registry_dependency_is_ok(env, tmp_path):
    """A repo with no visible private-registry dependency declares nothing and
    is clean — the advisory must not nag every public-registry project."""
    ws = tmp_path / "ws-cap2"
    register_tmp_project(env["registry"], str(ws))
    (ws / "devclaw.json").write_text('{"schemaVersion": 1, "boilerplateRevision": 1}')
    (ws / "package-lock.json").write_text('{"packages": {"": {"name": "x"}}}')

    (f,) = _findings(_run(env), "project.capabilities.undeclared")
    assert f.verdict is Verdict.OK


# ---- instance: scorecard convergence ledger (spec 018 US1) ----------------


def test_missing_goal_convergence_table_detected(env):
    """Seeded fault: goal tables exist but goal_convergence was dropped (a DB
    predating spec 018) — every close would land rounds-unknown; FAIL with the
    restart remedy (GoalState bootstraps tables at construction)."""
    db = env["store"]._db
    db.execute("DROP TABLE goal_convergence")
    db.commit()
    (f,) = _findings(_run(env), "instance.scorecard.goal_convergence")
    assert f.verdict is Verdict.FAIL
    assert "goal_convergence" in f.evidence and "restart" in f.remedy


def test_goal_convergence_table_present_is_ok(env):
    (f,) = _findings(_run(env), "instance.scorecard.goal_convergence")
    assert f.verdict is Verdict.OK


# ---- instance: merge-on-close state shape (spec 025 US1) ------------------


def test_merge_on_close_columns_present_is_ok(env):
    (f,) = _findings(_run(env), "instance.merge.close_columns")
    assert f.verdict is Verdict.OK


def test_missing_merge_columns_detected(env):
    """Seeded fault: a DB predating spec 025 — the owed-merge marker columns
    are absent, so a pending merge would be forgotten across a restart."""
    db = env["store"]._db
    db.execute("ALTER TABLE goal_status DROP COLUMN pending_merge_pr")
    db.commit()
    (f,) = _findings(_run(env), "instance.merge.close_columns")
    assert f.verdict is Verdict.FAIL
    assert "pending_merge_pr" in f.evidence and "restart" in f.remedy


# ---- instance: interventions ledger (spec 032 US5) -------------------------


def test_missing_goal_interventions_table_detected(env):
    """Seeded fault: a DB predating spec 032 — human verbs would be dropped
    and the north-star metric reads unknown; FAIL with the restart remedy."""
    db = env["store"]._db
    db.execute("DROP TABLE goal_interventions")
    db.commit()
    (f,) = _findings(_run(env), "instance.scorecard.goal_interventions")
    assert f.verdict is Verdict.FAIL
    assert "goal_interventions" in f.evidence and "restart" in f.remedy


def test_goal_interventions_table_present_is_ok(env):
    (f,) = _findings(_run(env), "instance.scorecard.goal_interventions")
    assert f.verdict is Verdict.OK


# ---- instance: CI-hold state shape (spec 032 US1) --------------------------


@pytest.mark.parametrize("column,check_id", [
    ("pending_done_proposal", "instance.ci.goal_status_pending_done_proposal"),
    ("ci_green_head", "instance.ci.goal_status_ci_green_head"),
])
def test_missing_ci_hold_column_detected(env, column, check_id):
    """Seeded fault: a DB predating spec 032 — without ``pending_done_proposal``
    a done proposal held on CI is never re-driven; without ``ci_green_head``
    merge-on-close cannot prove it merges the head whose CI was green."""
    db = env["store"]._db
    db.execute(f"ALTER TABLE goal_status DROP COLUMN {column}")
    db.commit()
    (f,) = _findings(_run(env), check_id)
    assert f.verdict is Verdict.FAIL
    assert column in f.evidence and "restart" in f.remedy


def test_ci_hold_columns_present_is_ok(env):
    for check_id in ("instance.ci.goal_status_pending_done_proposal",
                     "instance.ci.goal_status_ci_green_head"):
        (f,) = _findings(_run(env), check_id)
        assert f.verdict is Verdict.OK


def test_missing_suppressed_pings_table_detected(env):
    """Seeded fault: a DB predating spec 025 US3 — arming quiet mode would
    DROP pings instead of recording them."""
    db = env["store"]._db
    db.execute("DROP TABLE suppressed_pings")
    db.commit()
    (f,) = _findings(_run(env), "instance.quiet.suppressed_pings")
    assert f.verdict is Verdict.FAIL
    assert "suppressed_pings" in f.evidence and "restart" in f.remedy


def test_done_goal_with_owed_merge_is_a_fail(env):
    """Seeded fault: a goal reads done while pending_merge_pr is set — a state
    the close path must never produce (merge fires BEFORE the ACHIEVE
    transition)."""
    db = env["store"]._db
    db.execute(
        "INSERT INTO goal_status (goal_id, version, phase, pending_merge_pr) "
        "VALUES ('g-owed', 1, 'done', 'https://github.com/o/r/pull/9')"
    )
    db.commit()
    (f,) = _findings(_run(env), "instance.merge.close_columns")
    assert f.verdict is Verdict.FAIL
    assert "g-owed" in f.evidence
# ---- project: referenced-goal record shape (spec 019 US1) -----------------


def test_malformed_issue_refs_detected(env):
    """Seeded fault: a referenced goal whose refs are not positive ints —
    hand-edit/corruption drift that would otherwise surface only as a
    mid-night dispatch crash."""
    register_tmp_project(env["registry"], env["tmp"] / "ws-refs", project_id="refs-proj")
    seed_goal(env["goals_dir"], "bad-refs", project_id="refs-proj", issue_refs=[-1])
    (f,) = [x for x in _findings(_run(env), "project.goals.issue_refs")
            if x.verdict is Verdict.FAIL]
    assert "bad-refs" in f.evidence and "cancel + recreate" in f.remedy


def test_wellformed_issue_refs_ok(env):
    register_tmp_project(env["registry"], env["tmp"] / "ws-refs2", project_id="refs-proj2")
    seed_goal(env["goals_dir"], "good-refs", project_id="refs-proj2", issue_refs=[4, 7])
    fs = _findings(_run(env), "project.goals.issue_refs")
    assert fs and all(x.verdict is Verdict.OK for x in fs
                      if x.project_id == "refs-proj2")


# ---- instance: pr_ledger (spec 018 US2) -----------------------------------


def test_missing_pr_ledger_table_detected(env):
    db = env["store"]._db
    db.execute("DROP TABLE pr_ledger")
    db.commit()
    (f,) = _findings(_run(env), "instance.scorecard.pr_ledger")
    assert f.verdict is Verdict.FAIL and "pr_ledger" in f.evidence


def test_populated_never_refreshed_ledger_is_a_warn(env):
    db = env["store"]._db
    db.execute("INSERT INTO pr_ledger (pr_url, opened_at_ms) VALUES ('https://gh/x/1', 1)")
    db.commit()
    (f,) = _findings(_run(env), "instance.scorecard.pr_ledger")
    assert f.verdict is Verdict.WARN and "stale" in f.evidence


# ---- instance: per-project sandbox sizing (spec 020 US4) -------------------


def test_project_sizing_check_passes_with_admittable_overrides(env, monkeypatch):
    import devclaw.host_resources as host_resources
    monkeypatch.setattr(host_resources, "host_mem_total_bytes", lambda: 64 << 30)
    env["registry"].create(id="fe", name="FE", workspace_dir="/ws/fe",
                           sandbox_memory="6g")
    report = _run(env)
    fs = _findings(report, "instance.sandbox.project_sizing")
    assert fs and all(f.verdict is Verdict.OK for f in fs)


def test_project_sizing_check_fails_when_the_host_shrank(env, monkeypatch):
    """Seeded fault (spec 016 FR-014): the override was admittable at write
    time on a bigger host; after a host shrink the stored value can never be
    admitted and dispatch would defer forever — doctor names it."""
    import devclaw.host_resources as host_resources
    monkeypatch.setattr(host_resources, "host_mem_total_bytes", lambda: 64 << 30)
    env["registry"].create(id="fe", name="FE", workspace_dir="/ws/fe",
                           sandbox_memory="32g")
    # the "shrink": doctor now sees an 8 GiB host
    monkeypatch.setattr(host_resources, "host_mem_total_bytes", lambda: 8 << 30)
    report = _run(env)
    fs = _findings(report, "instance.sandbox.project_sizing")
    assert fs and any(f.verdict is Verdict.FAIL for f in fs)
    assert any("no longer admittable" in f.evidence for f in fs)


# ---- instance: goal_status columns a brake depends on ---------------------
#
# One class, not one test per column (spec-016 FR-014): a DB bootstrapped
# before a column's ALTER TABLE reads it as absent, the brake that column
# carries degrades SILENTLY, and the stubbed suite structurally cannot see it
# because it always builds a fresh schema. Every new goal_status column a
# brake reads adds a case here — never a sibling test.
_BRAKE_COLUMNS = [
    # (column, check id, issue #728 / spec 030)
    ("slice_hold_count", "instance.dispatch.goal_status_slice_hold_count"),
    ("env_hold_notified", "instance.env.goal_status_env_hold_notified"),
    ("env_heal_attempts", "instance.env.goal_status_env_heal_attempts"),
]


@pytest.mark.parametrize("column,cid", _BRAKE_COLUMNS)
def test_brake_column_absent_detected(env, column, cid):
    """Seeded fault: the column is dropped, so the instance looks like one
    whose DB predates the migration that added it."""
    db = env["store"]._db
    db.execute(f"ALTER TABLE goal_status DROP COLUMN {column}")
    db.commit()
    (f,) = _findings(_run(env), cid)
    assert f.verdict is Verdict.FAIL
    assert column in f.evidence and "restart" in f.remedy


@pytest.mark.parametrize("column,cid", _BRAKE_COLUMNS)
def test_brake_column_present_is_ok(env, column, cid):
    (f,) = _findings(_run(env), cid)
    assert f.verdict is Verdict.OK and column in f.evidence


def test_donegate_progress_column_absent_detected(env):
    """Seeded fault (spec-016 FR-014): donegate_progress dropped → the DB
    predates the progress-aware churn brake; every done-gate round reads as
    flat and a converging goal parks at the cap exactly as before the fix."""
    db = env["store"]._db
    db.execute("ALTER TABLE goal_status DROP COLUMN donegate_progress")
    db.commit()
    (f,) = _findings(_run(env), "instance.donegate.goal_status_donegate_progress")
    assert f.verdict is Verdict.FAIL
    assert "donegate_progress" in f.evidence and "restart" in f.remedy


def test_donegate_progress_column_present_is_ok(env):
    (f,) = _findings(_run(env), "instance.donegate.goal_status_donegate_progress")
    assert f.verdict is Verdict.OK


def test_problems_tables_absent_detected(env):
    """Seeded fault (spec-016 FR-014, spec 031): goal_problems dropped → the DB
    predates spec 031; a human-gated block cannot record its Problem."""
    db = env["store"]._db
    db.execute("DROP TABLE goal_problems")
    db.commit()
    (f,) = _findings(_run(env), "instance.problems.tables")
    assert f.verdict is Verdict.FAIL
    assert "goal_problems" in f.evidence and "restart" in f.remedy


def test_problems_tables_present_is_ok(env):
    (f,) = _findings(_run(env), "instance.problems.tables")
    assert f.verdict is Verdict.OK


def test_problem_pointer_drift_detected(env):
    """Seeded fault (spec 031): goal_status.problem_id points at a Problem that
    is not open — drift the stubbed suite cannot see on a live DB."""
    db = env["store"]._db
    db.execute(
        "INSERT INTO goal_problems (id, goal_id, kind, raised_by, what, options_json, "
        "default_key, timebox_at, status, raised_at) VALUES "
        "('prb_x', 'g-drift', 'needs_answer', 'done_gate', 'w', '[]', 'k', 1, 'resolved', 1)"
    )
    db.execute(
        "INSERT INTO goal_status (goal_id, version, state, phase, lifecycle, problem_id, updated_at) "
        "VALUES ('g-drift', 1, 'blocked', 'blocked', 'executing', 'prb_x', 1)"
    )
    db.commit()
    (f,) = _findings(_run(env), "instance.problems.status_pointer")
    assert f.verdict is Verdict.FAIL
    assert "not open" in f.evidence


def test_problem_pointer_healthy_is_ok(env):
    (f,) = _findings(_run(env), "instance.problems.status_pointer")
    assert f.verdict is Verdict.OK


# ---- instance: registry-read credential (seeded faults) -------------------
# The tinyspec that added NODE_AUTH_TOKEN specified only the UNSET case
# ("blank ⇒ no forward, byte-identical"). Set-but-invalid was never
# considered and is strictly worse: it crosses into every sandbox and only
# surfaces as an `npm ci` 401 in there, after eating a goal's dispatch
# budget. Doctor is the deployed-instance guard for that; these are its
# seeded faults. NOTE: every case either leaves the token unset or patches
# the probe — the suite must never make a real network call.
_REG_CID = "instance.registry.token"


def _patch_probe(monkeypatch, status):
    from devclaw.doctor import checks_instance as ci

    called = {}

    def _fake(token, timeout_s=5.0):
        called["token_seen"] = token
        return status

    monkeypatch.setattr(ci, "_probe_registry_token", _fake)
    return called


def test_registry_token_unset_warns_and_never_probes(env, monkeypatch):
    def _boom(token, timeout_s=5.0):  # pragma: no cover - must not run
        raise AssertionError("probed on the unset path")

    from devclaw.doctor import checks_instance as ci

    monkeypatch.setattr(ci, "_probe_registry_token", _boom)
    (f,) = _findings(_run(env), _REG_CID)
    # unset is a supported posture (pre-token deployment), so it stays OK and
    # a clean instance still reports healthy — only malformed/rejected fails.
    assert f.verdict is Verdict.OK and "not set" in f.evidence


def test_registry_token_malformed_fails_without_probing(env, monkeypatch):
    """The 2026-08-31 incident shape: a set-but-not-a-GitHub-token value."""
    from devclaw.doctor import checks_instance as ci

    def _boom(token, timeout_s=5.0):  # pragma: no cover - must not run
        raise AssertionError("probed a malformed token")

    monkeypatch.setattr(ci, "_probe_registry_token", _boom)
    monkeypatch.setenv("NODE_AUTH_TOKEN", "powershell-junk-not-a-token")
    (f,) = _findings(_run(env), _REG_CID)
    assert f.verdict is Verdict.FAIL
    assert "not a GitHub token" in f.evidence and f.remedy


@pytest.mark.parametrize("status", [401, 403])
def test_registry_token_rejected_by_github_fails(env, monkeypatch, status):
    _patch_probe(monkeypatch, status)
    monkeypatch.setenv("NODE_AUTH_TOKEN", "ghp_wellformedbutdead")
    (f,) = _findings(_run(env), _REG_CID)
    assert f.verdict is Verdict.FAIL and str(status) in f.evidence


def test_registry_token_unreachable_is_unknown_never_ok(env, monkeypatch):
    """An unverifiable credential must never read as a healthy one."""
    _patch_probe(monkeypatch, None)
    monkeypatch.setenv("NODE_AUTH_TOKEN", "ghp_wellformedunverifiable")
    (f,) = _findings(_run(env), _REG_CID)
    assert f.verdict is Verdict.UNKNOWN
    assert f.verdict is not Verdict.OK


def test_registry_token_valid_is_ok(env, monkeypatch):
    _patch_probe(monkeypatch, 200)
    monkeypatch.setenv("NODE_AUTH_TOKEN", "ghp_goodtoken")
    (f,) = _findings(_run(env), _REG_CID)
    assert f.verdict is Verdict.OK


@pytest.mark.parametrize("value", ["powershell-junk-not-a-token", "ghp_supersecretvalue"])
def test_registry_token_value_never_appears_in_any_finding(env, monkeypatch, value):
    """The token is a credential: shape and probe status only, never the
    value — not in evidence, not in remedy, not anywhere in the report."""
    _patch_probe(monkeypatch, 401)
    monkeypatch.setenv("NODE_AUTH_TOKEN", value)
    report = _run(env)
    serialized = json.dumps(report.to_dict())
    assert value not in serialized
# ---- instance: a registered gate that is never consulted (seeded faults) ---
# The stubbed suite structurally cannot see this class: the declared-scope
# gate's own tests stayed green by BUILDING its trigger synthetically, while
# the production dispatch path stopped emitting it entirely after spec 022 US3.
# A green suite proves a gate CAN fire; only the running instance shows whether
# it ever does. Hence a doctor check, and hence these seeded faults.
_GATE_CID = "instance.gates.consultation"


def _seed_gate_outcomes(env, gates_per_settle, n):
    """Append n gate_outcomes events, each recording the same gate roster."""
    for i in range(n):
        env["store"].append_event(
            task_id=f"t{i}", type="gate_outcomes", source="settle",
            payload_json=json.dumps({"gates": gates_per_settle}),
        )


def test_gate_never_consulted_across_the_window_fails_loud(env):
    _seed_gate_outcomes(env, [
        {"gate_id": "verify", "consulted": True, "ok": True},
        {"gate_id": "scope", "consulted": False, "ok": True},
    ], 25)
    (f,) = _findings(_run(env), _GATE_CID)
    assert f.verdict is Verdict.FAIL
    assert "scope (0/25)" in f.evidence
    assert "verify" not in f.evidence, "a consulted gate is not reported inert"
    assert f.remedy


def test_a_gate_consulted_even_once_is_not_inert(env):
    gates = [{"gate_id": "browser", "consulted": False, "ok": True}]
    _seed_gate_outcomes(env, gates, 24)
    _seed_gate_outcomes(env, [{"gate_id": "browser", "consulted": True, "ok": True}], 1)
    (f,) = _findings(_run(env), _GATE_CID)
    assert f.verdict is Verdict.OK, "self-skipping is normal; never firing is not"


def test_below_the_window_is_unproven_not_inert(env):
    """Too few settles is not evidence — don't cry wolf on a fresh instance."""
    _seed_gate_outcomes(env, [{"gate_id": "scope", "consulted": False, "ok": True}], 5)
    (f,) = _findings(_run(env), _GATE_CID)
    assert f.verdict is Verdict.OK


def test_no_gate_outcomes_recorded_is_ok_not_a_fault(env):
    (f,) = _findings(_run(env), _GATE_CID)
    assert f.verdict is Verdict.OK and "no gate_outcomes" in f.evidence


def test_malformed_gate_outcome_payload_is_skipped_not_fatal(env):
    env["store"].append_event(task_id="bad", type="gate_outcomes",
                              source="settle", payload_json="{not json")
    _seed_gate_outcomes(env, [{"gate_id": "verify", "consulted": True, "ok": True}], 22)
    (f,) = _findings(_run(env), _GATE_CID)
    assert f.verdict is Verdict.OK


# ---- project: ready-label vs acceptance-contract drift -------------------
# The label and the contract have separate lifecycles (spec 019 made the
# acceptance section load-bearing after issues were already graded ready) —
# doctor surfaces the whole labeled population; dispatch stays the hard gate.

_READY_CID = "project.backlog.ready_contract"


def test_ready_issue_without_acceptance_section_warns(env, tmp_path, monkeypatch):
    from devclaw.doctor import checks_project

    register_tmp_project(env["registry"], str(tmp_path / "wsrc"),
                         repo_url="https://github.com/o/r")
    monkeypatch.setattr(checks_project, "_list_ready_issues", lambda url, label: [
        {"number": 12, "body": "just prose, no contract section"},
        {"number": 13, "body": "## Done when\n- behavior holds"},
        {"number": 14, "body": "## Acceptance criteria\n- also fine"},
    ])
    (f,) = _findings(_run(env), _READY_CID)
    assert f.verdict is Verdict.WARN
    assert "#12" in f.evidence and "#13" not in f.evidence and "#14" not in f.evidence
    assert "regrade_intake" in f.remedy


def test_unlistable_ready_backlog_is_unknown_never_ok(env, tmp_path, monkeypatch):
    from devclaw.doctor import checks_project

    register_tmp_project(env["registry"], str(tmp_path / "wsrc2"),
                         repo_url="https://github.com/o/r2")
    monkeypatch.setattr(checks_project, "_list_ready_issues", lambda url, label: None)
    (f,) = _findings(_run(env), _READY_CID)
    assert f.verdict is Verdict.UNKNOWN


def test_no_repo_url_short_circuits_without_listing(env, tmp_path, monkeypatch):
    """The stubbed suite must never shell out to gh — repo_url=None is the
    fixture default, so the boundary staying uncalled is load-bearing."""
    from devclaw.doctor import checks_project

    register_tmp_project(env["registry"], str(tmp_path / "wsrc3"))

    def _boom(url, label):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("gh boundary called with no repo_url")

    monkeypatch.setattr(checks_project, "_list_ready_issues", _boom)
    (f,) = _findings(_run(env), _READY_CID)
    assert f.verdict is Verdict.OK and "no repo_url" in f.evidence
