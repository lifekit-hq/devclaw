"""GitHub is the state (spec 046). Every read and every verb the goal layer
needs from the forge, through the ``gh`` CLI under one wall-clock bound.

Reads: the PR for a branch (head, state, mergeability, check rollup), the
failing check logs, an issue's live body, the comments that carry an
instruction or a devclaw record. Verbs: post a comment, squash-merge.
Everything is best-effort in one direction — a failure reads as ``unknown``
or ``(-1, reason)``, never an exception into the tick.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Optional

from .. import config as _config

GH_TIMEOUT_S = 20.0

_OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_NO_PR_MARKERS = ("no pull requests found", "could not find pull request", "no open pull request")
_BAD_CONCLUSIONS = {"failure", "timed_out", "action_required", "cancelled", "error", "stale"}
_INFRA_CONCLUSIONS = {"startup_failure"}
_OK_CONCLUSIONS = {"success", "neutral", "skipped"}
_PENDING_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested", "expected"}

#: devclaw's own comments carry this marker so they are never read as an
#: owner instruction: ``<!-- devclaw:<kind> k=v ... -->``
MARKER_RE = re.compile(r"<!--\s*devclaw:(\w+)((?:\s+\w+=\S+)*)\s*-->")


async def run_bounded(*argv: str, cwd: "str | None" = None,
                      timeout_s: "float | None" = None) -> tuple[int, str]:
    """``(returncode, combined output)``; spawn failure or timeout ⇒ ``(-1, why)``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return -1, f"{exc.__class__.__name__}: {exc}"
    bound = GH_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=bound)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return -1, f"timeout after {bound:g}s: {' '.join(argv[:3])}"
    except Exception as exc:  # noqa: BLE001
        return -1, f"{exc.__class__.__name__}: {exc}"
    rc = proc.returncode
    assert rc is not None
    return rc, out.decode(errors="replace").strip()


async def gh(*args: str, cwd: "str | None" = None) -> tuple[int, str]:
    return await run_bounded("gh", *args, cwd=cwd)


def _parse_json(rc: int, out: str) -> Optional[object]:
    if rc != 0:
        return None
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError:
        return None


def owner_repo(repo_url: str) -> Optional[str]:
    """``https://github.com/o/r.git`` / ``git@github.com:o/r.git`` → ``o/r``."""
    if not repo_url:
        return None
    m = _OWNER_REPO_RE.search(repo_url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


# ---- the PR and its checks ----------------------------------------------------

@dataclass(frozen=True)
class PrFacts:
    """One read of the goal branch's PR. ``state``: none | open | merged |
    closed | conflicting. ``ci``: none | green | red | pending | unknown |
    no_workflows | infra_broken."""

    state: str
    number: int = 0
    url: str = ""
    head_sha: str = ""
    base: str = ""
    ci: str = "none"
    ci_detail: str = ""
    failing_names: tuple[str, ...] = ()
    failing_logs: tuple[tuple[str, str], ...] = ()


def combine_checks(rollup: Optional[list[dict]], *, required: Optional[frozenset[str]],
                   workflows_present: bool) -> tuple[str, str, tuple[str, ...]]:
    """Fold a PR's ``statusCheckRollup`` into ``(ci, detail, failing_names)``. Pure."""
    if rollup is None:
        return "unknown", "could not read the PR's check rollup", ()
    if not rollup and not workflows_present:
        return "no_workflows", "no .github/workflows on the base branch", ()
    seen: set[str] = set()
    failing: list[str] = []
    pending: list[str] = []
    settled: dict[str, int] = {}
    for it in rollup:
        name = str(it.get("name") or it.get("context") or "").strip()
        if required is not None and name not in required:
            continue
        seen.add(name)
        status = str(it.get("status") or "").lower()
        conclusion = str(it.get("conclusion") or it.get("state") or "").lower()
        if status in _PENDING_STATUSES or conclusion in _PENDING_STATUSES or (
            status and status != "completed" and not conclusion
        ):
            pending.append(name)
            continue
        settled[conclusion] = settled.get(conclusion, 0) + 1
        if conclusion in _BAD_CONCLUSIONS:
            failing.append(name)
    if required is not None:
        pending.extend(sorted(required - seen))
    summary = ", ".join(f"{n}× {c or '(none)'}" for c, n in sorted(settled.items())) or "no settled checks"
    if failing:
        return "red", f"{len(failing)} failing: {', '.join(failing)} ({summary})", tuple(failing)
    if pending:
        return "pending", f"{len(pending)} still running: {', '.join(pending)} ({summary})", ()
    if not settled:
        return "pending", "workflows exist but no check has reported for this head yet", ()
    infra = sum(n for c, n in settled.items() if c in _INFRA_CONCLUSIONS)
    ok = sum(n for c, n in settled.items() if c in _OK_CONCLUSIONS)
    if infra and not ok:
        return "infra_broken", f"{infra} check(s) died at startup — CI never executed ({summary})", ()
    return "green", f"{ok} checks green ({summary})", ()


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TOKEN_RE = re.compile(r"\b(gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}")
_AUTH_RE = re.compile(r"(?i)(authorization:\s*(?:bearer|token|basic)\s+)\S+")


def scrub_log(text: str) -> str:
    text = _ANSI_RE.sub("", text or "")
    text = _TOKEN_RE.sub(lambda m: m.group(1) + "***", text)
    return _AUTH_RE.sub(lambda m: m.group(1) + "***", text)


def tail_by_job(log: str, *, lines: int) -> dict[str, str]:
    per_job: dict[str, list[str]] = {}
    for raw in (log or "").splitlines():
        parts = raw.split("\t", 2)
        if len(parts) < 3:
            continue
        per_job.setdefault(parts[0].strip(), []).append(parts[2].rstrip())
    return {job: "\n".join(rows[-lines:]) for job, rows in per_job.items()}


async def failed_check_logs(slug: str, head_sha: str, failing_names: tuple[str, ...],
                            *, lines: int) -> tuple[tuple[str, str], ...]:
    """The bounded tail of every failing check's job log — the fact the
    session cannot read itself (the sandbox holds no GitHub credential)."""
    if not head_sha or not failing_names or lines <= 0:
        return ()
    rc, out = await gh("run", "list", "--repo", slug, "--commit", head_sha,
                       "--json", "databaseId,conclusion,name", "--limit", "20")
    runs = _parse_json(rc, out)
    if not isinstance(runs, list):
        return ()
    tails: dict[str, str] = {}
    for r in runs:
        if not isinstance(r, dict) or str(r.get("conclusion") or "").lower() not in _BAD_CONCLUSIONS:
            continue
        rc_l, log = await gh("run", "view", str(r.get("databaseId")), "--repo", slug, "--log-failed")
        if rc_l != 0:
            continue
        for job, tail in tail_by_job(scrub_log(log), lines=lines).items():
            tails.setdefault(job, tail)
    return tuple((name, tails[name]) for name in failing_names if name in tails)


async def pr_facts(repo_url: str, branch: str) -> PrFacts:
    """The goal branch's PR, its head, its mergeability, its check rollup."""
    slug = owner_repo(repo_url)
    if not slug:
        return PrFacts("unknown", ci="unknown", ci_detail=f"not a GitHub remote: {repo_url!r}")
    rc, out = await gh("pr", "view", branch, "--repo", slug, "--json",
                       "number,url,state,headRefOid,baseRefName,statusCheckRollup,mergeable")
    if rc != 0:
        if any(m in out.lower() for m in _NO_PR_MARKERS):
            return PrFacts("none")
        return PrFacts("unknown", ci="unknown", ci_detail=f"could not read the PR: {out[:200]}")
    pr = _parse_json(rc, out)
    if not isinstance(pr, dict):
        return PrFacts("unknown", ci="unknown", ci_detail="unparseable PR payload")
    number = int(pr.get("number") or 0)
    url = str(pr.get("url") or "")
    head = str(pr.get("headRefOid") or "")
    base = str(pr.get("baseRefName") or "")
    gh_state = str(pr.get("state") or "").upper()
    if gh_state == "MERGED":
        return PrFacts("merged", number, url, head, base, ci="none")
    if gh_state == "CLOSED":
        return PrFacts("closed", number, url, head, base, ci="none")
    if str(pr.get("mergeable") or "").upper() == "CONFLICTING":
        return PrFacts("conflicting", number, url, head, base, ci="none",
                       ci_detail="the PR conflicts with its base; GitHub runs no checks on it")
    rollup_raw = pr.get("statusCheckRollup")
    rollup = [it for it in rollup_raw if isinstance(it, dict)] if isinstance(rollup_raw, list) else None
    required: Optional[frozenset[str]] = None
    if base:
        rc_p, out_p = await gh("api", f"repos/{slug}/branches/{base}/protection/required_status_checks/contexts")
        contexts = _parse_json(rc_p, out_p)
        if isinstance(contexts, list):
            required = frozenset(str(c) for c in contexts if c) or None
    workflows_present = True
    if not rollup:
        rc_wf, out_wf = await gh("api", f"repos/{slug}/contents/.github/workflows?ref={base or 'HEAD'}",
                                 "--jq", "length")
        workflows_present = rc_wf == 0 and out_wf.strip().isdigit() and int(out_wf.strip()) > 0
    ci, detail, failing = combine_checks(rollup, required=required, workflows_present=workflows_present)
    logs: tuple[tuple[str, str], ...] = ()
    if ci == "red":
        logs = await failed_check_logs(slug, head, failing, lines=_config.ci_log_tail_lines())
    return PrFacts("open", number, url, head, base, ci, detail, failing, logs)


# ---- issues ------------------------------------------------------------------

@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    state: str  # open | closed


class IssueError(Exception):
    """A referenced issue could not be read — the contract is load-bearing,
    so this blocks loudly; it never degrades to an empty contract."""


async def fetch_issue(repo_url: str, number: int) -> Issue:
    slug = owner_repo(repo_url)
    if not slug:
        raise IssueError(f"cannot derive owner/repo from {repo_url!r}")
    rc, out = await gh("api", f"repos/{slug}/issues/{number}")
    if rc != 0:
        raise IssueError(f"gh could not fetch {slug}#{number}: {out[:200]}")
    data = _parse_json(rc, out)
    if not isinstance(data, dict) or "state" not in data:
        raise IssueError(f"unexpected gh response for {slug}#{number}")
    return Issue(number=number, title=str(data.get("title") or ""),
                 body=str(data.get("body") or ""), state=str(data.get("state") or "open"))


CONTRACT_HEADINGS = ("Done when", "Acceptance")


def extract_acceptance(body: str) -> Optional[str]:
    """The ``## Done when`` / ``## Acceptance`` section of an issue body, or None."""
    if not body:
        return None
    alts = "|".join(re.escape(h) for h in CONTRACT_HEADINGS)
    m = re.search(rf"^(#{{2,4}})\s*(?:{alts})\b.*$", body, flags=re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    level = len(m.group(1))
    tail = body[m.end():]
    stop = re.search(rf"^#{{1,{level}}}\s", tail, flags=re.MULTILINE)
    section = (tail[: stop.start()] if stop else tail).strip()
    return section or None


# ---- comments: the owner's one channel, and devclaw's own records --------------

@dataclass(frozen=True)
class Comment:
    id: int
    number: int          # the issue or PR it sits on
    author: str
    body: str
    url: str
    created_at: str
    #: devclaw's own record kind (``block`` / ``verdict`` / ``decision`` / ...) or ""
    marker: str = ""
    marker_fields: tuple[tuple[str, str], ...] = ()

    @property
    def is_instruction(self) -> bool:
        return not self.marker and _config.mention().lower() in self.body.lower()

    def field(self, key: str, default: str = "") -> str:
        for k, v in self.marker_fields:
            if k == key:
                return v
        return default


def parse_marker(body: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    m = MARKER_RE.search(body or "")
    if not m:
        return "", ()
    fields = tuple(tuple(kv.split("=", 1)) for kv in m.group(2).split())  # type: ignore[misc]
    return m.group(1), tuple((k, v) for k, v in fields)


def marker(kind: str, **fields: object) -> str:
    return "<!-- devclaw:" + kind + "".join(f" {k}={v}" for k, v in fields.items()) + " -->"


async def list_comments(repo_url: str, number: int) -> list[Comment]:
    """Every comment on one issue/PR, oldest first (bounded pages)."""
    slug = owner_repo(repo_url)
    if not slug or not number:
        return []
    rc, out = await gh("api", f"repos/{slug}/issues/{number}/comments?per_page=100", "--paginate",
                       "--jq", ".[] | {id, body, user: .user.login, html_url, created_at}")
    if rc != 0:
        return []
    comments: list[Comment] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        body = str(d.get("body") or "")
        kind, fields = parse_marker(body)
        comments.append(Comment(
            id=int(d.get("id") or 0), number=number, author=str(d.get("user") or ""),
            body=body, url=str(d.get("html_url") or ""), created_at=str(d.get("created_at") or ""),
            marker=kind, marker_fields=fields,
        ))
    comments.sort(key=lambda c: c.id)
    return comments


async def post_comment(repo_url: str, number: int, body: str) -> str:
    """Post a comment; returns its URL or "" on failure."""
    slug = owner_repo(repo_url)
    if not slug or not number:
        return ""
    rc, out = await gh("api", f"repos/{slug}/issues/{number}/comments", "-f", f"body={body}",
                       "--jq", ".html_url")
    return out.strip() if rc == 0 else ""


# ---- the merge (the confirmed-achieved close) -----------------------------------

async def squash_merge(repo_url: str, pr_url: str) -> tuple[str, str]:
    """``("merged" | "conflict" | "error", detail)``."""
    rc, out = await gh("pr", "merge", pr_url, "--squash", "--delete-branch")
    if rc == 0:
        return "merged", out[:200]
    low = out.lower()
    if any(m in low for m in ("not mergeable", "merge conflict", "conflicting")):
        return "conflict", out[:300]
    return "error", out[:300]


async def sync_workspace_to_default(workspace_dir: str) -> None:
    """Best-effort post-merge sync of a checkout onto the merged default branch."""
    rc, _ = await run_bounded("git", "fetch", "origin", cwd=workspace_dir)
    if rc != 0:
        return
    rc, head = await run_bounded("git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short", cwd=workspace_dir)
    default = head.rsplit("/", 1)[-1] if rc == 0 and head else "main"
    await run_bounded("git", "checkout", "-f", default, cwd=workspace_dir)
    await run_bounded("git", "reset", "--hard", f"origin/{default}", cwd=workspace_dir)
