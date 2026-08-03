"""P2b (host-side): the gh READ adapter — argv construction, fail-LOUD error
handling (a gh failure must never masquerade as "no map"), and integration with
parse_map. The subprocess is injected, so no real gh/network."""

from __future__ import annotations

import json
import subprocess

import pytest

from devclaw.goal.wayfinder_gh import (
    WayfinderGhError,
    fetch_map_issues,
    read_map,
)


def _fake_run(*, returncode=0, stdout="", stderr="", capture_argv=None):
    def run(argv, **kwargs):
        if capture_argv is not None:
            capture_argv.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    return run


_MAP_JSON = json.dumps([
    {"number": 1, "title": "map", "state": "OPEN",
     "body": "## Destination\nShip /api/crons", "labels": [{"name": "wayfinder:map"}]},
    {"number": 2, "title": "record vs class?", "state": "CLOSED",
     "body": "Resolution: record", "labels": [{"name": "wayfinder:grilling"}]},
    {"number": 3, "title": "write endpoint", "state": "OPEN",
     "body": "Blocked by #2", "labels": [{"name": "wayfinder:task"}]},
])


# ---- fetch_map_issues -------------------------------------------------------


def test_fetch_returns_parsed_list_and_builds_correct_argv():
    seen: list = []
    out = fetch_map_issues("owner/repo", run=_fake_run(stdout=_MAP_JSON, capture_argv=seen))
    assert isinstance(out, list) and len(out) == 3
    argv = seen[0]
    assert argv[:3] == ["gh", "issue", "list"]
    assert "owner/repo" in argv
    assert "--json" in argv and "number,title,body,state,labels" in argv
    assert "--state" in argv and "all" in argv


def test_fetch_raises_on_nonzero_exit_not_empty_list():
    # the load-bearing fail-LOUD contract: gh broke != "no map"
    with pytest.raises(WayfinderGhError):
        fetch_map_issues("owner/repo", run=_fake_run(returncode=1, stderr="not found"))


def test_fetch_raises_on_unparseable_json():
    with pytest.raises(WayfinderGhError):
        fetch_map_issues("owner/repo", run=_fake_run(stdout="not json{"))


def test_fetch_raises_on_non_list_json():
    with pytest.raises(WayfinderGhError):
        fetch_map_issues("owner/repo", run=_fake_run(stdout='{"number": 1}'))


def test_fetch_empty_stdout_is_zero_issues_not_error():
    assert fetch_map_issues("owner/repo", run=_fake_run(stdout="")) == []
    assert fetch_map_issues("owner/repo", run=_fake_run(stdout="   ")) == []


def test_fetch_raises_when_gh_missing():
    def boom(argv, **kwargs):
        raise FileNotFoundError("gh not on PATH")
    with pytest.raises(WayfinderGhError):
        fetch_map_issues("owner/repo", run=boom)


# ---- read_map (fetch + parse) ----------------------------------------------


def test_read_map_parses_into_model():
    m = read_map("owner/repo", run=_fake_run(stdout=_MAP_JSON))
    assert m is not None
    assert m.map_number == 1
    assert m.destination == "Ship /api/crons"
    assert [t.number for t in m.tickets] == [2, 3]
    # #2 resolved (closed) → #3 is the frontier the tick would dispatch
    assert m.tickets[1].blocked_by == (2,)


def test_read_map_none_when_no_map_issue():
    tickets_only = json.dumps([
        {"number": 2, "title": "q", "state": "OPEN", "body": "",
         "labels": [{"name": "wayfinder:task"}]},
    ])
    assert read_map("owner/repo", run=_fake_run(stdout=tickets_only)) is None


def test_read_map_propagates_gh_error_never_swallows():
    # a gh failure must surface (cache-fallback/block-legibly), NOT become None
    with pytest.raises(WayfinderGhError):
        read_map("owner/repo", run=_fake_run(returncode=1, stderr="rate limited"))
