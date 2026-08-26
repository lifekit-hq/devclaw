"""Anti-drift guard for the chunk grammar (spec 021, contracts/chunk-grammar.md).

The host parser (devclaw/goal/slice_guard.py) and the runner's standalone
mirror (runner/runner.py — zero-dep, cannot import the host module) must parse
the SAME rows from the same tasks.md. The shared fixtures in
tests/fixtures/chunk_grammar/ are the frozen contract; a grammar change must
update the contract file, BOTH parsers, and these fixtures in one PR.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from devclaw.goal import slice_guard

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "chunk_grammar"
_RUNNER = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("runner_chunk_grammar", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runner_mod():
    return _load_runner()


@pytest.mark.parametrize("fixture", sorted(p.name for p in _FIXTURES.glob("*.md")))
def test_host_and_runner_parsers_agree_on_shared_fixtures(runner_mod, fixture):
    text = (_FIXTURES / fixture).read_text(encoding="utf-8")
    host_rows = slice_guard._task_rows(text)
    runner_rows = runner_mod._chunk_task_rows(text)
    assert host_rows == runner_rows, (
        f"grammar drift on {fixture}: host={host_rows!r} runner={runner_rows!r} "
        "— update contracts/chunk-grammar.md + BOTH parsers together"
    )


def test_valid_fixture_parses_the_expected_slices(runner_mod):
    text = (_FIXTURES / "valid.md").read_text(encoding="utf-8")
    rows = runner_mod._chunk_task_rows(text)
    stories = {s for (_k, s, _c) in rows if s}
    assert stories == {"US1", "US2"}
    checked = {k for (k, _s, c) in rows if c}
    assert checked == {"T001", "T010", "T011"}


def test_watcher_complete_slice_semantics_match_contract(runner_mod, tmp_path):
    """A slice is complete iff every row carrying its tag is checked; untagged
    rows never make a slice by themselves (contracts/chunk-grammar.md)."""
    ws = tmp_path / "ws"
    (ws / "specs" / "001-f").mkdir(parents=True)
    (ws / "specs" / "001-f" / "tasks.md").write_text(
        (_FIXTURES / "valid.md").read_text(encoding="utf-8")
    )
    watcher = runner_mod._SliceWatcher(str(ws))
    watcher.arm()
    # valid.md: US1 complete already, US2 incomplete → only ONE incomplete
    # slice at start → disarmed (FR-005 single-chunk fast path).
    assert watcher.armed is False
    complete = watcher._complete_slices(watcher._read_rows())
    assert complete == {("001-f", "US1")}
