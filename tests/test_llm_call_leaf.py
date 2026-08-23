"""llm_call is a LEAF module — the quality gate imports it without the heavies.

The quality gate needed exactly three symbols (`PlannerError`,
`claude_with_model`, `extract_json`) but historically imported them from the
(now-deleted) `planner`, which dragged `state_store` + `task_git` and closed
the ``quality → planner → loom → goal`` import cycle. The primitive lives in
``llm_call.py`` (only internal dep: ``loom.trace``, itself pure stdlib).
These pin the leaf-ness and the gate's rewiring so the cycle can't silently
return via any heavy module.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def test_llm_call_imports_without_planner_or_state_store():
    # Fresh interpreter: importing the leaf must not pull the heavy modules.
    code = (
        "import sys; import devclaw.llm_call; "
        "heavy = [m for m in ('devclaw.state_store', "
        "'devclaw.task_git', 'devclaw.task_queue', 'devclaw.goal') "
        "if m in sys.modules]; "
        "assert not heavy, f'leaf pulled heavy modules: {heavy}'; print('leaf-ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=_REPO
    )
    assert out.returncode == 0, out.stderr
    assert "leaf-ok" in out.stdout


def test_quality_modules_import_llm_call_not_heavy_modules():
    # Static source pin: the gate's modules take the LLM primitive from the
    # leaf, never from a heavy module (task_queue/goal drag state_store +
    # task_git and would re-close the old quality → planner-shaped cycle).
    # Only the LLM-calling modules are listed — browser_gate.py is pure
    # parsing and imports no caller at all.
    for mod in ("__init__.py", "reachability.py"):
        src = (_REPO / "devclaw" / "quality" / mod).read_text()
        assert "from ..llm_call import" in src, mod
        for heavy in ("from ..task_queue import", "from ..goal import",
                      "from ..state_store import", "from ..task_git import"):
            assert heavy not in src, f"{mod}: {heavy}"


async def _no_spawn(*argv, **kwargs):  # pragma: no cover - must not be reached
    raise AssertionError("subprocess must not spawn in this test")


def test_call_claude_strips_api_keys_from_subprocess_env(monkeypatch):
    # The OAuth-only invariant, pinned on the subprocess path itself: the env
    # dict handed to create_subprocess_exec must never carry an API key —
    # a stray key must not silently switch cognition onto metered billing.
    import asyncio as real_asyncio

    from devclaw import llm_call

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-leak")
    seen: dict = {}

    async def fake_spawn(*argv, **kwargs):
        seen["env"] = kwargs.get("env")
        raise OSError("stop here — env captured")

    monkeypatch.setattr(llm_call.asyncio, "create_subprocess_exec", fake_spawn)
    with pytest.raises(llm_call.PlannerError):
        real_asyncio.run(llm_call.call_claude("hi"))
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in seen["env"]




def test_call_claude_keeps_the_setup_token_in_subprocess_env(monkeypatch):
    # The other half of the OAuth-only invariant: the strip is a DENYLIST of
    # metered credentials, not an allowlist. A `claude setup-token` OAuth token
    # (subscription-backed, ranked above the /login credential) must survive
    # into host cognition's subprocess env — stripping it would silently put the
    # box back on the interactive login this token exists to stop depending on.
    import asyncio as real_asyncio

    from devclaw import llm_call

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    seen: dict = {}

    async def fake_spawn(*argv, **kwargs):
        seen["env"] = kwargs.get("env")
        raise OSError("stop here — env captured")

    monkeypatch.setattr(llm_call.asyncio, "create_subprocess_exec", fake_spawn)
    with pytest.raises(llm_call.PlannerError):
        real_asyncio.run(llm_call.call_claude("hi"))
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-live"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
