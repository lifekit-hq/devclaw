"""Onboarding tests — generate a DRAFT doc set on first touch of a repo.

`onboard` is a task kind that mirrors the `review_repository` read-only path
but is allowed to write THREE docs (a thin marker-delimited AGENTS.md pointer,
README.md, ARCHITECTURE.md) so both agent + human have onboarding
infrastructure. DECISIONS.md is retired (#552): under speckit the spec is the
decision memory, so no parallel ADR log is authored. The draft is
human-reviewed (not authoritative until then), so when a doc already exists
the agent validates-and-keeps rather than clobbering.

Two layers, both SDK-/docker-/claude-free: the goal-wrapper prompt (runner) and
the TaskQueue wiring (a stub engine stands in for the sandbox worker).
"""

import importlib.util
import os
from pathlib import Path

import pytest

from devclaw.engine import EngineRequest
from devclaw.queue import settle as queue_settle
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_under_test", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top-level only; main() (and its SDK imports) is not run
    return mod


# ---- the onboard goal-wrapper ----------------------------------------------


def test_onboard_writes_exactly_three_docs_no_decisions_md(runner):
    """#552 adopt-over-build: the onboard doc set is AGENTS.md + README.md +
    ARCHITECTURE.md. DECISIONS.md is retired — the speckit spec is the decision
    memory. Absence is proven against the raw wrapper template first."""
    assert "DECISIONS.md" not in runner._load_skills("onboard")  # raw skill bundle
    wrapped = runner._wrap_goal("onboard", "FOCUS-TOKEN")
    # each of the three docs is named so the LLM knows the concrete filenames
    for doc in ("AGENTS.md", "README.md", "ARCHITECTURE.md"):
        assert doc in wrapped, doc
    assert "DECISIONS.md" not in wrapped
    assert "three documents" in wrapped.lower()
    # each doc is marked DRAFT until a human reviews it
    assert "DRAFT" in wrapped
    # the optional focus still rides along
    assert "FOCUS-TOKEN" in wrapped


def test_onboard_authors_agents_md_as_marker_delimited_thin_pointer(runner):
    """The AGENTS.md onboard authors is a THIN, BOUNDED pointer written inside
    the devclaw:managed marker pair — a re-onboard replaces within the markers
    and preserves everything outside them (upstream spec-kit's marker-based
    upsert convention)."""
    wrapped = runner._wrap_goal("onboard", "")
    assert "THIN, BOUNDED pointer" in wrapped
    assert "<!-- devclaw:managed:start -->" in wrapped
    assert "<!-- devclaw:managed:end -->" in wrapped
    assert "REPLACES" in wrapped
    assert "preserves everything outside" in wrapped


def test_onboard_is_read_only_except_the_onboarding_artifacts(runner):
    wrapped = runner._wrap_goal("onboard", "")
    assert "READ ONLY" in wrapped
    # writes limited to the onboarding artifact set (three docs + the
    # dev-container Dockerfile) — every other file (source, build, config) is
    # off-limits so onboarding never mutates behaviour.
    assert "EXCEPT the onboarding artifacts" in wrapped
    assert ".devcontainer/Dockerfile" in wrapped
    assert "do not change" in wrapped or "Do NOT modify" in wrapped


def test_onboard_authors_devcontainer_dev_environment_when_absent(runner):
    """Onboarding establishes the project's dev-environment boilerplate — a
    `.devcontainer/Dockerfile` a human and the agent SHARE — created only when
    the repo has none (non-clobber, like the docs). It must be a DEV image (SDK
    present, not a slim prod image) and toolchain-only (no app COPY, no agent
    tooling) so devclaw can compose its harness on top."""
    wrapped = runner._wrap_goal("onboard", "")
    lower = wrapped.lower()
    assert ".devcontainer/Dockerfile" in wrapped
    # dev image, explicitly NOT a slim production image
    assert "dev image" in lower and "production" in lower
    # created only when absent — same non-clobber discipline as the docs
    assert "only when the repo has none" in lower or "leave it unchanged" in lower
    # composition contract: toolchain only, base debian, harness added by devclaw
    assert "toolchain only" in lower
    assert "debian" in lower


def test_onboard_agents_md_scope_is_pointer_not_narrative(runner):
    """The AGENTS.md contract: commands, layout pointers, links out — narrative
    and learnings are excluded (they live in ARCHITECTURE.md / .agent/skills/ /
    specs/)."""
    wrapped = runner._wrap_goal("onboard", "")
    for cue in ("what the repo is", "verify gate", "layout pointers",
                ".agent/skills/", "specs/"):
        assert cue in wrapped.lower() or cue in wrapped, cue
    assert "Do NOT put learnings, feature notes, decision rationale" in wrapped


def test_onboard_enforces_boundary_between_the_three_docs(runner):
    """Boundary discipline: each doc has one job — a project where README
    carries ADR content or ARCHITECTURE has quickstart commands is
    undocumented in practice because the reader can't rely on scope. Decision
    rationale cross-links the speckit spec, never a separate ADR log."""
    wrapped = runner._wrap_goal("onboard", "")
    assert "boundary discipline" in wrapped.lower()
    # README purpose: quickstart + purpose, NOT decision rationale
    assert "quickstart" in wrapped.lower()
    # ARCHITECTURE purpose: how the pieces fit, data flow — not quickstart
    assert "data flow" in wrapped.lower() or "component" in wrapped.lower()
    # decision memory = the spec; no parallel ADR log gets authored
    assert "separate ADR log" in wrapped


def test_onboard_keeps_substantive_existing_docs(runner):
    """No-clobber contract applies to any of the three docs, not just
    AGENTS.md. When a doc already exists AND is substantive, validate and
    keep what's accurate."""
    wrapped = runner._wrap_goal("onboard", "")
    assert "ALREADY exists" in wrapped
    assert "not clobber" in wrapped.lower() or "not blindly overwrite" in wrapped.lower()
    assert "keep what's accurate" in wrapped.lower() or "keep it" in wrapped.lower()


def test_onboard_kind_is_distinct_from_review(runner):
    assert runner._wrap_goal("onboard", "X") != runner._wrap_goal("review_repository", "X")


# ---- TaskQueue wiring (stub engine, no docker/claude) ----------------------


def _onboard_runner():
    """Stub engine that does what the real onboard agent does to the workspace:
    write AGENTS.md and report ok. No verify gate is involved for onboarding."""

    async def runner(req: EngineRequest):
        assert req.kind == "onboard"
        assert req.verify_cmd is None  # onboarding has no gate
        with open(os.path.join(req.workspace_dir, "AGENTS.md"), "w") as f:
            f.write("# DRAFT — generated by devclaw onboarding\n")
        return {
            "status": "ok",
            "workspaceDir": req.workspace_dir,
            "message": "onboarding completed.",
        }

    return runner


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


async def test_onboard_task_settles_done_and_writes_agents_md(store, tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    q = TaskQueue(store, runner=_onboard_runner())
    tid = q.submit(kind="onboard", workspace_dir=ws, goal="general onboarding")
    await q.drain()

    t = store.get_task(tid)
    assert t.status == "done"
    assert t.kind == "onboard"
    assert os.path.exists(os.path.join(ws, "AGENTS.md"))


async def test_onboard_task_has_no_verify_gate(store, tmp_path):
    """Onboarding is comprehension, not a code change: there is no verify gate
    to run, and a bare ``q.submit`` (no ``deliver=``) still defaults to no
    delivery — the queue's default is unchanged.

    This test used to also assert ``pr_url is None`` under the heading "no PR —
    the human reviews the draft". #598 overturned that policy, not this code
    path: the human still reviews the draft, but at the PR instead of in an
    untracked working tree that the next dispatch's ``git clean -fdx`` deletes.
    Delivery is decided by the ``onboard`` TOOL, which is what the three
    outcome tests below exercise; this one pins the queue default it rides on.
    """
    ws = str(tmp_path / "ws2")
    os.makedirs(ws)
    q = TaskQueue(store, runner=_onboard_runner())
    tid = q.submit(kind="onboard", workspace_dir=ws, goal="general onboarding")
    await q.drain()

    t = store.get_task(tid)
    assert t.verify_cmd is None
    assert t.deliver is False  # the QUEUE default, not the tool's choice


def test_onboard_kind_round_trips_through_the_store(store):
    store.create_task(id="ob1", kind="onboard", workspace_dir="/ws", goal="g")
    assert store.get_task("ob1").kind == "onboard"
    assert store.list_tasks(kind="onboard")[0].id == "ob1"


# ---- delivery: the three outcomes, driven through the onboard TOOL (#598) ---
#
# These go through `_tools.onboard(...)`, NOT `q.submit(deliver=True)`. The
# defect #598 records was one missing kwarg at that single tool call site, so a
# test that submits the task itself supplies the very thing under test and
# passes with the bug fully present — which is exactly how PR #610's version of
# this test passed twice while shipping nothing.


@pytest.fixture()
def onboard_env(store, tmp_path, monkeypatch):
    """Wire the real TaskQueue behind the real `onboard` tool, stubbing only the
    edges the tool crosses before submitting: project resolution, the workspace
    preflight, and the speckit check (adopt path)."""
    from devclaw import task_queue as _tq
    from devclaw.project_registry import ResolvedDispatch
    from devclaw.server import tools as _tools

    ws = str(tmp_path / "repo")
    os.makedirs(ws)
    # a CURRENT manifest, so onboard proceeds to the docs pass instead of
    # opening the spec-016 seed/migrate PR first
    from devclaw.project_manifest import seed_manifest

    seed_manifest(ws)
    q = TaskQueue(store, runner=_onboard_runner())

    monkeypatch.setattr(
        _tools.intake, "_resolve_project_or_reject",
        lambda project_id, tool: ResolvedDispatch(workspace_dir=ws, project_id=project_id),
    )

    async def _no_preflight(resolved, project_id):
        return None

    async def _has_speckit(workspace_dir):
        return True

    monkeypatch.setattr(_tools.intake, "_preflight_or_prep", _no_preflight)
    monkeypatch.setattr(_tools.intake._speckit, "has_committed_speckit", _has_speckit)
    monkeypatch.setattr(_tools.intake, "queue", q)
    monkeypatch.setattr(_tools.tasks, "store", store)
    return _tools, q, ws, _tq


async def test_onboard_delivers_its_artifacts_and_records_the_pr_url(onboard_env, monkeypatch):
    """The #598 defect itself: the run generated ARCHITECTURE.md and
    .devcontainer/Dockerfile, settled `done` with `error` null, and recorded
    `pr_url` null while both files sat untracked — one `git clean -fdx` from
    deletion. A successful onboard must commit what it generated and open a
    reviewable PR, with that URL on the task, exactly as implement_feature does.
    """
    import json as _json

    _tools, q, ws, _tq = onboard_env
    seen = {}

    async def _fake_deliver(**kwargs):
        seen.update(kwargs)
        return {"branch": "docs/onboarding", "pr_url": "https://github.com/o/r/pull/7"}

    monkeypatch.setattr(queue_settle, "deliver_change", _fake_deliver)

    out = _json.loads(await _tools.onboard("proj"))
    await q.drain()

    t = _tools.tasks.store.get_task(out["task_id"])
    assert t.deliver is True
    assert t.status == "done"
    assert t.pr_url == "https://github.com/o/r/pull/7"
    # It reused the shared seam rather than a second delivery path, and told it
    # the kind — `_KIND_TYPE["onboard"] == "docs"`, so the PR stays behind a
    # human merge.
    assert seen["kind"] == "onboard"
    assert seen["workspace_dir"] == ws


async def test_onboard_with_nothing_to_deliver_succeeds_without_a_pr(onboard_env, monkeypatch):
    """A re-onboard where every artifact is already accurate produces no change.
    That is a SUCCESS with no PR, and it must stay distinguishable from a
    delivery that broke — not collapsed into it."""
    import json as _json

    _tools, q, ws, _tq = onboard_env
    attempted = []

    async def _nothing_to_ship(**kwargs):
        # The exact string deliver_change writes when the tree is clean
        # (delivery/__init__.py) — one of the two prefixes _BENIGN_ERRORS
        # filters out. Using an invented phrase here would settle the task
        # FAILED, which is the very collapse this test exists to prevent.
        attempted.append(kwargs["task_id"])
        return {"error": "no changes to deliver"}

    monkeypatch.setattr(queue_settle, "deliver_change", _nothing_to_ship)

    out = _json.loads(await _tools.onboard("proj"))
    await q.drain()

    t = _tools.tasks.store.get_task(out["task_id"])
    assert t.status == "done"      # success...
    assert t.pr_url is None        # ...with no PR
    assert t.error is None         # and NOT the failure below

    # Load-bearing: "done with no PR" looks IDENTICAL whether delivery ran and
    # found nothing or was never attempted at all — which is the #598 bug. Pin
    # that it was attempted, or this test passes on the unfixed code.
    assert attempted == [t.id]
    assert t.deliver is True


async def test_onboard_that_cannot_deliver_settles_failed_not_done(onboard_env, monkeypatch):
    """#183's rule reaches onboard: a run that generated artifacts but could not
    ship them must settle `failed` with an actionable reason. Settling `done`
    without a PR is the false-green that made #598 self-concealing — every
    poller upstream reads such a row as shipped."""
    import json as _json

    _tools, q, ws, _tq = onboard_env

    async def _push_rejected(**kwargs):
        return {"error": "push failed: remote rejected"}

    monkeypatch.setattr(queue_settle, "deliver_change", _push_rejected)

    out = _json.loads(await _tools.onboard("proj"))
    await q.drain()

    t = _tools.tasks.store.get_task(out["task_id"])
    assert t.status == "failed"
    assert t.pr_url is None
    assert "push failed: remote rejected" in (t.error or "")
