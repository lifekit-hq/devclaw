"""Filing a development problem once (tinyspec file-intake-idempotent).

Narrow on purpose: this files, or counts onto, a GitHub issue in a registered
project's repository. It starts no goal and changes no code. GitHub is the
state — the key is a label on the issue, the count lives in its body.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Optional

from fastmcp.exceptions import ToolError

from ...goal.github import gh, owner_repo, post_comment
from .._state import mcp
from ._common import _resolve_project_or_reject

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


async def file_intake_in(slug: str, title: str, body: str, fingerprint: str = "") -> dict:
    """Idempotent by repo + fingerprint. Returns ``{url, number, count, created, fingerprint}``."""
    title = (title or "").strip()
    if not title:
        raise ToolError("file_intake requires a title")
    fp = derive_fingerprint(title, fingerprint)
    label = LABEL_PREFIX + fp
    lock = _locks.setdefault((slug, fp), asyncio.Lock())
    async with lock:
        issues = await _issues_with_label(slug, label)
        open_issue = next((i for i in issues if str(i.get("state")) == "open"), None)
        if open_issue is not None:
            return await _count_onto(slug, fp, open_issue, body)
        closed = next((i for i in issues if str(i.get("state")) == "closed"), None)
        return await _create(slug, fp, label, title, body, closed)


async def _count_onto(slug: str, fp: str, issue: dict, body: str) -> dict:
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
    if not await post_comment(f"https://github.com/{slug}", number, note):
        raise ToolError(f"file_intake: counted {slug}#{number} but could not post the occurrence comment")
    return {"url": url, "number": number, "count": count, "created": False, "fingerprint": fp}


async def _create(slug: str, fp: str, label: str, title: str, body: str, closed: Optional[dict]) -> dict:
    rc, out = await gh("api", "-X", "POST", f"repos/{slug}/labels", "-f", f"name={label}",
                       "-f", "color=ededed", "-f", "description=devclaw intake fingerprint")
    if rc != 0 and "already_exists" not in out:
        raise ToolError(f"file_intake: could not create the fingerprint label: {out[:200]}")
    text = _header(fp, 1) + (body or "").strip()
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
async def file_intake(project_id: str, title: str, body: str = "", fingerprint: str = "") -> str:
    """File a development-class problem as a GitHub issue, once. ``project_id``
    is a registered project (see ``list_projects``); the issue is filed on that
    project's repository. ``fingerprint`` identifies the failure: pass a short
    stable slug (``[a-z0-9._-]``, e.g. ``ci-timeout-build``) so every
    recurrence matches. When omitted it is derived from ``title`` — lowercased,
    numbers and hex ids collapsed, whitespace collapsed, hashed — so keep the
    title free of per-run noise. If an OPEN issue already carries this repo +
    fingerprint, no new issue is made: its count is bumped and ``body`` is
    posted as a comment. A closed one opens a new issue that references it.
    Returns JSON ``{url, number, count, created, fingerprint}``. This only
    files; it starts no goal and changes no code."""
    resolved = _resolve_project_or_reject(project_id, "file_intake")
    slug = owner_repo(resolved.repo_url or "")
    if not slug:
        raise ToolError(f"project {project_id!r} has no GitHub repo_url — set it with update_project")
    return json.dumps(await file_intake_in(slug, title, body, fingerprint), indent=2)


__all__ = ["file_intake", "file_intake_in", "derive_fingerprint"]
