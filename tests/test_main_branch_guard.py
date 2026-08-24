"""Regression tests for the main-branch PreToolUse guard.

Named after the behavior that was broken: the guard resolved the branch from
the session cwd (the main checkout, always on `main`), so it blocked every
legitimate commit made inside a worktree the command `cd`s into — a systematic
false positive that trained the DEVCLAW_ALLOW_MAIN reflex and hollowed out the
guard. The fix resolves the dir the git command actually runs in.
"""
import subprocess
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_HOOK = _REPO / ".claude" / "hooks" / "main-branch-guard.py"


def _hook_source(path: Path = _HOOK) -> str:
    # The devclaw sandbox tmpfs-shadows the repo's tracked .claude/, so the
    # working-tree file can be absent or unreadable in-sandbox; the committed
    # hook is the same content — read it through git rather than erroring the
    # whole suite at collection.
    try:
        return path.read_text()
    except OSError:
        return subprocess.run(
            ["git", "show", "HEAD:.claude/hooks/main-branch-guard.py"],
            cwd=_REPO, capture_output=True, text=True, check=True,
        ).stdout

if not _HOOK.exists():
    pytest.skip(
        f"hook file absent ({_HOOK}) — .claude/ is not present in this environment",
        allow_module_level=True,
    )


def _load():
    mod = types.ModuleType("main_branch_guard")
    mod.__file__ = str(_HOOK)
    exec(compile(_hook_source(), str(_HOOK), "exec"), mod.__dict__)
    return mod


guard = _load()


def test_hook_source_falls_back_to_git_when_working_tree_unreadable():
    # The sandbox case: .claude/ is tmpfs-shadowed, so the working-tree path
    # is unreadable — the loader must serve the committed hook via `git show`
    # instead of failing the suite at collection time.
    src = _hook_source(Path("/nonexistent/.claude/hooks/main-branch-guard.py"))
    assert "def blocks" in src and "def effective_cwd" in src

MAIN_CHECKOUT = "/home/x/projects/devclaw"  # the session cwd — always on main


# --- effective_cwd: which dir does the git command actually target? ---

def test_effective_cwd_variable_cd_is_unresolvable():
    # `cd "$WT" && git commit` — the worktree path is a shell var we can't
    # resolve in the hook → None (branch unknown), NOT the main checkout.
    assert guard.effective_cwd('cd "$WT" && git commit -m x', MAIN_CHECKOUT) is None


def test_effective_cwd_literal_cd_wins_over_payload():
    assert guard.effective_cwd("cd /tmp/wt && git commit -m x", MAIN_CHECKOUT) == "/tmp/wt"


def test_effective_cwd_git_dash_C_dir_form():
    assert guard.effective_cwd("git -C /tmp/wt commit -m x", MAIN_CHECKOUT) == "/tmp/wt"


def test_effective_cwd_commit_dash_C_ref_is_not_a_dir():
    # `git commit -C HEAD` reuses a commit message; -C here is NOT the dir flag.
    assert guard.effective_cwd("git commit -C HEAD", MAIN_CHECKOUT) == MAIN_CHECKOUT


def test_effective_cwd_no_cd_falls_back_to_payload():
    assert guard.effective_cwd("git commit -m x", MAIN_CHECKOUT) == MAIN_CHECKOUT


# --- blocks: end-to-end behavior with a stubbed branch lookup ---

@pytest.fixture
def on_branch(monkeypatch):
    """Stub current_branch to a per-path mapping (default: main checkout→main)."""
    def _set(mapping):
        monkeypatch.setattr(guard, "current_branch",
                            lambda cwd: mapping.get(cwd, ""))
    _set({MAIN_CHECKOUT: "main"})
    return _set


def test_worktree_commit_not_blocked_the_fixed_false_positive(on_branch):
    # THE regression: a worktree commit no longer blocks just because the
    # session cwd is the main checkout.
    assert guard.blocks('cd "$WT" && git commit -m x', MAIN_CHECKOUT) is None


def test_bare_commit_in_main_checkout_still_blocked(on_branch):
    # The guard still does its job when git really runs in a main-branch dir.
    msg = guard.blocks("git commit -m x", MAIN_CHECKOUT)
    assert msg and "on main" in msg


def test_literal_cd_into_main_checkout_is_blocked(on_branch):
    # Stronger than before: a `cd` into a literal main-branch dir is caught even
    # when the session cwd is elsewhere.
    on_branch({"/repo/main": "main"})
    msg = guard.blocks("cd /repo/main && git commit -m x", "/some/worktree")
    assert msg and "on main" in msg


def test_push_targeting_main_blocked_even_when_dir_unknown(on_branch):
    # The push-targets-main check is dir-independent — fires even with a $VAR cd.
    msg = guard.blocks('cd "$WT" && git push origin HEAD:main', MAIN_CHECKOUT)
    assert msg and "main" in msg


def test_override_always_wins(on_branch):
    assert guard.blocks("DEVCLAW_ALLOW_MAIN=1 git commit -m x", MAIN_CHECKOUT) is None


def test_worktree_push_feature_branch_allowed(on_branch):
    assert guard.blocks("cd /tmp/wt && git push origin HEAD:feat/x", MAIN_CHECKOUT) is None
