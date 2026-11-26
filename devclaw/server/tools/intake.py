"""Filing a development problem once (tinyspec file-intake-idempotent).

Narrow on purpose: this files, or counts onto, a GitHub issue in a registered
project's repository. It starts no goal and changes no code. GitHub is the
state — the key is a label on the issue, the count lives in its body.

The envelope fields and the receipt are the build-standards entry "Envelope and
receipt at every door" (vault ``memory/system/build-standards.md``, 2026-11-16,
decision 0016); this module enforces them where the issue is made and cites
that entry rather than restating it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Optional

from fastmcp.exceptions import ToolError

from ...goal.github import gh, owner_repo, post_comment
from ...project_registry import UnknownProject
from .._state import mcp, registry

LABEL_PREFIX = "intake:"
_PLAIN_FP_RE = re.compile(r"[a-z0-9._-]{1,40}")
_NUM_RE = re.compile(r"\b[0-9a-f]{7,}\b|\d+")
_COUNT_MARK_RE = re.compile(r"<!--\s*devclaw:intake fp=(\S+) count=(\d+)\s*-->")
_OCC_LINE_RE = re.compile(r"^\*\*Occurrences:\*\* \d+$", re.MULTILINE)
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def derive_fingerprint(title: str, fingerprint: str = "") -> str:
    given = (fingerprint or "").strip().lower()
    if given:
        return given if _PLAIN_FP_RE.fullmatch(given) else hashlib.sha256(given.encode()).hexdigest()[:16]
    norm = " ".join(_NUM_RE.sub("#", (title or "").lower()).split())
    return hashlib.sha256(norm.encode()).hexdigest()[:12]


def _header(fp: str, count: int) -> str:
    return f"<!-- devclaw:intake fp={fp} count={count} -->\n**Occurrences:** {count}\n\n"


def check_envelope(slug: str, project_id: str, owner: str, cls: str, acceptance: str) -> str:
    """The reason the door refuses this envelope, or ``""`` when it is accepted."""
    if not (owner or "").strip():
        return "missing `owner` (the repo this item is for)"
    if not (acceptance or "").strip():
        return "missing `acceptance` (the done-when the grade checks)"
    if owner.strip().lower() not in {slug.lower(), project_id.strip().lower()}:
        return f"`owner` {owner.strip()!r} is not this project's repo {slug!r}"
    if (cls or "dev").strip().lower() != "dev":
        return f"`class` {cls.strip()!r} is not one this door takes (dev)"
    return ""


def _envelope_block(fp: str, origin: str, owner: str, cls: str, acceptance: str, thread: str) -> str:
    def one(v: str) -> str:
        return " ".join((v or "").split()).replace("```", "'" * 3)

    rows = [("origin", one(origin)), ("owner", one(owner)), ("class", one(cls) or "dev"),
            ("fingerprint", fp), ("acceptance", one(acceptance)), ("thread", one(thread))]
    return "```devclaw-envelope\n" + "\n".join(f"{k}: {v}" for k, v in rows) + "\n```\n\n"


def rejected(reason: str) -> dict:
    return {"id": "", "url": "", "state": "rejected", "grade": {"ok": False, "reason": reason}}


def receipt(slug: str, result: dict) -> dict:
    """``{id, url, state}`` first, then the existing result fields."""
    state = "submitted" if result["created"] else "working"
    return {"id": f"{slug}#{result['number']}", "url": result["url"], "state": state,
            "grade": {"ok": True, "reason": ""}, **result}


def _json(rc: int, out: str, what: str) -> object:
    if rc != 0:
        raise ToolError(f"file_intake: {what} failed: {out[:200]}")
    try:
        return json.loads(out or "null")
    except ValueError:
        raise ToolError(f"file_intake: {what} returned unparseable output")


async def _issues_with_label(slug: str, label: str) -> list[dict]:
    rc, out = await gh("api", f"repos/{slug}/issues?labels={label}&state=all&per_page=100"
                       "&sort=created&direction=desc")
    data = _json(rc, out, "listing issues")
    return [i for i in (data if isinstance(data, list) else []) if isinstance(i, dict) and "pull_request" not in i]


async def file_intake_in(slug: str, title: str, body: str, fingerprint: str = "", envelope: str = "",
                         thread: str = "", label_prefix: str = LABEL_PREFIX) -> dict:
    """Idempotent by repo + fingerprint. Returns ``{url, number, count, created, fingerprint}``."""
    title = (title or "").strip()
    if not title:
        raise ToolError("file_intake requires a title")
    fp = derive_fingerprint(title, fingerprint)
    label = label_prefix + fp
    lock = _locks.setdefault((slug, fp), asyncio.Lock())
    async with lock:
        issues = await _issues_with_label(slug, label)
        open_issue = next((i for i in issues if str(i.get("state")) == "open"), None)
        if open_issue is not None:
            return await _count_onto(slug, fp, open_issue, body, thread)
        closed = next((i for i in issues if str(i.get("state")) == "closed"), None)
        return await _create(slug, fp, label, title, body, closed, envelope)


async def _count_onto(slug: str, fp: str, issue: dict, body: str, thread: str = "") -> dict:
    number = int(issue["number"])
    old = str(issue.get("body") or "")
    m = _COUNT_MARK_RE.search(old)
    count = (int(m.group(2)) if m else 1) + 1
    if m:
        new = _COUNT_MARK_RE.sub(f"<!-- devclaw:intake fp={fp} count={count} -->", old, count=1)
        new = _OCC_LINE_RE.sub(f"**Occurrences:** {count}", new, count=1)
    else:
        new = _header(fp, count) + old
    rc, out = await gh("api", "-X", "PATCH", f"repos/{slug}/issues/{number}", "-f", f"body={new}")
    _json(rc, out, "updating the issue count")
    url = str(issue.get("html_url") or "")
    note = f"Occurrence {count} of this failure.\n\n{(body or '').strip()}".strip()
    if (thread or "").strip():
        note += f"\n\nthread: {' '.join(thread.split())}"
    if not await post_comment(f"https://github.com/{slug}", number, note):
        raise ToolError(f"file_intake: counted {slug}#{number} but could not post the occurrence comment")
    return {"url": url, "number": number, "count": count, "created": False, "fingerprint": fp}


async def _create(slug: str, fp: str, label: str, title: str, body: str, closed: Optional[dict],
                  envelope: str = "") -> dict:
    rc, out = await gh("api", "-X", "POST", f"repos/{slug}/labels", "-f", f"name={label}",
                       "-f", "color=ededed", "-f", "description=devclaw intake fingerprint")
    if rc != 0 and "already_exists" not in out:
        raise ToolError(f"file_intake: could not create the fingerprint label: {out[:200]}")
    text = _header(fp, 1) + envelope + (body or "").strip()
    if closed is not None:
        text += f"\n\nA previous issue with this fingerprint was closed: #{int(closed['number'])}."
    rc, out = await gh("api", "-X", "POST", f"repos/{slug}/issues", "-f", f"title={title}",
                       "-f", f"body={text}", "-f", f"labels[]={label}")
    data = _json(rc, out, "creating the issue")
    if not isinstance(data, dict) or "number" not in data:
        raise ToolError("file_intake: unexpected response creating the issue")
    return {"url": str(data.get("html_url") or ""), "number": int(data["number"]), "count": 1,
            "created": True, "fingerprint": fp}


@mcp.tool
async def file_intake(project_id: str, title: str, owner: str, acceptance: str, body: str = "",
                      fingerprint: str = "", origin: str = "", class_: str = "dev", thread: str = "") -> str:
    """File a development-class problem as a GitHub issue, once, and get a
    receipt. The envelope and receipt are the build-standards entry "Envelope
    and receipt at every door" (2026-11-16, decision 0016); the fields are
    ``origin``, ``owner``, ``class`` (passed here as ``class_``; this door takes
    ``dev``), ``fingerprint``, ``acceptance`` and ``thread``. ``project_id`` is a
    registered project (see ``list_projects``); ``owner`` is that project's repo
    (``owner/name``) and ``acceptance`` is the done-when; both are required, and
    an envelope missing one is refused with a reason, never guessed. ``thread``
    is the caller's thread id, written into the issue so a reply finds its way
    back. ``fingerprint`` identifies the failure: a short stable slug
    (``[a-z0-9._-]``) so every recurrence matches; when omitted it is derived
    from ``title`` (numbers and hex ids collapsed), so keep the title free of
    per-run noise. An OPEN issue with this repo + fingerprint gets its count
    bumped and ``body`` posted as a comment; a closed one opens a new issue that
    references it. Returns the receipt as JSON ``{id, url, state, grade, number,
    count, created, fingerprint}`` with ``state`` ``submitted`` (new issue),
    ``working`` (a repeat that added a count) or ``rejected`` (the envelope
    failed its grade or the project is unknown; ``grade.reason`` says why). This only files; it starts no
    goal and changes no code."""
    try:
        target = registry.resolve_intake(project_id)
    except UnknownProject:
        valid = ", ".join(registry.intake_project_ids())
        return json.dumps(rejected(f"unknown project_id {project_id!r}; valid projects: {valid}"), indent=2)
    except ValueError as exc:
        raise ToolError(str(exc))
    slug = owner_repo(target.repo_url)
    if not slug:
        raise ToolError(f"project {project_id!r} has no GitHub repo_url — set it with update_project")
    reason = check_envelope(slug, project_id, owner, class_, acceptance)
    if reason:
        return json.dumps(rejected(reason), indent=2)
    fp = derive_fingerprint(title, fingerprint)
    block = _envelope_block(fp, origin, owner, class_, acceptance, thread)
    result = await file_intake_in(slug, title, body, fingerprint, block, thread, target.label_prefix)
    return json.dumps(receipt(slug, result), indent=2)


__all__ = ["file_intake", "file_intake_in", "derive_fingerprint", "check_envelope", "receipt", "rejected"]
