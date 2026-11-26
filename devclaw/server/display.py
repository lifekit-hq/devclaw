"""Display text read from GitHub, never stored (#1058).

The issue is the record from ``ready`` on, and a decision is the comment it
posted. The title a goal row shows and the text a decision shows are read live
here, from the issue and the decision's ``comment_url``. The stored copies
(``goals.objective``, ``decisions.text``) are display-only: they stand in only
while a live read has not arrived, and no tick, gate or merge path reads them.

Reads are cached per process (``_TTL_S``) and bounded per request
(``_BUDGET_S``): a cold console load shows what arrived in time — the goal id
stands in for a missing title — and the rest fills on the next poll.
"""

from __future__ import annotations

import asyncio
import functools
import re
import time
from typing import Awaitable, Callable, Optional

from ..goal import github as _gh

_TTL_S = 900.0
_BUDGET_S = 2.0
_CONCURRENCY = 8

_cache: dict[tuple, tuple[float, str]] = {}
_pending: dict[tuple, asyncio.Task] = {}
_sem: Optional[asyncio.Semaphore] = None

_COMMENT_URL_RE = re.compile(r"github\.com/([^/]+/[^/]+)/(?:issues|pull)/\d+#issuecomment-(\d+)")


async def _issue_title(repo_url: str, number: int) -> str:
    return (await _gh.fetch_issue(repo_url, number)).title


async def _comment_body(url: str) -> str:
    """The live body of the comment at ``url`` (an ``html_url``), or ""."""
    m = _COMMENT_URL_RE.search(url or "")
    if not m:
        return ""
    rc, out = await _gh.gh("api", f"repos/{m.group(1)}/issues/comments/{m.group(2)}", "--jq", ".body")
    return out.strip() if rc == 0 else ""


#: injectable readers (tests pass fakes)
title_reader: Callable[[str, int], Awaitable[str]] = _issue_title
comment_reader: Callable[[str], Awaitable[str]] = _comment_body


async def _read_title(repo: str, n: int) -> str:
    return await title_reader(repo, n)


async def _read_comment(url: str) -> str:
    return await comment_reader(url)


def _ensure(key: tuple, make: Callable[[], Awaitable[str]]) -> Optional[asyncio.Task]:
    """A task fetching ``key`` when its cached value is missing or old, else None."""
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < _TTL_S:
        return None
    task = _pending.get(key)
    if task is None:
        async def run() -> None:
            global _sem
            if _sem is None:
                _sem = asyncio.Semaphore(_CONCURRENCY)
            try:
                async with _sem:
                    value = await make()
            except Exception:  # noqa: BLE001 — display text never fails a read
                value = hit[1] if hit else ""
            finally:
                _pending.pop(key, None)
            _cache[key] = (time.monotonic(), value)
        task = _pending[key] = asyncio.get_running_loop().create_task(run())
    return task


def _title_key(repo: str, n: int) -> tuple:
    return ("issue", repo, n)


def _comment_key(url: str) -> tuple:
    return ("comment", url)


def _cached(key: tuple) -> str:
    hit = _cache.get(key)
    return hit[1] if hit else ""


def _comment_urls(row: dict) -> list[dict]:
    """The dicts in ``row`` that carry a ``commentUrl`` and want its text."""
    holders = list(row.get("decisions") or [])
    answered = (row.get("attention") or {}).get("answered")
    if isinstance(answered, dict):
        holders.append(answered)
    return [h for h in holders if isinstance(h, dict) and h.get("commentUrl")]


async def with_display(rows: list[dict]) -> list[dict]:
    """Fill each goal row's ``objective`` (its issues' titles) and every
    decision's ``text`` (its comment's body) from GitHub. Mutates and returns."""
    tasks = []
    for row in rows:
        repo = str(row.get("repoUrl") or "")
        for n in row.get("issues") or []:
            tasks.append(_ensure(_title_key(repo, n), functools.partial(_read_title, repo, n)))
        for h in _comment_urls(row):
            url = str(h["commentUrl"])
            tasks.append(_ensure(_comment_key(url), functools.partial(_read_comment, url)))
    waiting = [t for t in tasks if t is not None]
    if waiting:
        await asyncio.wait(waiting, timeout=_BUDGET_S)
    for row in rows:
        repo = str(row.get("repoUrl") or "")
        titles = [_cached(_title_key(repo, n)) for n in row.get("issues") or []]
        row["objective"] = "; ".join(t for t in titles if t)[:300] or row.get("objective") or ""
        for h in _comment_urls(row):
            h["text"] = _cached(_comment_key(str(h["commentUrl"]))) or h.get("text") or ""
    return rows
