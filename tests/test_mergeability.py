"""The mergeability probe — the read-only advisory that survived #641.

Auto-merge and the program PR-stack reconciler were deleted; this module is what
is left of the old ``goal/merge.py``, and it only ASKS. The probe must be
best-effort in one specific direction: an unknown answer means "say nothing",
never "all clear" — a settle that treats UNKNOWN as mergeable would report a
conflicting delivery as landable, which is the failure #394 added it for.
"""

from __future__ import annotations

import pytest

from devclaw.goal import mergeability


@pytest.mark.asyncio
async def test_empty_url_never_shells_out(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("gh must not be spawned for an empty url")

    monkeypatch.setattr(mergeability, "_run_gh", _boom)
    assert await mergeability.pr_conflicting("") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rc,out,expected",
    [
        (0, "CONFLICTING", True),
        (0, "conflicting", True),     # verdict is upper-cased before compare
        (0, "MERGEABLE", False),
        (0, "UNKNOWN", None),         # GitHub still computing → say nothing
        (0, "", None),                # unparseable → say nothing
        (1, "CONFLICTING", None),     # gh failed → say nothing, NOT "all clear"
    ],
)
async def test_probe_maps_gh_verdicts_and_stays_silent_when_unsure(
    monkeypatch, rc, out, expected
):
    async def _fake_gh(*argv):
        return rc, out

    monkeypatch.setattr(mergeability, "_run_gh", _fake_gh)
    assert await mergeability.pr_conflicting("https://github.com/o/r/pull/1") is expected


@pytest.mark.asyncio
async def test_probe_never_raises_when_gh_is_missing(monkeypatch):
    # _run_gh converts a spawn failure into (-1, "<Exc>: msg") rather than
    # raising, because this runs inside the tick.
    async def _explode(*argv, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(mergeability.asyncio, "create_subprocess_exec", _explode)
    assert await mergeability.pr_conflicting("https://github.com/o/r/pull/1") is None
