"""Host facts reach the session before it spends, and the gate reads the diff.

Two zero-waste guards on the verify path (#987):

- a repository that drives a browser on a host whose Chromium cannot load a
  shared library is told in the FIRST prompt, not when the e2e step runs at the
  end of a long paid session;
- a branch whose whole diff is documentation runs ``.devclaw/verify`` with
  ``DEVCLAW_DOCS_ONLY=1``, so the repo's script may leave its browser e2e out.
  Anything unreadable or mixed fails CLOSED: the e2e step stays in.

Real git for the diff; ``ldd`` and the browser cache are stubbed.
"""

import os
import subprocess

import pytest

from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(cwd, *argv):
    return subprocess.run(["git", *argv], cwd=cwd, check=True, text=True, capture_output=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def _repo(tmp_path, files):
    """A repo with ``origin/main`` at a base commit and ``files`` changed on a branch."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "app.py").write_text("x = 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "base")
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "main")
    _git(work, "fetch", "-q", "origin")
    _git(work, "checkout", "-q", "-b", "goal")
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    return work


@pytest.mark.parametrize("files, docs_only", [
    ({"README.md": "a"}, True),
    ({"docs/guide/setup.md": "a", "CHANGELOG.md": "b", "LICENSE": "c"}, True),
    ({"specs/001/spec.md": "a", ".devclaw/workflow.md": "b"}, True),
    ({"README.md": "a", "app.py": "x = 2\n"}, False),            # one code file ends it
    ({"frontend/src/page.md": "a"}, False),                      # markdown the product may read
    ({"requirements.txt": "a"}, False),
    ({".devclaw/verify": "#!/bin/sh\n"}, False),                 # the gate itself is not docs
    ({}, False),                                                 # no change claims nothing
])
async def test_only_a_documentation_diff_is_docs_only(tmp_path, files, docs_only):
    work = _repo(tmp_path, files)
    assert await engine.docs_only_diff(str(work)) is docs_only
    _git(work, "add", "-A")
    if files:
        _git(work, "commit", "-q", "-m", "change")
    assert await engine.docs_only_diff(str(work)) is docs_only  # committed or not


async def test_no_base_to_diff_against_is_not_docs_only(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("a")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "only")
    assert await engine.docs_only_diff(str(work)) is False


async def test_the_verify_run_sees_the_docs_only_signal_only_when_it_is_true(tmp_path):
    cmd = f"echo flag=${{{engine.DOCS_ONLY_ENV}:-unset}}"
    on = await engine.run_verify(cmd, str(tmp_path), timeout=30, docs_only=True)
    off = await engine.run_verify(cmd, str(tmp_path), timeout=30)
    assert "flag=1" in on["output"] and "flag=unset" in off["output"]


@pytest.fixture
def chromium(tmp_path, monkeypatch):
    binary = tmp_path / "ms-playwright" / "chromium-1" / "chrome-linux" / "chrome"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "ms-playwright"))
    return binary


def _ldd(monkeypatch, rc, out):
    async def host(workspace_dir, *argv, timeout=120.0):
        assert argv[0] == "ldd"
        return rc, out
    monkeypatch.setattr(engine, "_host", host)


@pytest.mark.parametrize("config", ["playwright.config.ts", "frontend/playwright.config.ts"])
async def test_a_browser_repo_on_a_host_missing_a_library_hears_it_before_the_session(
        chromium, tmp_path, monkeypatch, config):
    work = tmp_path / "repo"
    (work / config).parent.mkdir(parents=True)
    (work / config).write_text("")
    _ldd(monkeypatch, 0, "\tlibc.so.6 => /lib/libc.so.6 (0x1)\n\tlibXfixes.so.3 => not found\n")
    note = await engine.browser_preflight(str(work))
    assert note and "libXfixes.so.3" in note and engine.DOCS_ONLY_ENV in note

    prompts, events = [], []

    async def fake_turn(req, prompt, *, resume):
        prompts.append(prompt)
        return engine.Turn(session_id="s1", ok=True, text="NOTHING: nothing to do")

    monkeypatch.setattr(engine, "run_turn", fake_turn)
    req = EngineRequest(kind="implement_feature", workspace_dir=str(work), goal="g",
                        verify_cmd="true", on_event=events.append)
    await engine.run_claude(req)
    assert prompts[0].startswith("Host preflight:") and "libXfixes.so.3" in prompts[0]
    assert any(e.type == "Preflight" for e in events)


@pytest.mark.parametrize("config, rc, out", [
    ("playwright.config.ts", 0, "\tlibc.so.6 => /lib/libc.so.6 (0x1)\n"),   # every library resolves
    ("playwright.config.ts", 1, "ldd: not a dynamic executable"),            # inconclusive stays silent
    (None, 0, "\tlibXfixes.so.3 => not found\n"),                            # the repo drives no browser
])
async def test_the_preflight_is_silent_unless_a_browser_repo_lacks_a_library(
        chromium, tmp_path, monkeypatch, config, rc, out):
    work = tmp_path / "repo"
    work.mkdir()
    if config:
        (work / config).write_text("")
    _ldd(monkeypatch, rc, out)
    assert await engine.browser_preflight(str(work)) is None


async def test_no_installed_browser_is_not_a_preflight_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "repo"
    work.mkdir()
    (work / "playwright.config.ts").write_text("")
    assert await engine.browser_preflight(str(work)) is None
