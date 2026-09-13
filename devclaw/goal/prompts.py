"""The two prompts the host writes (spec 046, pillar 2 — the protocol): the
session brief and the done-gate review brief. Facts in, one prompt out."""

from __future__ import annotations

from .. import config as _config
from ..loom.untrusted import UNTRUSTED_NOTE, fence_untrusted
from ..prompts import load_prompt
from ..state_store import Goal, Task
from . import github as _gh
from .world import World

_BODY_CAP = 6000
_COMMENT_CAP = 1500
_MAX_COMMENTS = 12
_LOG_CAP = 4000


def _render_issues(issues: tuple[_gh.Issue, ...], goal: Goal) -> str:
    if not issues:
        return fence_untrusted("GOAL", goal.objective or "(no contract text)")
    parts = []
    for i in issues:
        body = i.body if len(i.body) <= _BODY_CAP else i.body[:_BODY_CAP] + "\n…(truncated)"
        parts.append(f"### Issue #{i.number} — {i.title} ({i.state})\n\n"
                     + fence_untrusted(f"ISSUE {i.number}", body))
    return "\n\n".join(parts)


def _render_comments(world: World) -> str:
    relevant = [c for c in world.comments if c.is_instruction or c.marker]
    relevant = relevant[-_MAX_COMMENTS:]
    if not relevant:
        return ""
    lines = ["", "## On the threads (newest last)", ""]
    for c in relevant:
        who = "devclaw record" if c.marker else f"owner instruction ({c.author})"
        body = _gh.MARKER_RE.sub("", c.body).strip()
        if len(body) > _COMMENT_CAP:
            body = body[:_COMMENT_CAP] + "…"
        lines.append(f"- **{who}**, {c.created_at[:10]} on #{c.number}:\n"
                     + fence_untrusted(f"COMMENT {c.id}", body))
    return "\n".join(lines) + "\n"


def _render_ci_logs(world: World) -> str:
    if not world.pr.failing_logs:
        return ""
    lines = ["", "## Failing CI job logs (tail)", ""]
    for name, tail in world.pr.failing_logs:
        lines.append(f"### {name}\n" + fence_untrusted(f"CI LOG {name}", tail[-_LOG_CAP:]))
    return "\n".join(lines) + "\n"


def session_brief(goal: Goal, world: World, last: "Task | None") -> str:
    pr = world.pr
    if pr.state == "none":
        pr_line = "none yet — the first delivery opens it"
    else:
        pr_line = f"{pr.url} ({pr.state}, head {pr.head_sha[:10]})"
    ci_line = f"{pr.ci}" + (f" — {pr.ci_detail}" if pr.ci_detail else "")
    if last is None:
        last_exit = "this is the first session"
    else:
        last_exit = f"{last.exit or last.status}" + (f": {last.exit_detail}" if last.exit_detail else "")
    return load_prompt(
        "session",
        branch=goal.branch,
        untrusted_note=UNTRUSTED_NOTE,
        issues=_render_issues(world.issues, goal),
        pr_line=pr_line,
        ci_line=ci_line,
        last_exit=last_exit,
        comments=_render_comments(world),
        ci_logs=_render_ci_logs(world),
        mention=_config.mention(),
    )


def contract_text(goal: Goal, issues: tuple[_gh.Issue, ...]) -> str:
    """The completion contract, read live: each issue's acceptance section,
    or its whole body when it has none."""
    if not issues:
        return goal.objective
    parts = []
    for i in issues:
        section = _gh.extract_acceptance(i.body) or i.body
        parts.append(f"### #{i.number} — {i.title}\n\n" + fence_untrusted(f"CONTRACT {i.number}", section[:_BODY_CAP]))
    return "\n\n".join(parts)


def review_brief(goal: Goal, world: World) -> str:
    return load_prompt("done-gate", untrusted_note=UNTRUSTED_NOTE,
                       contract=contract_text(goal, world.issues))
