#!/usr/bin/env python3
"""devclaw v2 — the stubborn loop.

The whole engine in one file, stdlib only: a mechanical shell around ONE
cognition boundary — a chain of claude sessions working toward a whole goal
in a git workspace. No decomposer, no evaluator, no review gates, no docker,
no MCP, no heartbeat. Those lived in v1's control plane and manufactured its
failures (see docs/proposals/v2-mvp.md).

The contract: a run always ends with either a delivered PR (exit 0) or a
readable .devclaw2/REPORT.md (exit 1). It never blocks waiting for a human.

Usage:
    python mvp/loop.py <workspace> "<goal text | @goal.md>" [options]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

# OAuth-only invariant: a stray key must never switch an autonomous run onto
# metered billing.
STRIPPED_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

DEFAULT_CLAUDE_CMD = "claude -p --dangerously-skip-permissions"
TRANSIENT_ATTEMPTS = 3
NO_PROGRESS_LIMIT = 2
VERIFY_TIMEOUT_S = 1800
TAIL_CHARS = 3000

DEVCLAW_DIR = ".devclaw2"
PLAN_FILE = f"{DEVCLAW_DIR}/PLAN.md"
DONE_FILE = f"{DEVCLAW_DIR}/DONE.md"
REPORT_FILE = f"{DEVCLAW_DIR}/REPORT.md"

# ---------------------------------------------------------------- prompts

PROMPT_HEADER = """\
You are an autonomous coding agent, session {session} of {max_sessions}, working
toward the goal below in the git repository at your current directory (branch
`{branch}`). Previous sessions may have left work here — the git log and the
working tree are your ground truth for what is already done.

GOAL:
{goal}
"""

STRATEGY_PLAN_FIRST = """\
If `.devclaw2/PLAN.md` does not exist, this is the PLANNING session: explore the
repository, break the goal into a markdown checklist of small verifiable steps,
write it to `.devclaw2/PLAN.md`, commit that file, and stop — do nothing else.

Otherwise: read `.devclaw2/PLAN.md`, pick the NEXT unchecked item, complete it,
mark it checked, and commit the result as one conventional commit. Leave any
notes the next session needs under a `## Session notes` heading at the bottom of
`.devclaw2/PLAN.md`.

When EVERY item is checked and the goal is fully complete, write
`.devclaw2/DONE.md` summarizing what was done.
"""

STRATEGY_REPLAN = """\
First revise `.devclaw2/PLAN.md` (create it if missing): reconcile the checklist
with the repository's actual state and what previous sessions learned — add,
reorder, or drop items as needed, keeping completed items checked. Commit the
plan change if you made one.

Then pick the NEXT unchecked item, complete it, mark it checked, and commit the
result as one conventional commit. Leave notes for the next session under a
`## Session notes` heading at the bottom of `.devclaw2/PLAN.md`.

When EVERY item is checked and the goal is fully complete, write
`.devclaw2/DONE.md` summarizing what was done.
"""

STRATEGY_DIRECT = """\
Work directly toward the goal. Commit completed work as logical conventional
commits as you go.

When the goal is fully complete, write `.devclaw2/DONE.md` summarizing what was
done.
"""

PROMPT_FOOTER = """\
Hard rules:
- Never weaken, skip, or delete tests to make something pass; fix root causes.
- Stay inside this repository; do not modify global config or credentials.
- If something is unresolvable, record it in a note file under `.devclaw2/` and
  make progress elsewhere — do not stall.
"""

VERIFY_SECTION = """\
The goal is only DONE when this command exits 0 (run from the repository root):
    {verify_cmd}
{failure_block}Run it yourself before declaring done. Do not change what the
command means or weaken what it checks.
"""

BUILTIN_STRATEGIES = {
    "plan-first": STRATEGY_PLAN_FIRST,
    "replan": STRATEGY_REPLAN,
    "direct": STRATEGY_DIRECT,
}


def build_prompt(
    goal: str,
    strategy: str,
    session: int,
    max_sessions: int,
    branch: str,
    verify_cmd: str | None = None,
    verify_tail: str = "",
    custom_strategy_text: str | None = None,
) -> str:
    """A strategy is a prompt variant ONLY — the shell is identical for all."""
    if custom_strategy_text is not None:
        body = custom_strategy_text
    else:
        body = BUILTIN_STRATEGIES[strategy]
    parts = [
        PROMPT_HEADER.format(
            session=session, max_sessions=max_sessions, branch=branch, goal=goal
        ),
        body,
    ]
    if verify_cmd:
        failure_block = ""
        if verify_tail:
            failure_block = (
                f"Its last run FAILED with this output tail:\n{verify_tail}\n"
            )
        parts.append(
            VERIFY_SECTION.format(verify_cmd=verify_cmd, failure_block=failure_block)
        )
    parts.append(PROMPT_FOOTER)
    return "\n".join(parts)


# ---------------------------------------------------------------- plumbing


def log(msg: str) -> None:
    print(f"[devclaw2] {msg}", flush=True)


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in STRIPPED_ENV_KEYS:
        env.pop(key, None)
    return env


def sh(
    cmd: list[str] | str,
    cwd: Path,
    timeout: float | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        shell=isinstance(cmd, str),
        env=clean_env(),
        text=True,
        capture_output=True,
        timeout=timeout,
        input=input_text,
        stdin=subprocess.DEVNULL if input_text is None else None,
    )


def git(ws: Path, *args: str) -> subprocess.CompletedProcess:
    return sh(["git", *args], cwd=ws)


def tail(text: str, n: int = TAIL_CHARS) -> str:
    text = text or ""
    return text[-n:]


def tree_fingerprint(ws: Path) -> str:
    """Progress = HEAD moved or the working tree changed. (Content edits inside
    already-untracked files are not distinguished — acceptable for the brake.)"""
    head = git(ws, "rev-parse", "HEAD").stdout.strip()
    status = git(ws, "status", "--porcelain").stdout
    diff = git(ws, "diff", "HEAD").stdout
    return hashlib.sha256((head + "\0" + status + "\0" + diff).encode()).hexdigest()


def run_claude(prompt: str, ws: Path, timeout: float) -> tuple[bool, str]:
    """One session. Signal-death (-9 OOM class) and timeouts are TRANSIENT:
    bounded retry with backoff (v1 lesson #448/#449). Any other failure is
    reported as a failed session — the loop's no-progress brake owns the stop
    decision; run_claude never wedges the run."""
    cmd = shlex.split(os.environ.get("DEVCLAW2_CLAUDE_CMD", DEFAULT_CLAUDE_CMD))
    backoff = float(os.environ.get("DEVCLAW2_BACKOFF_S", "20"))
    last = ""
    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        try:
            proc = sh(cmd, cwd=ws, timeout=timeout, input_text=prompt)
        except subprocess.TimeoutExpired:
            last = f"session timed out after {timeout:.0f}s"
        else:
            if proc.returncode == 0:
                return True, tail(proc.stdout)
            if proc.returncode < 0:
                last = f"claude died with signal {-proc.returncode}"
            else:
                return False, tail(
                    f"claude exited {proc.returncode}\n{proc.stderr}\n{proc.stdout}"
                )
        log(f"transient: {last} (attempt {attempt}/{TRANSIENT_ATTEMPTS})")
        if attempt < TRANSIENT_ATTEMPTS:
            time.sleep(backoff)
    return False, last


def check_done(ws: Path, verify_cmd: str | None) -> tuple[bool, str]:
    """Done = the agent self-reported (DONE.md). With --verify, additionally the
    verify command must exit 0; a verify crash counts as NOT done (fail closed)."""
    if not (ws / DONE_FILE).exists():
        return False, ""
    if not verify_cmd:
        return True, ""
    try:
        proc = sh(verify_cmd, cwd=ws, timeout=VERIFY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"verify timed out after {VERIFY_TIMEOUT_S}s"
    if proc.returncode == 0:
        return True, ""
    return False, tail(f"verify exited {proc.returncode}\n{proc.stderr}\n{proc.stdout}")


def commit_all(ws: Path, message: str) -> None:
    git(ws, "add", "-A")
    if git(ws, "diff", "--cached", "--quiet").returncode != 0:
        git(ws, "commit", "-m", message)


def slugify(goal: str) -> str:
    first = goal.strip().splitlines()[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", first).strip("-")[:40].strip("-")
    return slug or "goal"


# ---------------------------------------------------------------- endings


def deliver(ws: Path, branch: str, goal: str, mode: str) -> int:
    """Finished goal → ship it. A delivery failure is a loud exit 1, never a
    silent 'done without a PR' (v1 lesson #183)."""
    log("goal self-reported done — delivering")
    if mode == "none":
        return 0
    commit_all(ws, f"chore(devclaw2): finalize {slugify(goal)}")
    if mode == "commit":
        log("delivered: committed locally")
        return 0
    push = git(ws, "push", "-u", "origin", branch)
    if push.returncode != 0:
        log(f"DELIVERY FAILED: push failed:\n{tail(push.stderr, 800)}")
        return 1
    if mode == "push":
        log(f"delivered: pushed {branch}")
        return 0
    done_summary = ""
    done_path = ws / DONE_FILE
    if done_path.exists():
        done_summary = done_path.read_text()
    body = (
        f"## Goal\n\n{goal}\n\n## Agent summary\n\n{done_summary}\n\n"
        "---\n🤖 Shipped by the devclaw v2 stubborn loop"
    )
    try:
        pr = sh(
            [
                "gh", "pr", "create",
                "--head", branch,
                "--title", f"devclaw2: {slugify(goal)}",
                "--body", body,
            ],
            cwd=ws,
        )
    except FileNotFoundError:
        log("DELIVERY FAILED: gh CLI not found")
        return 1
    if pr.returncode != 0:
        log(f"DELIVERY FAILED: gh pr create failed:\n{tail(pr.stderr, 800)}")
        return 1
    log(f"delivered: {pr.stdout.strip()}")
    return 0


def abandon(
    ws: Path,
    branch: str,
    goal: str,
    mode: str,
    reason: str,
    sessions_run: int,
    last_detail: str,
    verify_tail_text: str,
) -> int:
    """Never-block contract: no 'ask the human and wait' state exists. The run
    ends loudly with a readable morning report (and a WIP draft PR when the
    deliver mode includes pushing)."""
    log(f"ABANDONING: {reason}")
    report = (
        f"# devclaw2 run report — ABANDONED\n\n"
        f"**Reason:** {reason}\n\n"
        f"**Sessions run:** {sessions_run}\n\n"
        f"## Goal\n\n{goal}\n\n"
        f"## Last session output tail\n\n```\n{last_detail}\n```\n"
    )
    if verify_tail_text:
        report += f"\n## Last verify failure tail\n\n```\n{verify_tail_text}\n```\n"
    (ws / DEVCLAW_DIR).mkdir(exist_ok=True)
    (ws / REPORT_FILE).write_text(report)
    commit_all(ws, f"chore(devclaw2): abandon report for {slugify(goal)}")
    if mode in ("push", "pr"):
        push = git(ws, "push", "-u", "origin", branch)
        if push.returncode != 0:
            log(f"report push failed (best-effort): {tail(push.stderr, 400)}")
        elif mode == "pr":
            try:
                pr = sh(
                    [
                        "gh", "pr", "create", "--draft",
                        "--head", branch,
                        "--title", f"devclaw2 WIP (abandoned): {slugify(goal)}",
                        "--body", report,
                    ],
                    cwd=ws,
                )
                if pr.returncode == 0:
                    log(f"WIP draft PR: {pr.stdout.strip()}")
            except FileNotFoundError:
                pass
    log(f"report written: {REPORT_FILE}")
    return 1


# ---------------------------------------------------------------- main loop


def read_at_arg(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text()
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("workspace", help="path to a git repository to work in")
    p.add_argument("goal", help="goal text, or @file to read it from a file")
    p.add_argument(
        "--strategy",
        default="plan-first",
        help="plan-first | replan | direct | @file.md (custom prompt template)",
    )
    p.add_argument("--verify", default=None, help="opt-in gate: done only when this shell command exits 0")
    p.add_argument("--max-iters", type=int, default=10, help="session cap (default 10)")
    p.add_argument("--branch", default=None, help="work branch (default v2/<goal-slug>)")
    p.add_argument(
        "--deliver",
        choices=["pr", "push", "commit", "none"],
        default="pr",
        help="what happens on done (default pr)",
    )
    p.add_argument(
        "--session-timeout", type=float, default=3600, help="per-session wall clock seconds"
    )
    args = p.parse_args(argv)
    if args.strategy not in BUILTIN_STRATEGIES and not args.strategy.startswith("@"):
        p.error(f"--strategy must be one of {sorted(BUILTIN_STRATEGIES)} or @file.md")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ws = Path(args.workspace).resolve()
    if not (ws / ".git").exists():
        log(f"FATAL: {ws} is not a git repository")
        return 2
    if git(ws, "status", "--porcelain").stdout.strip():
        log(f"FATAL: {ws} has uncommitted changes — commit or stash them first")
        return 2

    goal = read_at_arg(args.goal).strip()
    custom_strategy = (
        read_at_arg(args.strategy) if args.strategy.startswith("@") else None
    )
    branch = args.branch or f"v2/{slugify(goal)}"
    if git(ws, "rev-parse", "--verify", "--quiet", branch).returncode == 0:
        checkout = git(ws, "checkout", branch)
    else:
        checkout = git(ws, "checkout", "-b", branch)
    if checkout.returncode != 0:
        log(f"FATAL: cannot checkout {branch}:\n{tail(checkout.stderr, 400)}")
        return 2
    (ws / DEVCLAW_DIR).mkdir(exist_ok=True)
    log(f"goal: {slugify(goal)} | branch: {branch} | strategy: {args.strategy}")

    no_progress = 0
    verify_tail_text = ""
    last_detail = ""
    session = 0
    for session in range(1, args.max_iters + 1):
        prompt = build_prompt(
            goal,
            args.strategy if custom_strategy is None else "plan-first",
            session,
            args.max_iters,
            branch,
            verify_cmd=args.verify,
            verify_tail=verify_tail_text,
            custom_strategy_text=custom_strategy,
        )
        before = tree_fingerprint(ws)
        log(f"session {session}/{args.max_iters}: starting")
        try:
            ok, last_detail = run_claude(prompt, ws, args.session_timeout)
        except FileNotFoundError as exc:
            log(f"FATAL: claude command not found ({exc})")
            return 2
        log(f"session {session}/{args.max_iters}: {'ok' if ok else 'failed'}")
        done, verify_tail_text = check_done(ws, args.verify)
        if done:
            return deliver(ws, branch, goal, args.deliver)
        if tree_fingerprint(ws) == before:
            no_progress += 1
            if no_progress >= NO_PROGRESS_LIMIT:
                return abandon(
                    ws, branch, goal, args.deliver,
                    f"no progress in {NO_PROGRESS_LIMIT} consecutive sessions",
                    session, last_detail, verify_tail_text,
                )
        else:
            no_progress = 0
    return abandon(
        ws, branch, goal, args.deliver,
        f"session cap ({args.max_iters}) exhausted",
        session, last_detail, verify_tail_text,
    )


if __name__ == "__main__":
    sys.exit(main())
