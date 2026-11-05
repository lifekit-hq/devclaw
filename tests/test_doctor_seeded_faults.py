"""doctor's seeded faults — every piece of tooling an unattended session needs.

The tripwire class: for each binary the delivery path runs, a host that lacks
it must be a LOUD finding with a nonzero exit, never a goal that stops
mid-flight and reads as a mute instance (goal dc-phone-tab-bar-971, #982).
Parametrize the sweep below when the path grows a binary; do not add a sibling
file.

The hand-off's home is the one check with three states rather than two: a home
the first goal's ``no-mistakes init`` creates is a ``warn``, and only a home no
session can heal — uncreatable, unreadable, or not a directory — is a ``fail``.

The last test pins the verdict → exit-code mapping itself: "the same verdict"
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
#: every binary the delivery path runs, and the check that reports it
TOOLING = {
    "gh": check_tools,
    "git": check_tools,
    "claude": check_tools,
    "treehouse": check_tools,
    "no-mistakes": check_handoff,
}
_CHECK_ID = {check_tools: "tools.{}", check_handoff: "handoff.{}"}


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


def _tooling_findings(monkeypatch, tmp_path, *, without: str = "") -> dict[str, Finding]:
    """Every tooling finding, with one binary seeded off PATH."""
    _stub_path(monkeypatch, tmp_path, *(n for n in TOOLING if n != without))
    home = tmp_path / "nm"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("NM_HOME", str(home))
    out: dict[str, Finding] = {}
    for check in dict.fromkeys(TOOLING.values()):
        out.update({f.check_id: f for f in check(None, None)})
    return out


@pytest.mark.parametrize("binary", list(TOOLING))
def test_a_missing_binary_on_the_delivery_path_is_a_loud_fail(binary, monkeypatch, tmp_path, production_engine):
    check_id = _CHECK_ID[TOOLING[binary]].format(binary)

    present = _tooling_findings(monkeypatch, tmp_path)[check_id]
    assert present.verdict is Verdict.OK
    assert "on PATH" in present.evidence

    missing = _tooling_findings(monkeypatch, tmp_path, without=binary)[check_id]
    assert missing.verdict is Verdict.FAIL
    assert "not on PATH" in missing.evidence
    # the one binary devclaw does not install itself names where it comes from
    if binary == "no-mistakes":
        assert _RUNBOOK in missing.remedy


def _home(monkeypatch, tmp_path, home: str | None) -> Finding:
    _stub_path(monkeypatch, tmp_path, *TOOLING)
    if home is None:
        monkeypatch.delenv("NM_HOME", raising=False)
    else:
        monkeypatch.setenv("NM_HOME", home)
    return {f.check_id: f for f in check_handoff(None, None)}["handoff.home"]


def test_the_handoff_home_separates_what_a_goal_heals_from_what_it_cannot(
    monkeypatch, tmp_path, production_engine
):
    """The home's three states. ``warn`` is for the one a goal heals by itself;
    every home no session can fix is as loud as a missing binary, because exit
    0 on it reads as an instance that can ship."""
    present = tmp_path / "nm"
    present.mkdir()
    assert _home(monkeypatch, tmp_path, str(present)) == Finding("handoff.home", Verdict.OK, str(present))

    # warn, not fail: until the first goal's `no-mistakes init` there is no home
    # to find, and that instance is healthy (spec 016 FR-004 shape).
    absent = _home(monkeypatch, tmp_path, str(tmp_path / "gone"))
    assert absent.verdict is Verdict.WARN
    assert str(tmp_path / "gone") in absent.evidence and _RUNBOOK in absent.remedy

    # unset ⇒ no-mistakes' own documented default (docs/reference/env-vars.md),
    # expanded — asserted as the literal, so the constant cannot drift silently
    default = _home(monkeypatch, tmp_path, None)
    assert NM_HOME_DEFAULT == "~/.no-mistakes"
    assert os.path.expanduser("~/.no-mistakes") in default.evidence
    assert "~" not in default.evidence

    # a symlink to a real directory is a home, not a misconfiguration
    link = tmp_path / "link-to-nm"
    link.symlink_to(present)
    assert _home(monkeypatch, tmp_path, str(link)).verdict is Verdict.OK

    a_file = tmp_path / "nm-file"
    a_file.write_text("")
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")
    # a home whose parent is a FILE: ENOTDIR on the walk, never the absence
    under_a_file = a_file / "nm"
    for broken in (a_file, dangling, under_a_file):
        finding = _home(monkeypatch, tmp_path, str(broken))
        assert finding.verdict is Verdict.FAIL, broken
        assert str(broken) in finding.evidence and _RUNBOOK in finding.remedy

    # a relative NM_HOME resolves against whatever cwd doctor happens to run
    # in — never a home, even when a directory of that name exists here
    (tmp_path / "relative-nm").mkdir()
    monkeypatch.chdir(tmp_path)
    relative = _home(monkeypatch, tmp_path, "relative-nm")
    assert relative.verdict is Verdict.FAIL
    assert "relative-nm" in relative.evidence and _RUNBOOK in relative.remedy

    # the raw value is judged, not a tidied one: no-mistakes receives the
    # environment verbatim, so these are the paths it would really use. The
    # tilde is the likeliest misconfiguration, because the DOCUMENTED default
    # is written with one and a compose `environment:` block does not expand it
    # — no-mistakes then makes a literal `~` directory in its cwd.
    for unusable in (f"  {present}  ", "~/nm", f"~nosuchuser{os.sep}nm", "$HOME/nm"):
        finding = _home(monkeypatch, tmp_path, unusable)
        assert finding.verdict is Verdict.FAIL, unusable
        assert "absolute" in finding.evidence and repr(unusable) in finding.evidence

    # an absent home several levels deep is still the self-healing kind — the
    # ancestor walk finds the writable parent rather than stopping at the first
    # missing component
    deep = _home(monkeypatch, tmp_path, str(tmp_path / "a" / "b" / "c" / "nm"))
    assert deep.verdict is Verdict.WARN



def test_a_handoff_home_that_cannot_be_stated_is_a_fail_on_any_host(
    monkeypatch, tmp_path, production_engine
):
    """The `OSError` arm on its own, pinned without depending on this process's
    uid — the realistic permission fixtures below only run for non-root, and
    this branch is the one a wrongly-owned state volume lands in."""
    home = tmp_path / "nm"
    home.mkdir()
    _stub_path(monkeypatch, tmp_path, *TOOLING)
    monkeypatch.setenv("NM_HOME", str(home))

    def _denied(*a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "stat", _denied)
    finding = {f.check_id: f for f in check_handoff(None, None)}["handoff.home"]
    assert finding.verdict is Verdict.FAIL
    assert "cannot be read" in finding.evidence and "Permission denied" in finding.evidence


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses the permission bits this pins")
def test_an_unreadable_handoff_home_never_reads_as_the_self_healing_warn(
    monkeypatch, tmp_path, production_engine
):
    """A state volume mounted or owned wrong. ``isdir``/``lexists`` swallow
    EACCES and would report it as "not created yet" with the remedy "a goal
    creates it" — an exit 0, and a lie, on a host that cannot ship."""
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    (sealed / "nm").mkdir()
    sealed.chmod(0o000)
    try:
        unreadable = _home(monkeypatch, tmp_path, str(sealed / "nm"))
        assert unreadable.verdict is Verdict.FAIL
        assert str(sealed / "nm") in unreadable.evidence and _RUNBOOK in unreadable.remedy

        # the remedy names the sealed PARENT, not the leaf the operator set —
        # otherwise the line leaves them to bisect the chain
        assert f"make {sealed} a readable" in unreadable.remedy

        # and the same for a home that is merely absent under it
        uncreatable = _home(monkeypatch, tmp_path, str(sealed / "not-yet"))
        assert uncreatable.verdict is Verdict.FAIL
        assert f"make {sealed} a readable" in uncreatable.remedy
    finally:
        sealed.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses the permission bits this pins")
def test_a_handoff_home_no_goal_can_create_is_not_the_self_healing_warn(
    monkeypatch, tmp_path, production_engine
):
    """`mkdir -p` semantics. An absent home under a read-only parent is absent
    forever — reporting it as "the first goal's `no-mistakes init` creates it"
    is an exit 0 and a false remedy. A home that exists but this uid cannot
    write into is the same class: `init` fails on state.sqlite every session."""
    read_only = tmp_path / "ro"
    read_only.mkdir(mode=0o555)
    sealed_home = tmp_path / "sealed"
    sealed_home.mkdir(mode=0o500)
    try:
        finding = _home(monkeypatch, tmp_path, str(read_only / "nm"))
        assert finding.verdict is Verdict.FAIL
        assert "cannot be created" in finding.evidence
        assert f"make {read_only} a writable" in finding.remedy and _RUNBOOK in finding.remedy
        # several missing components deep, the answer is still the read-only
        # ancestor — not the first component mkdir -p would have created
        deep = _home(monkeypatch, tmp_path, str(read_only / "a" / "b" / "nm"))
        assert deep.verdict is Verdict.FAIL
        assert f"make {read_only} a writable" in deep.remedy
        # Present but sealed: `init` cannot put state.sqlite in it. The leaf
        # stats fine, so without its own probe this is the `ok` branch.
        finding = _home(monkeypatch, tmp_path, str(sealed_home))
        assert finding.verdict is Verdict.FAIL
        assert "not writable" in finding.evidence
        assert f"make {sealed_home} a writable" in finding.remedy and _RUNBOOK in finding.remedy
    finally:
        read_only.chmod(0o700)
        sealed_home.chmod(0o700)


def test_the_stub_engine_never_needs_the_handoff(monkeypatch, tmp_path):
    """`DEVCLAW_ENGINE=stub` hands nothing off — a FAIL there would be a false
    alarm on every local dev box, the same reason check_tools skips the engine
    binaries."""
    monkeypatch.setattr(_config, "ENGINE", "stub")
    _stub_path(monkeypatch, tmp_path)
    monkeypatch.delenv("NM_HOME", raising=False)
    assert [f.verdict for f in check_handoff(None, None)] == [Verdict.OK]


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


@pytest.mark.parametrize("missing,code", [("", 0), *((b, 1) for b in TOOLING)])
def test_devclaw_doctor_exits_the_same_way_whichever_binary_is_missing(
    monkeypatch, capsys, tmp_path, production_engine, missing, code
):
    """End to end over the real findings and the real exit mapping: a missing
    no-mistakes costs the instance exactly what a missing engine binary does.
    Only the tooling checks run — the rest need an instance, not a tmpdir."""
    found = _tooling_findings(monkeypatch, tmp_path, without=missing)
    monkeypatch.setattr(cli, "run_doctor", lambda store, registry: DoctorReport(list(found.values())))
    assert cli._cmd_doctor(argparse.Namespace(json=False)) == code


@pytest.mark.parametrize(
    "verdict,code",
    [(Verdict.OK, 0), (Verdict.WARN, 0), (Verdict.FAIL, 1), (Verdict.UNKNOWN, 1)],
)
def test_devclaw_doctor_exits_nonzero_on_fail_and_unknown(monkeypatch, capsys, verdict, code):
    monkeypatch.setattr(cli, "run_doctor", lambda store, registry: DoctorReport([Finding("x", verdict, "e")]))
    assert cli._cmd_doctor(argparse.Namespace(json=False)) == code
