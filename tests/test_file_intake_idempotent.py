"""file_intake is idempotent by repo + fingerprint (tinyspec file-intake-idempotent).

A repeat failure adds a count to the open issue; it never opens a second one,
even when two callers race. The forge is an in-memory fake behind ``gh``."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from devclaw.server.tools import intake


class FakeForge:
    def __init__(self):
        self.issues: dict[str, list[dict]] = {}
        self.comments: list[tuple[str, int, str]] = []

    async def gh(self, *args: str):
        await asyncio.sleep(0)  # yield, so a racing caller interleaves here
        a = list(args)
        route = next(x for x in a if x.startswith("repos/"))
        slug = re.match(r"repos/([^/]+/[^/]+)/", route).group(1)
        fields = {k: v for k, v in (x.split("=", 1) for x in a if "=" in x and not x.startswith("repos/"))}
        if "/labels" in route:
            return 0, "{}"
        if "-X" in a and "PATCH" in a:
            n = int(route.rsplit("/", 1)[1])
            next(i for i in self.issues[slug] if i["number"] == n)["body"] = fields["body"]
            return 0, "{}"
        if "-X" in a and "POST" in a:
            n = len(self.issues.setdefault(slug, [])) + 1
            issue = {"number": n, "state": "open", "title": fields["title"], "body": fields["body"],
                     "html_url": f"https://github.com/{slug}/issues/{n}", "labels": [fields["labels[]"]]}
            self.issues[slug].append(issue)
            return 0, json.dumps(issue)
        label = re.search(r"labels=([^&]+)", route).group(1)
        found = [i for i in reversed(self.issues.get(slug, [])) if label in i["labels"]]
        return 0, json.dumps(found)

    async def post_comment(self, repo_url: str, number: int, body: str) -> str:
        self.comments.append((repo_url, number, body))
        return "https://github.com/c"


@pytest.fixture
def forge(monkeypatch):
    f = FakeForge()
    monkeypatch.setattr(intake, "gh", f.gh)
    monkeypatch.setattr(intake, "post_comment", f.post_comment)
    intake._locks.clear()
    return f


def _file(slug="o/r", title="build timed out after 90s", body="log", fp=""):
    return intake.file_intake_in(slug, title, body, fp)


async def test_first_filing_opens_one_issue_with_count_one(forge):
    out = await _file(fp="ci-timeout")
    assert out["created"] and out["count"] == 1 and out["number"] == 1
    assert len(forge.issues["o/r"]) == 1 and forge.issues["o/r"][0]["labels"] == ["intake:ci-timeout"]


async def test_a_repeat_counts_onto_the_open_issue(forge):
    first = await _file(fp="ci-timeout")
    again = await _file(fp="ci-timeout", body="second log")
    assert again["url"] == first["url"] and again["count"] == 2 and not again["created"]
    assert len(forge.issues["o/r"]) == 1
    assert "count=2" in forge.issues["o/r"][0]["body"] and "**Occurrences:** 2" in forge.issues["o/r"][0]["body"]
    assert "second log" in forge.comments[-1][2]


async def test_the_fingerprint_is_derived_from_the_title_when_absent(forge):
    await _file(title="build 123 timed out after 90s")
    again = await _file(title="Build 456  timed out after 7s")
    assert again["count"] == 2 and len(forge.issues["o/r"]) == 1


async def test_the_same_fingerprint_in_another_repo_is_a_separate_issue(forge):
    a = await _file("o/a", fp="x")
    b = await _file("o/b", fp="x")
    assert a["created"] and b["created"] and b["count"] == 1


async def test_a_closed_issue_opens_a_new_one_that_references_it(forge):
    await _file(fp="x")
    forge.issues["o/r"][0]["state"] = "closed"
    again = await _file(fp="x")
    assert again["created"] and again["count"] == 1 and again["number"] == 2
    assert "#1" in forge.issues["o/r"][1]["body"]


async def test_a_concurrent_double_call_files_one_issue_counted_twice(forge):
    a, b = await asyncio.gather(_file(fp="x"), _file(fp="x"))
    assert len(forge.issues["o/r"]) == 1
    assert sorted(r["count"] for r in (a, b)) == [1, 2]
