#!/usr/bin/env python3
"""PreToolUse guard: a behavior-changing PR does not open without a filled
North-star case.

Constitution 2.9.0 (ruled 2026-09-07): every spec and tinyspec names the
north-star failure it moves, the number that shows it, and its cut
condition. The spec template and the tinyspec skeleton carry the section;
nothing checked that it was filled before the PR opened. This hook is that
check, at the one boundary that matters (`gh pr create`), and it is
mechanical on purpose: it verifies the judgment was RECORDED, it does not
make the judgment (that is the `north-star` skill).

  BLOCKED  gh pr create   when the branch changes behavior paths (devclaw/,
                          runner/, .sandcastle/, deploy/) and no spec or
                          tinyspec on the branch carries a North-star case,
                          or the case still holds the template placeholders
  ALLOWED  everything else - docs-only, tests-only, harness-only (.claude/,
           .specify/) diffs; any diff whose artifact has a filled case

Where the artifact is looked for: (1) a `specs/*/spec.md` or
`specs/tiny/*.md` changed on the branch; (2) failing that, a `specs/...md`
path or a `spec NNN` mention in the branch's commit messages (the spec may
have merged in an earlier PR). Not found -> block. Found -> the section
must exist and hold no placeholder.

Escape hatch: prefix the command with `DEVCLAW_ALLOW_NO_CASE=1 ` (a hotfix
on Denys's call; say why in the PR body). Exit codes: 0 allow, 2 block.
Fail-OPEN on our own errors: a guard bug must never wedge a PR.
"""

import json
import os
import re
import subprocess
import sys

BEHAVIOR_PREFIXES = ("devclaw/", "runner/", ".sandcastle/", "deploy/")
PLACEHOLDERS = (
    "stopped when it shouldn't |",          # the axis picker left unpicked
    "the loop-health / scorecard read",     # the number left as template
    "the observation under which",          # the cut condition left as template
)


def git(cwd: str, *args: str) -> str:
    out = subprocess.run(["git", "-C", cwd or ".", *args],
                         capture_output=True, text=True, timeout=8)
    return out.stdout if out.returncode == 0 else ""


def effective_cwd(command: str, payload_cwd: str) -> "str | None":
    for pat in (r"(?:^|&&|\|\||;)\s*cd\s+(\S+)",):
        m = re.search(pat, command)
        if m:
            raw = m.group(1)
            if "$" in raw or "`" in raw:
                return None
            return os.path.expanduser(raw.strip("'\""))
    return payload_cwd


def base_ref(cwd: str) -> str:
    for ref in ("origin/main", "main"):
        if git(cwd, "rev-parse", "--verify", "-q", ref):
            return ref
    return ""


def changed_paths(cwd: str, base: str) -> "list[str]":
    return [p for p in git(cwd, "diff", "--name-only", f"{base}...HEAD").splitlines() if p]


def artifacts_on_branch(cwd: str, base: str, changed: "list[str]") -> "list[str]":
    found = [p for p in changed
             if re.fullmatch(r"specs/[^/]+/spec\.md", p) or re.fullmatch(r"specs/tiny/[^/]+\.md", p)]
    if found:
        return found
    body = git(cwd, "log", "--format=%B", f"{base}..HEAD")
    for m in re.finditer(r"specs/[\w./-]+\.md", body):
        if os.path.exists(os.path.join(cwd, m.group(0))):
            found.append(m.group(0))
    for m in re.finditer(r"\bspec\s+0*(\d{3})\b", body, re.IGNORECASE):
        num = m.group(1)
        specs_dir = os.path.join(cwd, "specs")
        if os.path.isdir(specs_dir):
            for d in os.listdir(specs_dir):
                if d.startswith(num + "-") and os.path.exists(os.path.join(specs_dir, d, "spec.md")):
                    found.append(f"specs/{d}/spec.md")
    return sorted(set(found))


def case_problem(cwd: str, path: str) -> "str | None":
    try:
        with open(os.path.join(cwd, path), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return f"{path}: unreadable"
    m = re.search(r"^##\s+North-star case.*?$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return f"{path}: no `## North-star case` section"
    section = m.group(1)
    for ph in PLACEHOLDERS:
        if ph in section:
            return f"{path}: North-star case still holds the template placeholder ({ph.strip()!r})"
    if not re.search(r"\*\*Failure moved\*\*\s*:\s*\S", section):
        return f"{path}: North-star case has no `Failure moved` line"
    return None


def blocks(command: str, payload_cwd: str) -> "str | None":
    if "DEVCLAW_ALLOW_NO_CASE=1" in command:
        return None
    if not re.search(r"\bgh\s+pr\s+create\b", command):
        return None
    cwd = effective_cwd(command, payload_cwd)
    if not cwd:
        return None
    base = base_ref(cwd)
    if not base:
        return None
    changed = changed_paths(cwd, base)
    if not any(p.startswith(BEHAVIOR_PREFIXES) for p in changed):
        return None  # docs / tests / harness only
    artifacts = artifacts_on_branch(cwd, base, changed)
    if not artifacts:
        return (
            "blocked: this branch changes behavior (devclaw/ runner/ …) and no "
            "spec or tinyspec on it carries a North-star case (constitution 2.9.0). "
            "Run /north-star on the change, write the case into specs/NNN-*/spec.md "
            "or specs/tiny/<name>.md, and name that file in a commit message. "
            "Hotfix on Denys's call: prefix with DEVCLAW_ALLOW_NO_CASE=1 and say why in the PR."
        )
    problems = [p for p in (case_problem(cwd, a) for a in artifacts) if p]
    if problems and len(problems) == len(artifacts):
        return "blocked: " + "; ".join(problems) + ". Fill it (the /north-star skill is the judge)."
    return None


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
