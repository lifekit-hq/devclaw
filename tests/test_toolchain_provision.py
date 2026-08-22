"""Project-declared toolchain provisioning (ADR 0005).

The sandbox image ships no language SDKs beyond python+node; the runner's
pre-step detects the project's declared toolchain (mise-native files, or
translated global.json / package.json engines), `mise install`s it, and
exports the resulting environment so the agent's shells and the verify gate
inherit it. These tests pin the contract:

  - detection reads ONLY the workspace's declaration files;
  - no declaration → zero-cost no-op (no subprocess at all);
  - a declared toolchain with no mise on PATH fails CLOSED (the deploy-skew
    class — lifekit-stack#93 — must never silently degrade to python+node);
  - translated declarations are recorded OUTSIDE the workspace (a generated
    file in /workspace would dirty the diff the review gate sees);
  - the per-project cache volume mount is part of the docker argv posture.

The runner lives at runner/runner.py (not a package); its
runner is stdlib-only since spec 011, so a top-level import is dependency-free.
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

import devclaw.engine.sandcastle as sc

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_toolchain", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def restore_env():
    """_provision_toolchain deliberately mutates os.environ (that's the export
    contract); undo it so tests stay independent. Also clears MISE_GLOBAL_CONFIG_FILE
    before each test so native-path absence assertions start from a known clean state
    regardless of what the outer shell has set."""
    before = dict(os.environ)
    os.environ.pop("MISE_GLOBAL_CONFIG_FILE", None)
    yield
    os.environ.clear()
    os.environ.update(before)


# ---- detection ----


def test_no_declaration_detects_nothing(runner, tmp_path):
    assert runner._detect_toolchain(str(tmp_path)) == (False, {})


@pytest.mark.parametrize("name", [".mise.toml", "mise.toml", ".tool-versions"])
def test_mise_native_file_wins_untranslated(runner, tmp_path, name):
    (tmp_path / name).write_text("dotnet = '9.0'\n")
    # a global.json alongside is NOT translated — mise reads its own file as-is
    (tmp_path / "global.json").write_text('{"sdk": {"version": "8.0.100"}}')
    assert runner._detect_toolchain(str(tmp_path)) == (True, {})


def test_global_json_translates_to_fuzzy_major_minor(runner, tmp_path):
    # fuzzy on purpose: rollForward reality means any 9.0.x satisfies 9.0.203
    (tmp_path / "global.json").write_text('{"sdk": {"version": "9.0.203"}}')
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"dotnet": "9.0"})


def test_global_json_without_sdk_version_declares_nothing(runner, tmp_path):
    (tmp_path / "global.json").write_text('{"msbuild-sdks": {}}')
    assert runner._detect_toolchain(str(tmp_path)) == (False, {})


def test_corrupt_global_json_fails_closed_not_skipped(runner, tmp_path):
    (tmp_path / "global.json").write_text('{"sdk": {')
    with pytest.raises(runner.ToolchainError, match="global.json"):
        runner._detect_toolchain(str(tmp_path))


def test_package_json_engines_node_translates_minimum(runner, tmp_path):
    (tmp_path / "package.json").write_text('{"engines": {"node": ">=20.11"}}')
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"node": "20.11"})


def test_package_json_without_engines_declares_nothing(runner, tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}')
    assert runner._detect_toolchain(str(tmp_path)) == (False, {})


def test_translations_combine_across_files(runner, tmp_path):
    (tmp_path / "global.json").write_text('{"sdk": {"version": "10.0.100"}}')
    (tmp_path / "package.json").write_text('{"engines": {"node": "^22.1"}}')
    assert runner._detect_toolchain(str(tmp_path)) == (
        False,
        {"dotnet": "10.0", "node": "22.1"},
    )


# ---- generic marker-file table (#424) ----
# A repo that pins its SDK any way OTHER than mise-native / global.json /
# package.json engines (a .NET .csproj, a Go go.mod, a Rust Cargo.toml, a
# versioned pyproject/.python-version) declared NOTHING the detector saw, so
# pre-agent provisioning was a no-op and the agent had to self-wire mid-task —
# the 2026-07-29 v0.1 smoke derail. dotnet/go/rust are absent from the base
# image → marker presence provisions them (version parsed, else `latest`);
# python lives in the base → only an explicit version pin provisions it.


def test_csproj_without_global_json_provisions_dotnet_from_target_framework(runner, tmp_path):
    """The smoke case: a .NET project pinned via a .csproj with no global.json.
    <TargetFramework>net9.0</TargetFramework> → dotnet 9.0, provisioned
    pre-agent instead of relying on the agent noticing it must self-wire."""
    (tmp_path / "App.csproj").write_text(
        "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
        "  <PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup>\n"
        "</Project>\n"
    )
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"dotnet": "9.0"})


def test_csproj_in_immediate_subdir_is_detected(runner, tmp_path):
    """lifekit-dashboard verifies with `cd backend && dotnet test`, so its
    .csproj is one level down — depth-1 scan must find it (root-only detection
    would make the smoke fix a no-op for exactly the layout it targets)."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "Api.csproj").write_text(
        "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework>"
        "</PropertyGroup></Project>"
    )
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"dotnet": "8.0"})


def test_sln_without_parseable_framework_degrades_to_latest(runner, tmp_path):
    """A bare .sln (or a .csproj whose TargetFramework isn't netX.Y) still
    declares dotnet — version resolution degrades to `latest` (mise picks a
    default) rather than erroring."""
    (tmp_path / "Solution.sln").write_text("Microsoft Visual Studio Solution File\n")
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"dotnet": "latest"})


def test_global_json_version_wins_over_sibling_csproj(runner, tmp_path):
    """Idiomatic translator precedence: when both global.json (pins 9.0) and a
    .csproj (net8.0) declare dotnet, the global.json pin wins via setdefault."""
    (tmp_path / "global.json").write_text('{"sdk": {"version": "9.0.203"}}')
    (tmp_path / "App.csproj").write_text(
        "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework>"
        "</PropertyGroup></Project>"
    )
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"dotnet": "9.0"})


def test_go_mod_translates_go_directive(runner, tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n\nrequire ()\n")
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"go": "1.22"})


def test_bare_go_mod_degrades_to_latest(runner, tmp_path):
    """A go.mod with no `go` directive still declares Go → `latest`."""
    (tmp_path / "go.mod").write_text("module example.com/x\n")
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"go": "latest"})


def test_cargo_toml_translates_to_rust_latest(runner, tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"x\"\nversion = \"0.1.0\"\n")
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"rust": "latest"})


def test_python_version_file_pins_python(runner, tmp_path):
    """`.python-version` is an explicit pin → provision that python (unlike a
    version-less pyproject, which the base python satisfies)."""
    (tmp_path / ".python-version").write_text("3.12\n")
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"python": "3.12"})


def test_pyproject_requires_python_pins_minimum(runner, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"x\"\nrequires-python = \">=3.11\"\n"
    )
    assert runner._detect_toolchain(str(tmp_path)) == (False, {"python": "3.11"})


def test_versionless_pyproject_declares_nothing(runner, tmp_path):
    """python lives in the base image, so a pyproject with no requires-python is
    already satisfied — no forced `latest` install (contrast dotnet/go/rust)."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"x\"\n")
    assert runner._detect_toolchain(str(tmp_path)) == (False, {})


def test_marker_scan_skips_vendored_and_hidden_dirs(runner, tmp_path):
    """A go.mod under vendor/ or a .csproj under node_modules/ is a dependency's
    declaration, not the project's — never provisioned off it."""
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "go.mod").write_text("module dep\n\ngo 1.20\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "Dep.csproj").write_text(
        "<Project><TargetFramework>net6.0</TargetFramework></Project>"
    )
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "go.mod").write_text("module h\n\ngo 1.19\n")
    assert runner._detect_toolchain(str(tmp_path)) == (False, {})


def test_mise_native_still_wins_over_markers(runner, tmp_path):
    """A mise-native file short-circuits BEFORE the marker table — a repo with
    both .mise.toml and a .csproj stays (True, {}), mise reading its own file."""
    (tmp_path / ".mise.toml").write_text("[tools]\ndotnet = \"9.0\"\n")
    (tmp_path / "App.csproj").write_text(
        "<Project><TargetFramework>net8.0</TargetFramework></Project>"
    )
    assert runner._detect_toolchain(str(tmp_path)) == (True, {})


# ---- provisioning ----


def test_undeclared_workspace_is_a_zero_cost_noop(runner, tmp_path, monkeypatch):
    """No declaration → None, and NO subprocess is ever spawned (the tick-path
    analogue of the zero-token idle guard: idle projects pay nothing)."""

    def _boom(*a, **k):  # pragma: no cover - the assertion IS that it's unreached
        raise AssertionError("provisioning ran a subprocess on an undeclared workspace")

    monkeypatch.setattr(runner.subprocess, "run", _boom)
    monkeypatch.setattr(runner.shutil, "which", _boom)
    assert runner._provision_toolchain(str(tmp_path)) is None


def test_declared_toolchain_without_mise_fails_closed(runner, tmp_path, monkeypatch):
    """The deploy-skew class (lifekit-stack#93): new runner + stale image with
    no mise must be a loud error, never a silent python+node fallback."""
    (tmp_path / ".tool-versions").write_text("nodejs 22.1.0\n")
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    with pytest.raises(runner.ToolchainError, match="mise.*not on PATH"):
        runner._provision_toolchain(str(tmp_path))


def _fake_mise(calls, *, install_rc=0, env_json='{"PATH": "/fake/shims"}'):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:2] == ["mise", "install"]:
            return subprocess.CompletedProcess(argv, install_rc, stdout="", stderr="boom")
        if argv[:2] == ["mise", "env"]:
            return subprocess.CompletedProcess(argv, 0, stdout=env_json, stderr="")
        raise AssertionError(f"unexpected subprocess: {argv}")

    return run


def test_native_declaration_installs_in_workspace_trusted(
    runner, tmp_path, monkeypatch, restore_env
):
    (tmp_path / ".tool-versions").write_text("nodejs 22.1.0\n")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")
    calls = []
    monkeypatch.setattr(runner.subprocess, "run", _fake_mise(calls))

    summary = runner._provision_toolchain(str(tmp_path))

    assert summary is not None and summary["native"] is True and summary["tools"] == {}
    install_argv, install_kwargs = calls[0]
    assert install_argv == ["mise", "install"]
    # cwd = the workspace (mise reads the project's own file), and the
    # workspace is trusted so non-interactive mise doesn't refuse its config
    assert install_kwargs["cwd"] == str(tmp_path)
    assert install_kwargs["env"]["MISE_TRUSTED_CONFIG_PATHS"] == str(tmp_path)
    # native path records NO translated config
    assert "MISE_GLOBAL_CONFIG_FILE" not in os.environ


def test_translated_declaration_never_touches_the_workspace(
    runner, tmp_path, monkeypatch, restore_env
):
    """global.json → mise config OUTSIDE /workspace: a generated file in the
    workspace would dirty the diff the review gate and delivery see."""
    (tmp_path / "global.json").write_text('{"sdk": {"version": "9.0.203"}}')
    before = set(os.listdir(tmp_path))
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")
    calls = []
    monkeypatch.setattr(runner.subprocess, "run", _fake_mise(calls))

    summary = runner._provision_toolchain(str(tmp_path))

    assert summary is not None and summary["tools"] == {"dotnet": "9.0"}
    assert set(os.listdir(tmp_path)) == before  # workspace byte-untouched
    cfg = os.environ.get("MISE_GLOBAL_CONFIG_FILE")
    assert cfg and not cfg.startswith(str(tmp_path))
    assert 'dotnet = "9.0"' in Path(cfg).read_text()


def test_provisioned_env_is_exported_for_agent_and_verify_gate(
    runner, tmp_path, monkeypatch, restore_env
):
    (tmp_path / ".tool-versions").write_text("dotnet 9.0\n")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")
    calls = []
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        _fake_mise(calls, env_json=json.dumps({"PATH": "/shims:/usr/bin", "DOTNET_ROOT": "/tools/dotnet"})),
    )

    runner._provision_toolchain(str(tmp_path))

    # the whole point: the agent's shells AND verify_cmd inherit the toolchain
    assert os.environ["PATH"] == "/shims:/usr/bin"
    assert os.environ["DOTNET_ROOT"] == "/tools/dotnet"


def test_mise_env_export_strips_api_keys(runner, tmp_path, monkeypatch, restore_env):
    """OAuth-only invariant: `mise env` reflects the (trusted) workspace's own
    [env] config, so a project's .mise.toml could reintroduce a metered API
    key AFTER _refuse_api_key() already passed. The export loop must re-apply
    the denylist — a stray key must never silently switch autonomous runs
    onto metered billing."""
    (tmp_path / ".tool-versions").write_text("nodejs 22\n")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        _fake_mise(
            [],
            env_json=json.dumps(
                {
                    "PATH": "/shims",
                    "ANTHROPIC_API_KEY": "sk-ant-leak",
                    "ANTHROPIC_AUTH_TOKEN": "leak-token",
                }
            ),
        ),
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    runner._provision_toolchain(str(tmp_path))

    assert os.environ["PATH"] == "/shims"  # legit vars still exported
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ


def test_mise_install_failure_is_legible_and_closed(
    runner, tmp_path, monkeypatch, restore_env
):
    (tmp_path / ".tool-versions").write_text("nodejs 22\n")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")
    monkeypatch.setattr(runner.subprocess, "run", _fake_mise([], install_rc=1))
    with pytest.raises(runner.ToolchainError, match=r"mise install.*exit 1.*boom"):
        runner._provision_toolchain(str(tmp_path))


# ---- the per-project cache volume (sandcastle side) ----


def test_toolchain_volume_name_is_deterministic_and_project_scoped():
    a = sc._toolchain_volume_name("/srv/devclaw/workspaces/finance-sentry")
    assert a == sc._toolchain_volume_name("/srv/devclaw/workspaces/finance-sentry")
    assert a.startswith("devclaw-toolchains-finance-sentry-")
    # a DIFFERENT project (even same basename elsewhere) gets its OWN volume —
    # per-project isolation was the lock decision, over a shared cache
    b = sc._toolchain_volume_name("/elsewhere/finance-sentry")
    assert b != a
    # docker volume name grammar
    import re

    assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", a)


def test_toolchain_volume_name_sanitizes_hostile_basenames():
    v = sc._toolchain_volume_name("/srv/ws/My Repo (v2)!")
    assert v.startswith("devclaw-toolchains-my-repo-v2-")
    import re

    assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", v)


def test_docker_args_mount_the_project_toolchain_cache():
    args = sc._build_docker_args(
        container_name="c",
        host_bind_path="/host/ws",
        claude_dir="/home/me/.claude",
        payload="{}",
    )
    expected = f"{sc._toolchain_volume_name('/host/ws')}:{sc.CONTAINER_MISE_DATA}"
    assert expected in args
    assert args[args.index(expected) - 1] == "-v"


# ---- verify-gate mise re-sync (the .csproj/.sln regression) --------------
# A .NET repo that pins its SDK in a .csproj/.sln (no global.json/.mise.toml)
# declares nothing _detect_toolchain sees → pre-agent provisioning is a no-op.
# The agent then wires mise itself mid-task, but the verify gate inherited the
# STALE pre-agent env, so `dotnet test` exited 127. _resync_mise_env re-applies
# the current mise env right before the gate. (Reproduces the 2026-07-28 v0.1
# smoke failure on lifekit-hq/lifekit-dashboard.)


def test_resync_mise_env_puts_shims_on_path_and_folds_env(
    runner, tmp_path, monkeypatch, restore_env
):
    shims = tmp_path / "mise" / "shims"
    shims.mkdir(parents=True)
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("DOTNET_ROOT", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")

    def fake_run(argv, **kwargs):
        assert argv[:2] == ["mise", "env"]
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=json.dumps({"DOTNET_ROOT": "/ws/.dotnet", "PATH": "/ws/.dotnet:/usr/bin"}),
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner._resync_mise_env(str(tmp_path))
    # shims dir prepended (cwd-robust tool resolution for `cd backend && dotnet test`)
    assert os.environ["PATH"].split(os.pathsep)[0] == str(shims)
    # mise env folded in (DOTNET_ROOT so the SDK is locatable)
    assert os.environ["DOTNET_ROOT"] == "/ws/.dotnet"


def test_resync_mise_env_is_a_noop_without_mise(runner, tmp_path, monkeypatch, restore_env):
    monkeypatch.setenv("PATH", "/usr/bin")
    before = os.environ["PATH"]

    def _boom(*a, **k):  # pragma: no cover - assertion IS that it's unreached
        raise AssertionError("_resync ran a subprocess with no mise on PATH")

    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    monkeypatch.setattr(runner.subprocess, "run", _boom)
    runner._resync_mise_env(str(tmp_path))
    assert os.environ["PATH"] == before  # untouched


def test_resync_mise_env_reapplies_oauth_denylist(runner, tmp_path, monkeypatch, restore_env):
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda argv, **k: subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"ANTHROPIC_API_KEY": "sk-leak", "DOTNET_ROOT": "/x"}), stderr=""
        ),
    )
    runner._resync_mise_env(str(tmp_path))
    assert "ANTHROPIC_API_KEY" not in os.environ  # a workspace .mise.toml can't reintroduce a key
    assert os.environ.get("DOTNET_ROOT") == "/x"


def test_resync_mise_env_survives_mise_env_failure(runner, tmp_path, monkeypatch, restore_env):
    monkeypatch.setenv("PATH", "/usr/bin")
    before = dict(os.environ)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 1, stdout="", stderr="mise boom"),
    )
    runner._resync_mise_env(str(tmp_path))  # must not raise
    assert os.environ.get("DOTNET_ROOT") == before.get("DOTNET_ROOT")


# ---- agent-shell mise shims on PATH (the .csproj/.sln agent-derail) ---------
# A .NET repo that pins its SDK in a .csproj/.sln (no global.json/.mise.toml)
# declares nothing _detect_toolchain sees → pre-agent _provision_toolchain is a
# no-op and never puts mise shims on PATH. Without _ensure_mise_shims_on_path the
# agent's shell inherits the bare image PATH, hits `dotnet: command not found`,
# and derails into editing AGENTS.md instead of the ticket (2026-07-29 v0.1 smoke
# on lifekit-hq/lifekit-dashboard). Twin of the verify-gate re-sync (#421).


def test_ensure_mise_shims_prepends_shims_dir_for_native_sdk_repo(
    runner, tmp_path, monkeypatch, restore_env
):
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")

    runner._ensure_mise_shims_on_path()

    shims = str(tmp_path / "mise" / "shims")
    # shims dir prepended (so `dotnet` resolves once the agent `mise install`s it)
    assert os.environ["PATH"].split(os.pathsep)[0] == shims
    # deliberately added even though it does not exist yet — mise creates it at
    # install time, AFTER this runs; a shim resolves the version lazily at exec.
    assert not os.path.isdir(shims)


def test_ensure_mise_shims_is_a_noop_without_mise(
    runner, monkeypatch, restore_env
):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)

    runner._ensure_mise_shims_on_path()

    assert os.environ["PATH"] == "/usr/bin"  # untouched


def test_ensure_mise_shims_is_idempotent(
    runner, tmp_path, monkeypatch, restore_env
):
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/local/bin/mise")

    runner._ensure_mise_shims_on_path()
    runner._ensure_mise_shims_on_path()

    shims = str(tmp_path / "mise" / "shims")
    # prepended exactly once, not stacked
    assert os.environ["PATH"].split(os.pathsep).count(shims) == 1
    assert os.environ["PATH"].split(os.pathsep)[0] == shims
