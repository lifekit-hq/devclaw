#!/usr/bin/env python3
"""PreToolUse guard: a merge reads the verdict of record.

Spec 032 ruled that the project's own CI rollup is the verdict of record, and
wired it into the AUTONOMOUS path — tick_donegate refuses to merge-on-close on
a non-green head. The human path was never wired: `/ship` verifies a LOCAL
pytest run and `gh pr merge` then consults nothing at all.

That held only while local and CI agreed. On 2026-09-08 they stopped agreeing —
CI runs on the deploy host and the break (tinyspec `suite-owns-its-environment`)
was a lock race only a loaded box loses — so two PRs were squash-merged green-
locally / red-on-CI and `main` stayed red for five hours, holding every
devclaw-repo goal on `mechanical:ci`.

  BLOCKED  gh pr merge   when the PR's checks are failing or still pending
  ALLOWED  a green rollup; a repo with no checks; anything that is not a merge

Fails OPEN on every error, like the sibling guards: no `gh`, no network, an
unreadable payload. A hook that wedges the ritual is worse than the drift it
catches, and a human is present at this boundary.

Escape hatch: prefix with DEVCLAW_ALLOW_RED_MERGE=1 (mirrors
DEVCLAW_ALLOW_MAIN and DEVCLAW_ALLOW_NO_CASE). A deliberate red merge stays
possible, and stays a conscious act that names itself.
"""
import json
import re
import subprocess
import sys

#: Rollup states that are not a green verdict. PENDING is included on purpose:
#: "the checks had not finished" is exactly how a red merge gets rationalised.
BAD = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def _pr_ref(command: str) -> "str | None":
    """The PR the command merges: an explicit number/URL/branch, or '' for the
    current branch. None when this is not a merge."""
    if not re.search(r"\bgh\s+pr\s+merge\b", command):
        return None
    tail = command.split("pr merge", 1)[1]
    for tok in tail.split():
        if tok.startswith("-"):
            continue
        if re.fullmatch(r"\d+", tok) or tok.startswith("http"):
            return tok
        break
    return ""


def _checks(ref: str, cwd: str) -> "list[dict] | None":
    """The PR's checks, or None when they cannot be read (fail open)."""
    args = ["gh", "pr", "checks", "--json", "name,state,link"]
    if ref:
        args.insert(3, ref)
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=25, cwd=cwd or None)
    except Exception:
        return None
    out = (p.stdout or "").strip()
    if not out:
        # `gh pr checks` exits non-zero both for "no checks configured" and for
        # "some check failed"; only the former yields no JSON. No checks is not
        # a red verdict — nothing to disagree with.
        return []
    try:
        parsed = json.loads(out)
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


def blocks(command: str, cwd: str) -> "str | None":
    if "DEVCLAW_ALLOW_RED_MERGE=1" in command:
        return None
    ref = _pr_ref(command)
    if ref is None:
        return None
    checks = _checks(ref, cwd)
    if checks is None:  # unreadable — fail open
        return None
    bad = [c for c in checks if str(c.get("state", "")).upper() in BAD]
    pending = [c for c in checks if str(c.get("state", "")).upper() == "PENDING"]
    if not bad and not pending:
        return None
    lines = ["blocked: the PR's CI is not green, and CI is the verdict of record (spec 032)."]
    for c in bad:
        lines.append(f"  FAILING  {c.get('name')}  {c.get('link', '')}")
    for c in pending:
        lines.append(f"  PENDING  {c.get('name')}")
    lines.append(
        "A local pytest run is not the verdict — it runs on a different machine "
        "under different load, which is exactly how main went red on 2026-09-08."
    )
    lines.append(
        "Fix the failure, or wait for the run. Deliberate red merge on Denys's "
        "call: prefix with DEVCLAW_ALLOW_RED_MERGE=1 and say why in the PR."
    )
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return 0
        command = (payload.get("tool_input") or {}).get("command", "") or ""
        msg = blocks(command, payload.get("cwd") or "")
        if msg:
            print(msg, file=sys.stderr)
            return 2
        return 0
    except Exception:
        return 0  # fail-open


if __name__ == "__main__":
    sys.exit(main())
