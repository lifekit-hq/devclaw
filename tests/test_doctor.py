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
from devclaw.goal.project_id_cutoff import CUTOFF_META_KEY as PID_KEY
from devclaw.goal.store import GoalStore
from devclaw.goal.store.view_migration import MIGRATION_META_KEY as VIEW_KEY
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


def test_missing_migration_marker_is_a_fail(env):
    env["store"].delete_meta(VIEW_KEY)
    (f,) = [x for x in _findings(_run(env), "instance.migrations.meta_keys")
            if x.verdict is Verdict.FAIL]
    assert "goal view migration" in f.evidence
    assert "restart" in f.remedy


def test_pid_backfill_marker_missing_is_warn_not_fail(env):
    report = _run(env)  # fresh DB: server-boot backfill never ran
    warns = [x for x in _findings(report, "instance.migrations.meta_keys")
             if x.verdict is Verdict.WARN]
    assert warns and "backfill" in warns[0].evidence


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
    db.commit()
    report = _run(env)
    (docs,) = _findings(report, "instance.legacy.goal_docs_table")
    (cursor,) = _findings(report, "instance.legacy.inbox_cursor_column")
    assert docs.verdict is Verdict.FAIL and cursor.verdict is Verdict.FAIL


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
    # green everything the fixture doesn't already: backfill marker + window
    env["store"].set_meta(PID_KEY, str(NOW_MS))
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
