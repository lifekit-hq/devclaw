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
from devclaw.state_store.problems import (
    ENV_DEFICIENCY_CATEGORY, ENV_DEFICIENCY_KIND, fingerprint_for,
)

from devclaw import config as _config
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
    # GH_TOKEN likewise: a developer machine exports one for `gh`, and the
    # delivery check would then probe api.github.com from the suite.
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # DEVCLAW_SELF_REPO for the same reason (spec 038 US2): the self-repo check
    # reads the ambient environment, so a host that exports it would flip the
    # seeded fault to OK and the guard would pass having checked nothing.
    monkeypatch.delenv("DEVCLAW_SELF_REPO", raising=False)
    # The suite IS the stubbed engine. Both credentials are required only by
    # the production engine (tinyspec durable-container-secrets); with them
    # deleted above, a clean instance stays healthy only under a dev/test
    # engine — the production seeded faults set ENGINE="" explicitly.
    monkeypatch.setattr(_config, "ENGINE", "stub")

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
    # spec 034: the host-side worker-memory blob is dropped at boot too
    db.execute("CREATE TABLE project_docs (scope_key TEXT, kind TEXT)")
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
    (pdocs,) = _findings(report, "instance.legacy.project_docs_table")
    (cursor,) = _findings(report, "instance.legacy.inbox_cursor_column")
    (lane,) = _findings(report, "instance.legacy.program_lane")
    assert docs.verdict is Verdict.FAIL and cursor.verdict is Verdict.FAIL
    assert pdocs.verdict is Verdict.FAIL and ".devclaw/" in pdocs.evidence
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


# Checks that read the workspace resolved it as ``Path(workspace_dir or "")``,
# which is ``Path(".")`` — the devclaw process's OWN checkout, carrying an
# AGENTS.md, a .specify/, a .git and a devclaw.json. A workspace-less project
# row is legal (registered before its clone exists), so those checks answered
# OK about devclaw itself under another project's id. Absent ⇒ UNKNOWN, never a
# verdict inferred from the host's tree.
@pytest.mark.parametrize("workspace_dir", [None, "/nonexistent/never-cloned"])
def test_workspaceless_project_is_never_judged_from_the_host_checkout(
    env, workspace_dir
):
    env["registry"].create(id="no-ws", name="no-ws", workspace_dir=workspace_dir)
    mine = [f for f in _run(env).findings if f.project_id == "no-ws"]
    assert mine, "a registered project must still be reported on"

    # The one loud verdict for this condition stays with preflight alone.
    (pre,) = [f for f in mine if f.check_id == "project.workspace.preflight"]
    assert pre.verdict is Verdict.FAIL

    # The defect was affirmative health, not the wording: every check that
    # reads the workspace must decline to judge rather than claim OK. Checks
    # sourced from the registry/goal store (links, issue refs, backlog) read no
    # workspace and stay legitimately OK — they are not in this set.
    for cid in ("project.manifest.presence", "project.markers.integrity",
                "project.scaffold.drift", "project.scaffold.tracked_state",
                "project.capabilities.undeclared"):
        (f,) = [x for x in mine if x.check_id == cid]
        assert f.verdict is Verdict.UNKNOWN, f"{cid} judged a workspace-less project"


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


# ---- the GitHub credential: present, live, and scoped ---------------------
#
# specs/tiny/github-credential-in-the-registry.md. The failure this seeds is
# not a 401 — `repo` alone makes delivery, intake and the issue doorway all
# work. It is the SILENT half: without `actions:read` the failing-job log read
# degrades to a note, the worker meets a red verdict with no evidence, and the
# only lever left in its reach is the CI definition (fs-431, 2026-09-08).


def _delivery(env, monkeypatch, *, token="ghp_dummyneverechoed", status=200, scopes=("repo",)):
    from devclaw.doctor import checks_instance as ci

    monkeypatch.setattr(_config, "ENGINE", "")
    monkeypatch.setenv(ci._DELIVERY_TOKEN.var, token)
    monkeypatch.setattr(
        ci, "_probe_github_scopes",
        lambda _t, timeout_s=5.0: (status, None if scopes is None else frozenset(scopes)),
    )
    return _findings(_run(env), "instance.delivery.token")


def test_delivery_token_without_actions_read_fails_loud(env, monkeypatch):
    (f,) = _delivery(env, monkeypatch, scopes=("repo", "workflow"))
    assert f.verdict is Verdict.FAIL
    assert "actions:read" in f.evidence and "actions:read" in (f.remedy or "")
    assert "ghp_dummyneverechoed" not in f.evidence  # never echo the value


def test_delivery_token_absent_in_production_fails(env, monkeypatch):
    from devclaw.doctor import checks_instance as ci

    monkeypatch.setattr(_config, "ENGINE", "")
    monkeypatch.delenv(ci._DELIVERY_TOKEN.var, raising=False)
    (f,) = _findings(_run(env), "instance.delivery.token")
    assert f.verdict is Verdict.FAIL and "boot_guard" in f.evidence


@pytest.mark.parametrize("kw,verdict", [
    ({"token": "not-a-github-token"}, Verdict.FAIL),      # shape
    ({"status": 401}, Verdict.FAIL),                      # revoked
    ({"status": None}, Verdict.UNKNOWN),                  # unreachable ⇒ never OK
    ({"scopes": None}, Verdict.UNKNOWN),                  # fine-grained: not stated
    ({"scopes": ("repo", "actions:read")}, Verdict.OK),
])
def test_delivery_token_verdicts(env, monkeypatch, kw, verdict):
    (f,) = _delivery(env, monkeypatch, **kw)
    assert f.verdict is verdict


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


def test_goal_checkouts_not_ignored_warns_with_onboard_remedy(env, tmp_path):
    """Boilerplate revision 2: the repo root must ignore .goals/ (per-goal
    checkouts live under the project workspace)."""
    ws = tmp_path / "ws-gc"
    register_tmp_project(env["registry"], str(ws))
    (f,) = _findings(_run(env), "project.goal_checkouts.ignored")
    assert f.verdict is Verdict.WARN and "onboard" in f.remedy
    (ws / ".gitignore").write_text("node_modules/\n.goals/\n")
    (f,) = _findings(_run(env), "project.goal_checkouts.ignored")
    assert f.verdict is Verdict.OK


def test_worker_memory_drift_warns_with_curate_remedy(env, tmp_path):
    """Seeded fault (spec 034 FR-008): the repo's committed ``.devclaw/``
    memory is advisory-checked — a dangling index line, an unindexed fact
    file, or an index past the soft maximum WARNs naming the item; absence
    and a consistent layout are OK; nothing is ever dropped or held."""
    from devclaw.doctor.checks_project import WORKER_MEMORY_INDEX_SOFT_MAX
    from devclaw.speckit_setup import ensure_worker_memory_seeded

    ws = tmp_path / "ws-mem"
    register_tmp_project(env["registry"], str(ws))
    ws.mkdir(exist_ok=True)
    (ok,) = _findings(_run(env), "project.worker_memory.health")
    assert ok.verdict is Verdict.OK and "not seeded" in ok.evidence
    # the onboard-PR seed: an empty, consistent index ⇒ ok; never re-seeded
    assert ensure_worker_memory_seeded(str(ws)) == ".devclaw/MEMORY.md"
    assert ensure_worker_memory_seeded(str(ws)) is None
    (ok,) = _findings(_run(env), "project.worker_memory.health")
    assert ok.verdict is Verdict.OK and "0 facts" in ok.evidence
    index = ws / ".devclaw" / "MEMORY.md"
    facts = ws / ".devclaw" / "memory"
    facts.mkdir()
    (facts / "tmpdir.md").write_text("# Private TMPDIR\n\nuse mktemp -d\n")
    index.write_text(index.read_text() + "- [Private TMPDIR](memory/tmpdir.md) — pytest tmpdir\n")
    (ok,) = _findings(_run(env), "project.worker_memory.health")
    assert ok.verdict is Verdict.OK and "1 facts" in ok.evidence
    # a dangling line + an unindexed file ⇒ one WARN naming both
    index.write_text(index.read_text() + "- [Gone](memory/gone.md) — deleted fact\n")
    (facts / "orphan.md").write_text("# Orphan\n")
    (f,) = _findings(_run(env), "project.worker_memory.health")
    assert f.verdict is Verdict.WARN
    assert "gone.md" in f.evidence and "orphan.md" in f.evidence and "PR" in f.remedy
    # past the soft maximum ⇒ curation smell, still WARN, still nothing dropped
    index.write_text(
        "## Facts\n" + "".join(
            f"- [f{i}](memory/f{i}.md) — hook\n" for i in range(WORKER_MEMORY_INDEX_SOFT_MAX + 1)
        )
    )
    for i in range(WORKER_MEMORY_INDEX_SOFT_MAX + 1):
        (facts / f"f{i}.md").write_text(f"# f{i}\n")
    (facts / "orphan.md").unlink()
    (facts / "tmpdir.md").unlink()
    (f,) = _findings(_run(env), "project.worker_memory.health")
    assert f.verdict is Verdict.WARN and "soft maximum" in f.evidence
    assert len(list(facts.glob("*.md"))) == WORKER_MEMORY_INDEX_SOFT_MAX + 1


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


def test_tracked_feature_json_is_flagged_as_merge_conflict_fuel(env, tmp_path):
    """Seeded fault (merge-on-close wedge class, 2026-09-02): a repo that
    tracks ``.specify/feature.json`` puts the per-checkout speckit pointer in
    every goal PR — every worker run rewrites it — so any two goal branches
    landing in sequence conflict on it. Doctor must WARN naming the file and
    the untrack remedy; the scaffold-gitignored (untracked) pointer is OK."""
    import subprocess

    from devclaw.speckit_setup import scaffold_specify

    ws = tmp_path / "ws-fj"
    register_tmp_project(env["registry"], str(ws))
    scaffold_specify(str(ws))
    pointer = ws / ".specify" / "feature.json"
    pointer.write_text('{"feature_directory": "specs/001-x"}')

    def git(*args):
        subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t", "-c", "user.name=t",
                        *args], check=True, capture_output=True)

    git("init", "-q")
    # untracked (the scaffold's .specify/.gitignore covers it) ⇒ ok
    (ok,) = _findings(_run(env), "project.scaffold.tracked_state")
    assert ok.verdict is Verdict.OK
    # forced past the ignore and committed — the finance-sentry shape ⇒ warn
    git("add", "-f", ".specify/feature.json")
    git("commit", "-q", "-m", "track the pointer")
    (f,) = _findings(_run(env), "project.scaffold.tracked_state")
    assert f.verdict is Verdict.WARN and "feature.json" in f.evidence
    assert "git rm --cached" in f.remedy
    # the remedy applied ⇒ ok again
    git("rm", "-q", "--cached", ".specify/feature.json")
    (ok2,) = _findings(_run(env), "project.scaffold.tracked_state")
    assert ok2.verdict is Verdict.OK


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

    # #819 — the finance-sentry shape: the npm project is one level down, so a
    # root-only read reported "nothing visible" while the repo depended on
    # GitHub Packages. The nested file is evidence and names its own path.
    (ws / "devclaw.json").write_text('{"schemaVersion": 1, "boilerplateRevision": 1}')
    (ws / ".npmrc").unlink()
    (ws / "frontend").mkdir()
    (ws / "frontend" / ".npmrc").write_text(
        "//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}\n")
    (nested,) = _findings(_run(env), "project.capabilities.undeclared")
    assert nested.verdict is Verdict.WARN
    assert "frontend/.npmrc" in nested.evidence
    assert "registry:npm-github" in nested.remedy

    # A verify contract pointed at the private registry with no checked-in
    # .npmrc anywhere is the same undeclared dependency.
    (ws / "frontend" / ".npmrc").unlink()
    (ws / "devclaw.json").write_text(
        '{"schemaVersion": 1, "boilerplateRevision": 1, '
        '"verifyCmd": "npm ci --registry=https://npm.pkg.github.com && npm test"}'
    )
    (via_cmd,) = _findings(_run(env), "project.capabilities.undeclared")
    assert via_cmd.verdict is Verdict.WARN
    assert "verifyCmd" in via_cmd.evidence


def test_no_private_registry_dependency_is_ok(env, tmp_path):
    """A repo with no visible private-registry dependency declares nothing and
    is clean — the advisory must not nag every public-registry project."""
    ws = tmp_path / "ws-cap2"
    register_tmp_project(env["registry"], str(ws))
    (ws / "devclaw.json").write_text(
        '{"schemaVersion": 1, "boilerplateRevision": 1, '
        '"verifyCmd": "cd frontend && npm ci && npm test"}'
    )
    (ws / "frontend").mkdir()
    (ws / "frontend" / "package-lock.json").write_text('{"packages": {"": {"name": "x"}}}')

    # Running `npm ci` is not itself evidence — the public registry needs no
    # capability, and the remedy would be wrong advice.
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


@pytest.mark.parametrize("fault, named", [
    # a DB predating spec 032 — human verbs would be dropped and the
    # north-star metric reads unknown
    ("DROP TABLE goal_interventions", "goal_interventions"),
    # a DB predating the (goal, sha) key — every settle re-records the same
    # hand commit and non_worker_commits inflates (66 rows / 43 shas, 2026-09-08)
    ("DROP INDEX uq_goal_interventions_commit", "uq_goal_interventions_commit"),
])
def test_goal_interventions_shape_drift_detected(env, fault, named):
    """Seeded faults on the interventions ledger: FAIL, naming the missing
    shape, with the restart remedy."""
    db = env["store"]._db
    db.execute(fault)
    db.commit()
    (f,) = _findings(_run(env), "instance.scorecard.goal_interventions")
    assert f.verdict is Verdict.FAIL
    assert named in f.evidence and "restart" in f.remedy


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
    # (column, check id, spec 030)
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


def test_slice_hold_stranded_goal_detected(env):
    """Seeded fault: a goal parked on the RETIRED mechanical:slice_hold kind.

    The brake is gone, so no code path can heal that kind — the goal is
    stranded until a human resumes it. Only a deployed instance can be in this
    state (a restored backup, or a boot that released nothing), which is
    exactly the FR-014 class the stubbed suite cannot see.
    """
    db = env["store"]._db
    db.execute(
        "INSERT INTO goal_status (goal_id, phase, blocked_on, blocked_kind) "
        "VALUES ('stranded', 'blocked', 'dispatch held 5 consecutive ticks', "
        "'mechanical:slice_hold')"
    )
    db.commit()
    (f,) = _findings(_run(env), "instance.legacy.slice_hold_retired")
    assert f.verdict is Verdict.FAIL
    assert "slice_hold" in f.evidence and "resume_goal" in f.remedy


def test_slice_hold_retired_is_ok_on_a_clean_instance(env):
    (f,) = _findings(_run(env), "instance.legacy.slice_hold_retired")
    assert f.verdict is Verdict.OK and "retired" in f.evidence


def test_retired_cognition_problems_detected(env):
    """Seeded fault: a problems row naming a DELETED cognition role.

    `cognition/<role>` rows are keyed on the caller's role; the callers for
    goal_planner/summary/trend-detector are gone, so these rows can never be
    raised again — yet their LIFETIME count keeps them at the top of the
    default `ORDER BY count DESC` read. Only a deployed instance reaches this
    state (an interrupted boot, or a pre-retirement backup), the FR-014 class
    the stubbed suite cannot otherwise see.
    """
    from devclaw.state_store.problems import RETIRED_COGNITION_ROLES

    db = env["store"]._db
    for role in sorted(RETIRED_COGNITION_ROLES):
        db.execute(
            "INSERT INTO problems (fingerprint, category, kind, summary, "
            "sample_message, count, first_seen_ms, last_seen_ms) "
            "VALUES (?, 'cognition', ?, 's', 'm', 60, 1, 2)",
            (f"fp-{role}", role),
        )
    db.commit()
    (f,) = _findings(_run(env), "instance.legacy.retired_cognition_problems")
    assert f.verdict is Verdict.FAIL
    assert "goal_planner" in f.evidence and "restart devclaw" in f.remedy


def test_retired_cognition_problems_ok_on_a_clean_instance(env):
    (f,) = _findings(_run(env), "instance.legacy.retired_cognition_problems")
    assert f.verdict is Verdict.OK


def test_live_cognition_role_is_never_purged(env):
    """The purge is keyed on a DECLARED retired set, never on staleness — an
    ancient `cognition/evaluator` row stays, because that caller still exists.
    Pins the boundary the migration must not cross."""
    from devclaw.state_store.problems import RETIRED_COGNITION_ROLES

    assert "evaluator" not in RETIRED_COGNITION_ROLES
    assert "review" not in RETIRED_COGNITION_ROLES
    db = env["store"]._db
    db.execute(
        "INSERT INTO problems (fingerprint, category, kind, summary, "
        "sample_message, count, first_seen_ms, last_seen_ms) "
        "VALUES ('fp-live', 'cognition', 'evaluator', 's', 'm', 3, 1, 2)"
    )
    db.commit()
    (f,) = _findings(_run(env), "instance.legacy.retired_cognition_problems")
    assert f.verdict is Verdict.OK


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


def test_unset_self_repo_is_a_finding_only_once_a_gap_has_been_swallowed(env, monkeypatch):
    """Seeded fault (spec 038 US2): an instance holding worker-reported
    environment gaps with no self-repo files none of them — #818's silence,
    made visible on the instance that is actually losing filings rather than on
    every dev checkout. Driven across all three shapes on ONE workspace,
    because ``_findings`` unpacks a single finding per check id."""
    cid = "instance.env.self_repo_configured"
    gap = "dotnet-ef not available in the sandbox"

    # 1. unset, but nothing has been swallowed yet — the dev-checkout default.
    (f,) = _findings(_run(env), cid)
    assert f.verdict is Verdict.OK and "no unfiled worker-reported" in f.evidence

    # 2. a gap lands in the catalog while the instance still cannot file it.
    env["store"].record_problem(
        category=ENV_DEFICIENCY_CATEGORY, kind=ENV_DEFICIENCY_KIND,
        message=gap, recovered=False, goal_id="g", task_id="t1",
    )
    (f,) = _findings(_run(env), cid)
    assert f.verdict is Verdict.FAIL
    assert "DEVCLAW_SELF_REPO" in f.evidence and "holds 1 worker-reported" in f.evidence
    assert "DEVCLAW_SELF_REPO" in f.remedy

    # 3. a gap that WAS filed is not counted as lost — the finding must not
    # assert a loss that did not happen.
    env["store"].set_problem_issue(
        fingerprint_for(ENV_DEFICIENCY_CATEGORY, ENV_DEFICIENCY_KIND, gap),
        issue_number=7, issue_state="open",
    )
    (f,) = _findings(_run(env), cid)
    assert f.verdict is Verdict.OK

    # 4. configured — an unfiled gap is fine again, because it will be filed.
    env["store"].set_problem_issue(
        fingerprint_for(ENV_DEFICIENCY_CATEGORY, ENV_DEFICIENCY_KIND, gap),
        issue_number=None, issue_state=None,
    )
    monkeypatch.setenv("DEVCLAW_SELF_REPO", "lifekit-hq/devclaw")
    (f,) = _findings(_run(env), cid)
    assert f.verdict is Verdict.OK and "is set" in f.evidence


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


@pytest.mark.parametrize("engine, verdict", [("", Verdict.FAIL), ("host", Verdict.OK), ("stub", Verdict.OK)])
def test_registry_token_unset_fails_in_production_never_probes(env, monkeypatch, engine, verdict):
    """Required instance-wide (tinyspec durable-container-secrets): unset is
    FAIL under the production engine — the 2026-09-03 recreate left it blank
    while doctor said OK and a worker burned a session on an `npm ci` 401.
    Dev/test engines need no credential. The unset path never probes."""
    def _boom(token, timeout_s=5.0):  # pragma: no cover - must not run
        raise AssertionError("probed on the unset path")

    from devclaw.doctor import checks_instance as ci

    monkeypatch.setattr(ci, "_probe_registry_token", _boom)
    monkeypatch.setattr(_config, "ENGINE", engine)
    (f,) = _findings(_run(env), _REG_CID)
    assert f.verdict is verdict and "not set" in f.evidence
    if verdict is Verdict.FAIL:
        assert "deploy" in f.remedy  # the same fix the boot guard names


@pytest.mark.parametrize("engine, verdict", [("", Verdict.FAIL), ("host", Verdict.OK), ("stub", Verdict.OK)])
def test_setup_token_unset_fails_in_production(env, monkeypatch, engine, verdict):
    """Same rule for the OAuth setup-token: absence in production is the
    revocable-mounted-login posture, never OK."""
    monkeypatch.setattr(_config, "ENGINE", engine)
    (f,) = _findings(_run(env), "instance.auth.setup_token")
    assert f.verdict is verdict and "not set" in f.evidence
    if verdict is Verdict.FAIL:
        assert "deploy" in f.remedy


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


def test_contract_pins_table_absent_detected(env):
    """Seeded fault (spec-016 FR-014, spec 035): goal_contract_pins dropped →
    the DB predates the pin; every done-gate round re-decomposes the contract
    (the rubric-drift class the pin exists to kill)."""
    db = env["store"]._db
    db.execute("DROP TABLE goal_contract_pins")
    db.commit()
    (f,) = _findings(_run(env), "instance.donegate.contract_pins")
    assert f.verdict is Verdict.FAIL
    assert "goal_contract_pins" in f.evidence and "restart" in f.remedy


def test_contract_pins_corrupt_row_detected(env):
    """Seeded fault (spec 035 FR-008): an unparseable pin row — recurring
    corruption means a second writer outside the GoalStore seam."""
    db = env["store"]._db
    db.execute(
        "INSERT INTO goal_status (goal_id, version, updated_at) VALUES ('g-pin', 1, 1)"
    )
    db.execute(
        "INSERT INTO goal_contract_pins (goal_id, revision, clauses, pinned_at_ms)"
        " VALUES ('g-pin', 'abc123', 'not json', 1)"
    )
    db.commit()
    (f,) = _findings(_run(env), "instance.donegate.contract_pins")
    assert f.verdict is Verdict.FAIL
    assert "unparseable" in f.evidence and "g-pin" in f.evidence


def test_contract_pins_orphan_goal_detected(env):
    """Seeded fault (spec 035 FR-008): a pin row keyed to no goal_status row."""
    db = env["store"]._db
    db.execute(
        "INSERT INTO goal_contract_pins (goal_id, revision, clauses, pinned_at_ms)"
        " VALUES ('g-ghost', 'abc123', '[{\"id\": \"c1\", \"text\": \"t\"}]', 1)"
    )
    db.commit()
    (f,) = _findings(_run(env), "instance.donegate.contract_pins")
    assert f.verdict is Verdict.FAIL
    assert "g-ghost" in f.evidence


def test_contract_pins_present_is_ok(env):
    (f,) = _findings(_run(env), "instance.donegate.contract_pins")
    assert f.verdict is Verdict.OK


# ---- spec 039: loop-health tables + the silent worker usage source ---------


@pytest.mark.parametrize("fault, named", [
    ("DROP TABLE loop_spans", "loop_spans"),
    # spec 039 US6: the calibration columns are part of the same shape —
    # a goal_convergence predating them records every close as unpredicted
    ("ALTER TABLE goal_convergence RENAME COLUMN dispatches TO dispatches_old", "dispatches"),
    # deferred with US3 until the ledger existed to read it (2026-09-09):
    # without the column every close records its cost as unknown
    ("ALTER TABLE goal_convergence RENAME COLUMN cost_tokens TO cost_tokens_old", "cost_tokens"),
])
def test_loop_health_tables_absent_fails_with_restart_remedy(env, fault, named):
    """A DB predating spec 039 must not read as healthy: every loop-health
    metric would silently be unknown. Seeded faults: a table dropped, a
    calibration column missing."""
    env["store"]._db.execute(fault)
    env["store"]._commit()
    f = _findings(_run(env), "instance.loop_health.tables")
    assert f and f[0].verdict is Verdict.FAIL
    assert named in f[0].evidence and "restart" in (f[0].remedy or "")


def test_silent_worker_usage_source_warns(env):
    """Tasks settled this week with no worker usage row reported = the source
    the spec's live check found dead; doctor names it instead of letting cost
    per outcome read as a healthy zero."""
    tid = "t-usage-silent"
    env["store"].create_task(id=tid, kind="implement_feature", workspace_dir=str(env["tmp"]), goal="x")
    env["store"].claim_pending(tid)
    env["store"].mark_done(tid, json.dumps({"status": "ok"}))
    f = _findings(_run(env), "instance.loop_health.tables")
    assert f and f[0].verdict is Verdict.WARN
    assert "usage" in f[0].evidence
