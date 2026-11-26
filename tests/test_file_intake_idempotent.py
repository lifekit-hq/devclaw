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


# --- the envelope and the receipt (tinyspec file-intake-envelope) -------------


def test_a_refused_envelope_is_a_rejected_receipt_with_a_reason():
    cases = (("", "x", "dev", "owner"), ("o/r", " ", "dev", "acceptance"),
             ("x/other", "x", "dev", "owner"), ("o/r", "x", "ops", "class"))
    for owner, acc, cls, word in cases:
        reason = intake.check_envelope("o/r", "p", owner, cls, acc)
        assert word in reason
        out = intake.rejected(reason)
        assert out["state"] == "rejected" and out["id"] == "" and out["grade"]["reason"] == reason
    assert intake.check_envelope("o/r", "p", "o/r", "dev", "x") == ""


async def test_a_full_envelope_returns_a_submitted_receipt_and_writes_the_block(forge):
    block = intake._envelope_block("ci-timeout", "night-agent", "o/r", "dev", "the build stops timing out", "t-1")
    out = intake.receipt("o/r", await intake.file_intake_in("o/r", "build timed out", "log", "ci-timeout", block, "t-1"))
    assert (out["id"], out["state"], out["url"]) == ("o/r#1", "submitted", "https://github.com/o/r/issues/1")
    body = forge.issues["o/r"][0]["body"]
    assert "devclaw-envelope" in body and "thread: t-1" in body and "acceptance: the build stops timing out" in body


async def test_a_repeat_returns_a_working_receipt_with_the_count(forge):
    block = intake._envelope_block("x", "a", "o/r", "dev", "done", "t-1")
    await intake.file_intake_in("o/r", "t", "", "x", block, "t-1")
    out = intake.receipt("o/r", await intake.file_intake_in("o/r", "t", "again", "x", block, "t-2"))
    assert out["state"] == "working" and out["count"] == 2 and out["id"] == "o/r#1"
    assert "thread: t-2" in forge.comments[-1][2]


# --- the tool itself: the wiring between the envelope check and the filing ----


@pytest.fixture
def tool(forge, monkeypatch, tmp_path):
    from devclaw.project_registry import ProjectRegistry

    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    reg.create(id="p", name="p", repo_url="https://github.com/o/r", workspace_dir="/w")
    monkeypatch.setattr(intake, "registry", reg)
    return getattr(intake.file_intake, "fn", intake.file_intake)


async def test_the_tool_files_nothing_for_a_refused_envelope(tool, forge):
    out = json.loads(await tool("p", "build timed out", owner="o/r", acceptance="  ", fingerprint="ci-timeout"))
    assert out["state"] == "rejected" and "acceptance" in out["grade"]["reason"] and out["id"] == ""
    assert forge.issues == {} and forge.comments == []


async def test_the_tool_returns_the_receipt_and_the_issue_carries_the_same_fingerprint(tool, forge):
    out = json.loads(await tool("p", "build timed out", owner="o/r", acceptance="the build stops timing out",
                                fingerprint="ci-timeout", origin="night-agent", thread="t-1"))
    assert (out["id"], out["state"]) == ("o/r#1", "submitted") and out["fingerprint"] == "ci-timeout"
    issue = forge.issues["o/r"][0]
    assert issue["labels"] == ["intake:" + out["fingerprint"]]
    assert "fingerprint: ci-timeout" in issue["body"] and "thread: t-1" in issue["body"]
    again = json.loads(await tool("p", "build timed out", owner="o/r", acceptance="x", fingerprint="ci-timeout"))
    assert (again["id"], again["state"], again["count"]) == ("o/r#1", "working", 2)


# --- the vault and the fleet: owners with an issue queue and no workspace -----


@pytest.mark.parametrize("project_id,slug", [("vault", "dsdevq/memory"), ("fleet", "lifekit-hq/lifekit-stack")])
async def test_the_vault_and_the_fleet_file_and_count_like_any_project(tool, forge, project_id, slug):
    out = json.loads(await tool(project_id, "note rots", owner=slug, acceptance="it stops", fingerprint="rot"))
    assert (out["id"], out["state"], out["url"]) == (f"{slug}#1", "submitted", f"https://github.com/{slug}/issues/1")
    assert forge.issues[slug][0]["labels"] == ["intake:rot"]
    again = json.loads(await tool(project_id, "note rots", owner=project_id, acceptance="it stops", fingerprint="rot"))
    assert (again["id"], again["state"], again["count"]) == (f"{slug}#1", "working", 2)
    assert len(forge.issues[slug]) == 1


async def test_an_unknown_project_is_refused_with_the_valid_list(tool, forge):
    out = json.loads(await tool("nope", "t", owner="o/r", acceptance="x"))
    assert out["state"] == "rejected" and out["id"] == ""
    assert "'nope'" in out["grade"]["reason"] and "fleet, p, vault" in out["grade"]["reason"]
    assert forge.issues == {} and forge.comments == []
