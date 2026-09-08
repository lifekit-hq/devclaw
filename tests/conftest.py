"""Shared test fixtures + hermeticity guards."""

import asyncio
import os
import pathlib
import tempfile

import pytest

# Point the runner at the IN-REPO skill bundle, never the baked
# /opt/devclaw/skills/ installation (whose content changes independently of this
# branch). runner/skills/ is the one home for worker-kind instructions (#613) and
# is exactly what the sandbox image bakes, so a prompt assertion here is an
# assertion about what production actually sends. It used to default to a
# nonexistent path so the embedded _KIND_WRAPPERS fallback would fire — which
# meant the prompt tests pinned a copy production never read, and a prompt edit
# could land in the wrong one with every test still green (#610). Set at
# module-level (not in a fixture) so it is in effect when the module-scoped
# `runner` fixture executes exec_module and reads the env var.
# SET, never setdefault: CI runs on the same host devclaw is deployed to, so an
# ambient DEVCLAW_SKILLS_DIR would be adopted and point the suite straight at
# the baked bundle this pin exists to avoid — the #610 wrong-copy bug, silently
# restored by the guard against it. A hermeticity pin is an assertion about the
# suite's world, not a default for it.
os.environ["DEVCLAW_SKILLS_DIR"] = str(
    pathlib.Path(__file__).resolve().parents[1] / "runner" / "skills"
)

# Give the suite its own devclaw.db. `devclaw.server._state` builds a real
# StateStore/ProjectRegistry AT IMPORT TIME against DEVCLAW_DB, which defaults
# to `devclaw.db` relative to CWD — so any test importing `devclaw.server.tools`
# wrote a live database into the repo root. Under `-n auto` that one file is
# shared by every worker, which is how a schema migration became a race. One
# path per worker process (PYTEST_XDIST_WORKER is set before conftest imports;
# empty when running -n0). Module-level, not a fixture: import-time side
# effects need the env var already in place.
# SET, never setdefault — for TWO reasons, and setdefault defeated both.
#
# 1. Inheritance. Under `-n auto` the xdist CONTROLLER imports this file first
#    and execnet spawns every worker with the controller's os.environ, so a
#    worker's own setdefault sees the value already present and does nothing.
#    Every worker then opened the CONTROLLER's database — the suffix below read
#    `main`, never `gw3`, and the per-worker path this pin promises was dead
#    code. Sixteen processes sharing one file, while `devclaw.server._state`
#    builds a real StateStore AT IMPORT TIME, means sixteen concurrent
#    `PRAGMA journal_mode = WAL` on it: a lock race a quiet dev box wins and the
#    loaded VPS runner loses. The loser fails to IMPORT the module, its tests
#    vanish from that worker's collection, and xdist aborts the whole run on the
#    mismatch — which is what painted CI red from 2026-09-08 17:45 onward with
#    no commit responsible, tracking instance load instead.
# 2. Ambience. CI runs on the deploy host; an ambient DEVCLAW_DB would be
#    adopted and point the suite at a real database (same reasoning as
#    DEVCLAW_SELF_REPO below, which already got this right).
#
# mkdtemp is unique per process, so once the value is no longer inherited each
# worker gets its own file; the worker suffix stays as legible belt-and-braces.
os.environ["DEVCLAW_DB"] = str(
    pathlib.Path(tempfile.mkdtemp(prefix="devclaw-suite-"))
    / f"devclaw-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}.db"
)

# The instance's OWN repo slug. Since spec 038 this is load-bearing on a path
# the suite drives: a worker-reported environment deficiency files the gap
# through the issue doorway at the hold, and unset is what makes that a stated
# no-op that spawns nothing (FR-006). Inherited from the ambient environment it
# would flip a stubbed settle into a real `gh issue create` against a real
# repository — and CI runs on the same host devclaw is deployed to, so the
# suite would behave one way there and another in a sandbox. Cleared, never
# defaulted: tests that need it set it explicitly with monkeypatch.
os.environ.pop("DEVCLAW_SELF_REPO", None)

from devclaw import llm_call as _llm_call_mod
from devclaw import task_queue
from devclaw.delivery import deploy as _deploy_mod
from devclaw.engine import sandcastle as _sandcastle_mod


@pytest.fixture(autouse=True)
def _no_real_merges_by_default(monkeypatch):
    """Merge-on-close (spec 025) runs on every achieved done-gate close and
    shells the real ``gh`` CLI. A unit test driving a close must never reach
    the network (or a developer's authenticated gh) — default the seam to
    NO_PR, the nothing-to-merge success outcome, so every pre-025 close test
    keeps its exact behavior. Merge tests patch ``tick_donegate._attempt_merge``
    themselves with recording fakes."""
    from devclaw.goal import merge_on_close as _moc
    from devclaw.goal import tick_donegate as _tdg

    async def _no_pr(workspace_dir, branch):
        return _moc.MergeResult(_moc.MergeOutcome.NO_PR, detail="stubbed (conftest)")

    async def _no_sync(workspace_dir):
        return None

    monkeypatch.setattr(_tdg, "_attempt_merge", _no_pr)
    monkeypatch.setattr(_tdg, "_sync_workspace", _no_sync)


@pytest.fixture(autouse=True)
def _disable_review_gate_by_default(monkeypatch):
    """The pre-PR review gate's default reviewer shells out to the real `claude`
    CLI. On a developer machine that's authenticated, an un-injected TaskQueue in
    a test with a real git workspace would make a live, non-deterministic Claude
    call (and in CI it would just fail open). Keep the whole suite hermetic by
    defaulting the gate OFF; the review-gate tests re-enable it explicitly and
    inject a stub reviewer.
    """
    monkeypatch.setattr(task_queue, "REVIEW_GATE_ENABLED", False)


@pytest.fixture(autouse=True)
def _disable_sandbox_sweep_by_default(monkeypatch):
    """``TaskQueue.recover()`` sweeps orphaned sandbox containers via the real
    docker CLI. A test process is NOT the devclaw process: on a docker-enabled
    dev machine a live devclaw could be mid-task, and a test calling recover()
    must never ``docker rm -f`` its containers (the "any labeled container is
    orphaned" premise only holds for the real server's startup). Default the
    sweep to a no-op; the wiring test injects its own recording stub the same
    way, and the sweep's own unit tests patch its subprocess seam directly.
    """
    monkeypatch.setattr(task_queue, "sweep_orphan_sandboxes", lambda owner_id: 0)


#: Program basenames a test process must NEVER spawn for real. Built from the
#: same env-derived module constants production uses, so an exotic
#: ``DEVCLAW_DOCKER_BIN`` on a dev host is still caught.
_CONTAINER_BINARIES = frozenset(
    os.path.basename(b)
    for b in (
        "docker",
        "tailscale",
        _deploy_mod.DOCKER_BIN,
        _deploy_mod.TAILSCALE_BIN,
        _sandcastle_mod.DOCKER_BIN,
    )
)

#: The cognition binary — the quota. A test that reaches it burns a real
#: `claude --print` call on an authenticated dev machine and fails in CI
#: (no binary), and the two outcomes disagree, which is how a silent skip
#: turned fail-closed hid for a day (2026-09-06: the admission lint's judge).
#: Injected fakes (``FakeClaude``, a patched ``_judge_undecided``) never reach
#: the spawn; the opt-in live cognition evals lift the guard themselves.
_COGNITION_BINARIES = frozenset(
    os.path.basename(b) for b in ("claude", _llm_call_mod.CLAUDE_BIN)
)

#: ``gh <noun> <verb>`` verbs that only READ. Everything else is treated as a
#: write, because this guard must fail CLOSED like every other brake in the
#: system: a denylist of mutating verbs silently admits the next one anybody
#: adds (`secret set`, `workflow run`, `release upload`), and the cost of being
#: wrong is a change to a live repository that outlives the test that made it.
#: The price of the allowlist is that a NEW read has to be named here — a
#: loud, one-line failure with the fix in the message.
_GH_READ_VERBS = frozenset(("view", "list", "status", "checks", "diff"))

#: ``gh api`` flags that turn the default GET into a POST. `gh` does this
#: implicitly whenever a field is supplied, so a method flag is not the only
#: tell.
_GH_API_WRITE_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")

_GH_GUARD_HINT = (
    "The pytest suite is fully stubbed — a test must NEVER perform a real "
    "GitHub write (it lands in a live repository and outlives the test). Stub "
    "the seam your test reaches: patch `devclaw.issue_doorway.GhCli`, pass a "
    "fake `gh=` adapter, patch `devclaw.delivery.*._run`, or leave "
    "DEVCLAW_SELF_REPO unset so the filing path is the no-op it is by default. "
    "If this really is a READ, add its verb to _GH_READ_VERBS in conftest."
)


def _is_gh_write(base: str, args: tuple) -> bool:
    """True unless this ``gh`` invocation is a recognised read."""
    if base != "gh":
        return False
    strings = [str(a) for a in args]
    if not strings:
        return False  # bare `gh` prints help
    if strings[0] == "api":
        for i, a in enumerate(strings):
            if a.startswith("--method="):
                return a.split("=", 1)[1].upper() != "GET"
            if a.startswith("-X") and len(a) > 2:  # glued: -XPOST
                return a[2:].upper() != "GET"
            if a in ("-X", "--method") and i + 1 < len(strings):
                return strings[i + 1].upper() != "GET"
            if a in _GH_API_WRITE_FLAGS or a.startswith(("-f=", "-F=")):
                return True
        return False
    return not (len(strings) >= 2 and strings[1] in _GH_READ_VERBS)


_COGNITION_GUARD_HINT = (
    "The pytest suite is fully stubbed — a test must NEVER spawn the real "
    "`claude` CLI (it is the account's quota, and CI has no binary). Inject a "
    "caller at the seam your test reaches: `svc._evaluator_caller = "
    "FakeClaude(...)`, `evaluator_caller=`, `claude_caller=`, or patch the "
    "module-global (`service._judge_undecided`, `evaluator.default_caller`). "
    "Live cognition evals opt in with DEVCLAW_RUN_COGNITION_EVALS=1."
)

_GUARD_HINT = (
    "The pytest suite is fully stubbed — a test must NEVER launch real "
    "docker/tailscale (a 2026-07-14 pytest run leaked a live, "
    "restart-unless-stopped `devclaw-deploy-g` container on two hosts). "
    "Stub the chokepoint your test actually reaches instead: monkeypatch "
    "`devclaw.delivery.deploy._run` (or `deploy_project` where imported), "
    "patch `devclaw.engine.sandcastle._docker_run_sync`, or disable the "
    "feature (e.g. pass `autodeploy=False`) when the test's intent doesn't "
    "cover deploys."
)


@pytest.fixture(autouse=True)
def _block_real_docker(monkeypatch):
    """Suite-wide hermeticity guard: any attempt to spawn a real ``docker`` or
    ``tailscale`` subprocess fails the test LOUDLY.

    The guard sits at the process-spawn chokepoint (``asyncio.create_subprocess_exec``
    plus sandcastle's sync ``_docker_run_sync`` seam) rather than at each caller, so a
    NEW escape hatch is caught too. It raises via ``pytest.fail`` — a BaseException —
    on purpose: best-effort paths like ``tick_donegate._auto_deploy`` swallow
    ``Exception`` by design, which is exactly how the deploy leak stayed silent.

    Tests that inject fake runners (``deploy._run``, ``_docker_run_sync``) replace
    the guarded seam and never reach this wrapper; pure helpers (``deploy_name``,
    ``_build_run_args``, ``deploy_port``) spawn nothing and stay testable; every
    other subprocess (git, gh, python) passes through untouched.
    """
    real_exec = asyncio.create_subprocess_exec

    live_cognition = os.environ.get("DEVCLAW_RUN_COGNITION_EVALS", "0") not in ("0", "", "false", "False")

    async def guarded_exec(program, *args, **kwargs):
        base = os.path.basename(str(program))
        if base in _CONTAINER_BINARIES:
            pytest.fail(
                f"BLOCKED: test tried to spawn a real container-daemon subprocess: "
                f"{program} {' '.join(str(a) for a in args[:8])} ...\n{_GUARD_HINT}"
            )
        if base in _COGNITION_BINARIES and not live_cognition:
            pytest.fail(
                f"BLOCKED: test tried to spawn the real cognition binary: "
                f"{program} {' '.join(str(a) for a in args[:6])} ...\n{_COGNITION_GUARD_HINT}"
            )
        if _is_gh_write(base, args):
            pytest.fail(
                f"BLOCKED: test tried to perform a real GitHub write: "
                f"{program} {' '.join(str(a) for a in args[:6])} ...\n{_GH_GUARD_HINT}"
            )
        return await real_exec(program, *args, **kwargs)

    def guarded_docker_sync(args):
        pytest.fail(
            f"BLOCKED: test tried to run real `docker {' '.join(str(a) for a in args[:8])}` "
            f"via sandcastle._docker_run_sync.\n{_GUARD_HINT}"
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", guarded_exec)
    monkeypatch.setattr(_sandcastle_mod, "_docker_run_sync", guarded_docker_sync)
