"""doctor names the no-mistakes hand-off — fail-closed, like a missing engine binary.

The tripwire class is doctor's seeded faults: for every piece of tooling an
unattended session needs, a host that lacks it must be a LOUD finding with a
nonzero exit, not a goal that stops at the hand-off and reads as a mute
instance (goal dc-phone-tab-bar-971, issue #982). The binary is a ``fail``
exactly like ``claude`` / ``treehouse``; the home is a ``warn``, because the
first goal's ``no-mistakes init`` is what creates it.

The second half pins the verdict → exit-code mapping itself: "the same verdict"
only means "the same exit code" while ``fail`` and ``unknown`` are the two that
exit nonzero.
"""

from __future__ import annotations

import argparse
import os
from types import SimpleNamespace

import pytest

from devclaw import cli
from devclaw import config as _config
from devclaw.doctor import DoctorReport, Finding, Verdict, run_doctor
from devclaw.doctor.checks import NM_HOME_DEFAULT, check_handoff, check_tools

_RUNBOOK = "docs/runbooks/no-mistakes-in-container.md"


@pytest.fixture
def production_engine(monkeypatch):
    """The engine doctor checks tooling for — pinned against the ambient env."""
    monkeypatch.setattr(_config, "ENGINE", "")
    monkeypatch.setattr(_config, "CLAUDE_BIN", "claude")
    monkeypatch.setattr(_config, "TREEHOUSE_BIN", "treehouse")


def _stub_path(monkeypatch, tmp_path, *names: str) -> None:
    """PATH holding exactly ``names`` as executables — its own directory per
    call, so an earlier case's shim cannot linger on a later case's PATH."""
    bindir = tmp_path / ("bin-" + ("-".join(names) or "empty"))
    bindir.mkdir(exist_ok=True)
    for name in names:
        shim = bindir / name
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))


def _findings(monkeypatch, tmp_path, *, on_path: bool, home: str | None) -> dict[str, Finding]:
    _stub_path(monkeypatch, tmp_path, *(("no-mistakes", "claude", "treehouse") if on_path else ()))
    if home is None:
        monkeypatch.delenv("NM_HOME", raising=False)
    else:
        monkeypatch.setenv("NM_HOME", home)
    return {f.check_id: f for f in check_handoff(None, None)}


def test_a_missing_no_mistakes_fails_like_a_missing_engine_binary(monkeypatch, tmp_path, production_engine):
    home = tmp_path / "nm"
    home.mkdir()
    found = _findings(monkeypatch, tmp_path, on_path=True, home=str(home))
    assert found["handoff.no-mistakes"].verdict is Verdict.OK
    assert "on PATH" in found["handoff.no-mistakes"].evidence

    missing = _findings(monkeypatch, tmp_path, on_path=False, home=str(home))["handoff.no-mistakes"]
    assert missing.verdict is Verdict.FAIL
    assert "not on PATH" in missing.evidence
    # the line names the fix …
    assert _RUNBOOK in missing.remedy
    # … and carries the verdict a missing `claude` / `treehouse` carries, which
    # is what the CLI's exit code is computed from.
    engine = {f.check_id: f for f in check_tools(None, None)}
    assert engine["tools.claude"].verdict is engine["tools.treehouse"].verdict is missing.verdict


def test_the_handoff_home_is_reported_and_never_fails_the_instance(monkeypatch, tmp_path, production_engine):
    present = tmp_path / "nm"
    present.mkdir()
    assert _findings(monkeypatch, tmp_path, on_path=True, home=str(present))["handoff.home"] == Finding(
        "handoff.home", Verdict.OK, str(present)
    )

    absent = _findings(monkeypatch, tmp_path, on_path=True, home=str(tmp_path / "gone"))["handoff.home"]
    # warn, not fail: until the first goal's `no-mistakes init` there is no home
    # to find, and that instance is healthy (spec 016 FR-004 shape).
    assert absent.verdict is Verdict.WARN
    assert str(tmp_path / "gone") in absent.evidence and _RUNBOOK in absent.remedy

    # unset ⇒ no-mistakes' own default, expanded, not the literal "~"
    default = _findings(monkeypatch, tmp_path, on_path=True, home=None)["handoff.home"]
    assert os.path.expanduser(NM_HOME_DEFAULT) in default.evidence
    assert "~" not in default.evidence


def test_the_stub_engine_never_needs_the_handoff(monkeypatch, tmp_path):
    """`DEVCLAW_ENGINE=stub` hands nothing off — a FAIL there would be a false
    alarm on every local dev box, the same reason check_tools skips the engine
    binaries."""
    monkeypatch.setattr(_config, "ENGINE", "stub")
    found = _findings(monkeypatch, tmp_path, on_path=False, home=None)
    assert [f.verdict for f in found.values()] == [Verdict.OK]


def test_the_handoff_reaches_the_report_devclaw_doctor_prints(monkeypatch, tmp_path, production_engine):
    """Registration, not just the function. A `check_handoff` missing from
    ``CHECKS`` reports nothing to `devclaw doctor` while every assertion above
    still passes — the shape of a check that exists and is never run."""
    _stub_path(monkeypatch, tmp_path, "no-mistakes")
    monkeypatch.setenv("NM_HOME", str(tmp_path))
    store = SimpleNamespace(
        global_pause=lambda: (None, None),
        list_goals=lambda open_only=False: [],
        db_size_bytes=lambda: 0,
        db_path=str(tmp_path / "devclaw.db"),
    )
    ids = [f.check_id for f in run_doctor(store, SimpleNamespace(list=lambda: [])).findings]
    assert "handoff.no-mistakes" in ids and "handoff.home" in ids
    # declared order — the report is deterministic for unchanged state
    assert ids.index("tools.git") < ids.index("handoff.no-mistakes") < ids.index("pause")


@pytest.mark.parametrize(
    "verdict,code",
    [(Verdict.OK, 0), (Verdict.WARN, 0), (Verdict.FAIL, 1), (Verdict.UNKNOWN, 1)],
)
def test_devclaw_doctor_exits_nonzero_on_fail_and_unknown(monkeypatch, capsys, verdict, code):
    monkeypatch.setattr(cli, "run_doctor", lambda store, registry: DoctorReport([Finding("x", verdict, "e")]))
    assert cli._cmd_doctor(argparse.Namespace(json=False)) == code
