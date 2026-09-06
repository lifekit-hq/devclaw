"""The OAuth-only invariant, pinned on the cognition subprocess path itself.

(The leaf-ness of ``llm_call`` that used to be pinned here by a fresh-interpreter
import and a static source scan is now an import-linter contract in
``pyproject.toml`` — tinyspec ``import-contracts``; the two tests were removed
with it, symmetric ratchet.)
"""

import pytest


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
