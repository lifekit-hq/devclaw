"""Skill-loader + hook-runner tests for the in-sandbox runner.

The runner concatenates markdown files baked into the sandbox image at
/opt/devclaw/skills/. Tests point `_SKILLS_DIR` at the in-repo source so the
loader is exercised against the same files that get baked.
"""

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _REPO_ROOT / "runner" / "runner.py"
_SKILLS_SRC = _REPO_ROOT / "runner" / "skills"
_HOOKS_SRC = _REPO_ROOT / "runner" / "hooks"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_skills_under_test", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def skill_dir(runner, monkeypatch):
    monkeypatch.setattr(runner, "_SKILLS_DIR", str(_SKILLS_SRC))
    return _SKILLS_SRC


@pytest.fixture
def hook_dir(runner, monkeypatch):
    monkeypatch.setattr(runner, "_HOOKS_DIR", str(_HOOKS_SRC))
    return _HOOKS_SRC


# ---- _load_skills behavior --------------------------------------------------


def test_common_skill_loads_for_every_kind(runner, skill_dir):
    for kind in ("implement_feature", "fix_bug", "review_repository", "onboard"):
        bundle = runner._load_skills(kind)
        assert "Common operating context" in bundle
        assert "AGENTS.md" in bundle


def test_writes_code_tier_loads_for_code_writing_kinds_only(runner, skill_dir):
    for kind in ("implement_feature", "fix_bug"):
        bundle = runner._load_skills(kind)
        assert "Quality bar" in bundle
        assert "Verify-gate coverage" in bundle
        assert "Commit hygiene" in bundle
    for kind in ("review_repository", "onboard"):
        bundle = runner._load_skills(kind)
        assert "Quality bar" not in bundle
        assert "Commit hygiene" not in bundle


# ---- doctrine (always-on) vs craft (self-selected) split --------------------


def test_craft_frontend_design_absent_from_always_on_brief(runner, skill_dir):
    """frontend-design is CRAFT (self-selected), not doctrine — its how-to body
    must NOT be concatenated into the always-on brief for ANY kind. Absence is
    only meaningful if the marker really exists in the craft file, so assert
    both (per rules/testing.md)."""
    marker = "Distinctive, not templated"
    craft_file = skill_dir / "craft" / "frontend-design.md"
    assert marker in craft_file.read_text(encoding="utf-8")  # marker is real
    for kind in ("implement_feature", "fix_bug", "review_repository", "onboard"):
        assert marker not in runner._load_skills(kind)


def test_craft_playwright_howto_absent_from_always_on_brief(runner, skill_dir):
    """The Playwright how-to moved out of the _writes-code doctrine tier into
    craft/. Its reference body (the config layout, "What you have") must be
    absent from the always-on brief; only the one-line pointer in the
    verify-gate-coverage doctrine survives."""
    marker = "playwright.config.ts"
    craft_file = skill_dir / "craft" / "playwright.md"
    assert marker in craft_file.read_text(encoding="utf-8")  # marker is real
    for kind in ("implement_feature", "fix_bug"):
        assert marker not in runner._load_skills(kind)


def test_craft_self_selection_pointer_present_in_brief(runner, skill_dir):
    """_common tells every task where the self-selected craft guides live so the
    agent can `ls` + read the relevant one. Kept ls-based/dynamic so adding a
    new craft file needs no brief edit."""
    for kind in ("implement_feature", "fix_bug", "review_repository", "onboard"):
        bundle = runner._load_skills(kind)
        assert "/opt/devclaw/skills/craft/" in bundle
        assert "frontend-design" in bundle  # named example
        assert "playwright" in bundle       # named example


def test_craft_files_exist_and_are_well_formed(runner, skill_dir):
    """The craft dir ships the two reference guides, each a non-empty markdown
    doc with a top-level heading."""
    craft_dir = skill_dir / "craft"
    names = {p.name for p in craft_dir.glob("*.md")}
    assert {"frontend-design.md", "playwright.md"} <= names
    for p in craft_dir.glob("*.md"):
        text = p.read_text(encoding="utf-8").strip()
        assert text, f"{p.name} is empty"
        assert text.startswith("# "), f"{p.name} lacks a top-level heading"


def test_craft_stays_out_of_the_always_on_brief(runner, skill_dir):
    """The doctrine/craft split kept the read-when-relevant craft/ how-tos
    (Playwright, frontend-design ≈ 4.8k chars combined) OUT of the always-on
    brief. This guards against craft/ silently getting re-concatenated, which
    would balloon the brief past ~17k. The ceiling sits well below that while
    leaving headroom for genuinely-added always-on doctrine (e.g. the #354
    repo-gate-conflict skill grew it to ~9.4k, then the demolition-P2 PLAN.md
    skill to ~12.2k, both on purpose)."""
    brief = runner._load_skills("implement_feature")
    # ~12.9k after spec 021's doctrine additions; craft re-concatenation
    # (≈ +4.8k) would push it past this ceiling.
    assert len(brief) < 13_600


def test_writes_code_brief_stays_lean_after_spoonfeeding_cut(runner, skill_dir):
    """Locks the 2026-08-05 worker-brief shrink (trust the agent, shrink the
    prompt): the general-engineering spoon-feeding in _common / quality-bar /
    verify-iterate was compressed (~12.2k → ~10.9k) while every devclaw-specific
    contract + guardrail (verify-gate coverage, repo-gate-conflict, the
    durable-memory skill — PLAN.md then, speckit artifacts now — commit) stayed. A regression above this ceiling means the spoon-feeding prose
    crept back — recompress it, don't raise the bar. (Ceiling lifted 11.4k →
    12.6k for the #508 doctrine additions — judgment-call return contract,
    precedent rule, one-shot scope bound — genuine doctrine, not prose creep.
    Lifted again 12.6k → 13.2k for spec 021: the harness-enforced one-slice
    contract (US1) and the per-slice read budget (US3) — the context-budget
    invariant's worker-facing half, genuine doctrine.)"""
    brief = runner._load_skills("implement_feature")
    assert len(brief) < 13_200
    # the guardrails the compression must never drop (the anti-#358 rules + the
    # pull-doctrine live on regardless of how tight the prose gets)
    assert "Never weaken or delete an existing test" in brief
    assert "no-op code" in brief
    assert "AGENTS.md" in brief


def test_feature_slice_doctrine_caps_agents_md_to_keep_honest_never_create(runner, skill_dir):
    """#552: feature PRs shipped a freshly-authored AGENTS.md alongside the
    feature. The doctrine is capped: update AGENTS.md only when the shipped
    change makes it wrong; NEVER create it from scratch — authoring belongs to
    onboarding. Absence is proven against the raw templates first (per
    rules/testing.md)."""
    # the old author-from-scratch instruction is gone from every raw source file
    doctrine_files = [skill_dir / "_common.md",
                      *sorted((skill_dir / "_writes-code").glob("*.md"))]
    for path in doctrine_files:
        assert "missing, create it" not in path.read_text(encoding="utf-8"), path.name
    # the cap ships in the writes-code tier (markers proven real in the raw file)
    cap_text = (skill_dir / "_writes-code" / "07-agents-md-honesty.md").read_text(
        encoding="utf-8"
    )
    assert "NEVER create AGENTS.md" in cap_text
    assert "NEVER append" in cap_text
    assert "THIN, BOUNDED pointer" in cap_text
    for kind in ("implement_feature", "fix_bug"):
        bundle = runner._load_skills(kind)
        assert "NEVER create AGENTS.md" in bundle
        assert "NEVER append" in bundle           # no learnings/feature notes/history
        assert "THIN, BOUNDED pointer" in bundle  # the file never grows
        assert "only when the change you shipped makes it wrong" in bundle
        assert "missing, create it" not in bundle


def test_onboard_brief_keeps_agents_md_authoring_uncapped(runner, skill_dir):
    """The never-create cap is feature/fix doctrine only — onboarding IS the
    authoring path and must not receive a contradicting rule (the cap lives in
    the _writes-code tier, which onboard never loads)."""
    bundle = runner._load_skills("onboard")
    assert "AGENTS.md" in bundle  # onboarding still produces it
    assert "NEVER create AGENTS.md" not in bundle
    assert "NEVER create AGENTS.md" not in runner._load_skills("review_repository")


def test_onboard_skill_is_three_docs_with_managed_markers(runner, skill_dir):
    """#552 adopt-over-build: the onboard skill authors exactly three docs —
    thin marker-delimited AGENTS.md + README.md + ARCHITECTURE.md. DECISIONS.md
    is retired (the speckit spec is the decision memory) — absence proven
    against the raw template first. The marker pair is upstream spec-kit's
    replace-within-markers upsert convention."""
    raw = (skill_dir / "onboard" / "00-onboard.md").read_text(encoding="utf-8")
    assert "DECISIONS.md" not in raw
    assert "<!-- devclaw:managed:start -->" in raw
    assert "<!-- devclaw:managed:end -->" in raw
    assert "THIN, BOUNDED pointer" in raw
    bundle = runner._load_skills("onboard")
    assert "DECISIONS.md" not in bundle
    assert "devclaw:managed:start" in bundle
    assert "preserves everything outside" in bundle


def test_common_skill_instructs_reading_architecture_map(runner, skill_dir):
    """Spec 027: the always-on _common.md must instruct workers to read
    ARCHITECTURE.md when it exists, for every task kind. Presence is proven
    against the raw source first (per rules/testing.md — both presence AND
    absence assertions need the marker to actually be in the file)."""
    raw = (skill_dir / "_common.md").read_text(encoding="utf-8")
    assert "ARCHITECTURE.md" in raw  # marker is real in the source
    for kind in ("implement_feature", "fix_bug", "review_repository", "onboard"):
        bundle = runner._load_skills(kind)
        assert "ARCHITECTURE.md" in bundle, (
            f"kind={kind!r}: _common.md ARCHITECTURE.md instruction missing from bundle"
        )


def test_fix_bug_keeps_its_smallest_change_skill(runner, skill_dir):
    bundle = runner._load_skills("fix_bug")
    assert "smallest change" in bundle.lower()


def test_fix_bug_loads_diagnosis_loop_after_scope(runner, skill_dir):
    """The diagnosis-loop discipline ships in the fix_bug tier, ordered after
    the scope skill (00- prefix before 10-) so scope framing comes first."""
    bundle = runner._load_skills("fix_bug")
    assert "Diagnosis loop" in bundle
    assert "red-capable" in bundle
    assert bundle.index("Bug-fix scope") < bundle.index("Diagnosis loop")
    for kind in ("implement_feature", "review_repository", "onboard"):
        assert "Diagnosis loop" not in runner._load_skills(kind)


def test_review_repository_loads_only_read_only_skill(runner, skill_dir):
    bundle = runner._load_skills("review_repository")
    assert "READ ONLY" in bundle
    assert "Commit hygiene" not in bundle  # no code-writing tier


def test_onboard_loads_agents_md_doctrine(runner, skill_dir):
    bundle = runner._load_skills("onboard")
    assert "ONBOARDING" in bundle
    assert "AGENTS.md" in bundle


def test_skill_blocks_are_separated_by_horizontal_rule(runner, skill_dir):
    bundle = runner._load_skills("implement_feature")
    # the loader joins with "\n\n---\n\n" so each skill is clearly delimited
    assert "\n\n---\n\n" in bundle


# ---- per-repo .agent/skills/ loading (D11) ----------------------------------


def test_per_repo_skill_appended_when_workspace_provided(runner, skill_dir, tmp_path):
    """A repo can carry observations the universal skills can't (e.g.
    "App.tsx is a 1827-line monolith") in <workspace>/.agent/skills/. The
    worker should see both the universal doctrine AND the per-repo notes."""
    repo_skills = tmp_path / ".agent" / "skills"
    repo_skills.mkdir(parents=True)
    (repo_skills / "frontend-structure.md").write_text(
        "# Frontend structure\n\nApp.tsx is a known 1827-line monolith.\n",
        encoding="utf-8",
    )
    bundle = runner._load_skills("implement_feature", workspace_dir=str(tmp_path))
    # universal skill present
    assert "Quality bar" in bundle
    # per-repo skill present too
    assert "1827-line monolith" in bundle


def test_per_repo_skill_loads_writes_code_tier(runner, skill_dir, tmp_path):
    """Per-repo _writes-code/ skills should load for code-writing kinds, matching
    the universal layout so a repo can add its own per-kind overrides."""
    repo_writes = tmp_path / ".agent" / "skills" / "_writes-code"
    repo_writes.mkdir(parents=True)
    (repo_writes / "20-repo-rule.md").write_text(
        "# Repo-specific rule\n\nNever import lodash.\n",
        encoding="utf-8",
    )
    for kind in ("implement_feature", "fix_bug"):
        bundle = runner._load_skills(kind, workspace_dir=str(tmp_path))
        assert "Never import lodash" in bundle
    # read-only kinds skip the writes-code tier per the existing rule
    bundle = runner._load_skills("review_repository", workspace_dir=str(tmp_path))
    assert "Never import lodash" not in bundle


def test_per_repo_skill_universal_comes_first(runner, skill_dir, tmp_path):
    """Universal devclaw doctrine appears BEFORE per-repo notes — the repo
    leans on what the agent already knows, not the other way around."""
    repo_skills = tmp_path / ".agent" / "skills"
    repo_skills.mkdir(parents=True)
    (repo_skills / "_common.md").write_text(
        "# Per-repo common\n\nREPO-COMMON-MARKER\n", encoding="utf-8",
    )
    bundle = runner._load_skills("implement_feature", workspace_dir=str(tmp_path))
    assert bundle.index("Common operating context") < bundle.index("REPO-COMMON-MARKER")


def test_per_repo_skill_missing_dir_is_silent(runner, skill_dir, tmp_path):
    """A workspace with no .agent/skills/ must not crash; loader returns
    just the universal bundle."""
    bundle = runner._load_skills("implement_feature", workspace_dir=str(tmp_path))
    assert "Quality bar" in bundle  # universal still there


def test_load_skills_default_arg_keeps_legacy_behavior(runner, skill_dir):
    """The workspace_dir kwarg defaults to None so the loader stays
    backward-compatible — universal-only bundle, unchanged."""
    bundle = runner._load_skills("implement_feature")
    assert "Quality bar" in bundle


# ---- _wrap_goal integration -------------------------------------------------


def test_wrap_goal_uses_skills_when_dir_present(runner, skill_dir):
    wrapped = runner._wrap_goal("implement_feature", "GOAL-TOKEN")
    assert "Common operating context" in wrapped
    assert "## Goal" in wrapped
    # goal rides along after the skills; the return contract is the final section
    assert "GOAL-TOKEN" in wrapped
    assert wrapped.index("## Goal") < wrapped.index("GOAL-TOKEN") < wrapped.index("STATUS:")


def test_wrap_goal_appends_return_contract_on_skills_path(runner, skill_dir):
    # Even with the baked skills loaded (production path), the structured
    # hand-back is appended after ## Goal so the engineer's result is legible.
    wrapped = runner._wrap_goal("implement_feature", "GOAL-TOKEN")
    for field in ("STATUS:", "CHANGED:", "VERIFIED:", "ACCEPTANCE:", "FOLLOW-UPS:"):
        assert field in wrapped
    # read-only kinds keep their own report contract — no code hand-back
    assert "FOLLOW-UPS:" not in runner._wrap_goal("review_repository", "x")


def test_wrap_goal_refuses_loudly_when_the_skill_bundle_is_missing(
    runner, monkeypatch, tmp_path
):
    """Named regression (#613): no skills ⇒ raise, never substitute text.

    There used to be a second copy of every instruction embedded in runner.py
    that rendered here instead. It was not a harmless fallback: a prompt edit
    could land in the copy production never reads while the canonical skill
    said something else, with nothing to tell them apart (#610). Briefing a
    worker that then runs unattended on substitute text is exactly the silent
    degradation the hardening philosophy forbids.
    """
    monkeypatch.setattr(runner, "_SKILLS_DIR", str(tmp_path / "nonexistent"))
    with pytest.raises(RuntimeError) as exc:
        runner._wrap_goal("implement_feature", "GOAL-TOKEN")
    # actionable: names the kind, the path, and which side is mis-wired
    assert "implement_feature" in str(exc.value)
    assert "DEVCLAW_SKILLS_DIR" in str(exc.value)


def test_wrap_goal_threads_workspace_dir_to_per_repo_skills(runner, skill_dir, tmp_path):
    """The full integration path: a repo carrying .agent/skills/ + a wrapped
    goal must produce a prompt containing BOTH the universal skill and the
    per-repo observation — proving D11 is wired end-to-end through _wrap_goal."""
    repo_skills = tmp_path / ".agent" / "skills"
    repo_skills.mkdir(parents=True)
    (repo_skills / "structure.md").write_text(
        "# Repo structure\n\nREPO-OBSERVATION-MARKER\n", encoding="utf-8",
    )
    wrapped = runner._wrap_goal(
        "implement_feature", "GOAL-TOKEN", workspace_dir=str(tmp_path),
    )
    assert "Quality bar" in wrapped  # universal reaches the prompt
    assert "REPO-OBSERVATION-MARKER" in wrapped  # so does per-repo
    assert "GOAL-TOKEN" in wrapped  # goal still rides along (return contract is last)
    assert wrapped != "GOAL-TOKEN"
    assert "AGENTS.md" in wrapped  # from embedded _CONTEXT_PREAMBLE


# ---- _run_hook behavior (universal + per-repo) ------------------------------


def test_run_hook_returns_empty_when_nothing_exists(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_HOOKS_DIR", str(tmp_path / "nonexistent"))
    warnings = runner._run_hook("pre-run", str(tmp_path), "implement_feature", "task-id")
    assert warnings == []


def test_pre_run_hook_executes(runner, hook_dir, tmp_path):
    # workspace must exist; pre-run snapshots HEAD if git, otherwise quiet.
    ws = tmp_path / "ws"
    ws.mkdir()
    warnings = runner._run_hook("pre-run", str(ws), "implement_feature", "task-id")
    # non-git workspace → no snapshot → no warnings on happy path
    assert all("fatal" not in w.lower() for w in warnings)


def test_the_post_run_hook_no_longer_computes_its_own_view_of_the_change(runner, hook_dir, tmp_path):
    """Spec 013 / #630. The hook used to answer "what did the agent change?"
    itself, from a `.devclaw-pre-head` sidecar, and had the same blind spot as
    the gates: it once reported "AGENTS.md exists but was not updated this run"
    on a run that had just CREATED AGENTS.md. Its three checks were RELOCATED to
    the host (``devclaw/quality/change_advisories.py``), which reads the one
    materialized span — not copied, because a second component doing the same
    trick is how the views drift apart again."""
    import subprocess as sp

    ws = tmp_path / "ws"
    ws.mkdir()
    sp.run(["git", "init", "-q", str(ws)], check=True)
    sp.run(["git", "-C", str(ws), "config", "user.email", "t@t"], check=True)
    sp.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True)
    (ws / "README.md").write_text("x")
    sp.run(["git", "-C", str(ws), "add", "."], check=True)
    sp.run(["git", "-C", str(ws), "commit", "-q", "-m", "init"], check=True)
    (ws / "e2e").mkdir()
    (ws / "e2e" / "smoke.spec.ts").write_text("test('x', () => {});")

    warnings = runner._run_hook(
        "post-run", str(ws), "implement_feature", "task-id", "pytest -q"
    )
    assert warnings == []
    # and it leaves no base-reference sidecar of its own behind
    assert not (ws / ".devclaw-pre-head").exists()


def test_the_pre_run_hook_writes_no_base_reference_sidecar(runner, hook_dir, tmp_path):
    """One definition of the change means one owner of where it starts: the
    host's ``tasks.pre_run_sha``. The worker layer kept its own copy so the
    post-run hook could diff against it; both are gone (spec 013)."""
    import subprocess as sp

    ws = tmp_path / "ws2"
    ws.mkdir()
    sp.run(["git", "init", "-q", str(ws)], check=True)
    runner._run_hook("pre-run", str(ws), "implement_feature", "task-id")
    assert not (ws / ".devclaw-pre-head").exists()


def test_per_repo_hook_runs_alongside_universal(runner, hook_dir, tmp_path):
    # When the workspace ships its own .agent/hooks/<name>.sh, runner fires the
    # universal hook AND the per-repo hook; both contribute to the warning list
    # with distinct tags so the goal layer can tell them apart.
    ws = tmp_path / "ws"
    (ws / ".agent" / "hooks").mkdir(parents=True)
    repo_hook = ws / ".agent" / "hooks" / "pre-run.sh"
    repo_hook.write_text("#!/usr/bin/env bash\necho 'repo-pre-run-fired'\n")
    repo_hook.chmod(0o755)

    warnings = runner._run_hook("pre-run", str(ws), "implement_feature", "task-id")
    repo_warnings = [w for w in warnings if w.startswith("[pre-run:repo]")]
    assert repo_warnings, f"expected per-repo warning, got: {warnings}"
    assert "repo-pre-run-fired" in repo_warnings[0]


def test_per_repo_hook_missing_does_not_crash(runner, hook_dir, tmp_path):
    # Workspaces WITHOUT .agent/hooks/ still work — per-repo layer is purely
    # opt-in, no warnings emitted on its behalf.
    ws = tmp_path / "ws"
    ws.mkdir()
    warnings = runner._run_hook("pre-run", str(ws), "implement_feature", "task-id")
    repo_warnings = [w for w in warnings if "[pre-run:repo]" in w]
    assert repo_warnings == []


def test_common_skill_mentions_per_repo_skills(runner, skill_dir):
    # The _common skill is what tells the agent to ls .agent/skills/ — that's
    # the entire discovery mechanism for the per-repo layer.
    bundle = runner._load_skills("implement_feature")
    assert ".agent/skills/" in bundle
    assert "PROJECT-OWNED" in bundle
