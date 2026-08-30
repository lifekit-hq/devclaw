"""
DevClaw — worker runner (runs inside the per-task sandbox container).

Spawned by the host sandcastle runner via ``docker run``. Reads a single JSON
request from argv[1] and streams progress to stdout, one prefixed line at a
time:

    event: {"id":"...","type":"ActionEvent","source":"agent","payload":{...},"ts":...}
    event: {"id":"...","type":"ObservationEvent",...}
    ...
    result: {"status":"ok","workspace_dir":"...","message":"..."}

The TS caller splits on newlines and routes `event:` lines to the events
table while waiting for the single terminating `result:` line. On failure
the `result:` line carries status='error' instead.

Authentication: Claude Code OAuth session via CLAUDE_CODE_EXECUTABLE +
CLAUDE_CONFIG_DIR env vars. No ANTHROPIC_API_KEY required or accepted.
"""

import atexit
import glob as _glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import TextIO
import tempfile
import time
import traceback

# Wall-clock cap for the verify gate subprocess so a hung test suite can't hang
# the task forever (the agent's own wall-clock guard is separate — improvement #3).
_VERIFY_TIMEOUT_S = int(os.environ.get("DEVCLAW_VERIFY_TIMEOUT_S", "900"))

#: Shell the verify gate runs under. bash with `pipefail` so a piped gate
#: command can't mask a failing stage's exit code (see _run_verify). Resolved
#: once, with a POSIX-sh fallback for a box without bash — there the masking
#: risk returns, but a missing shell must not fail every gate.
_BASH = shutil.which("bash")
_VERIFY_SHELL = [_BASH, "-o", "pipefail", "-c"] if _BASH else ["/bin/sh", "-c"]

# Skill bundle baked into the sandbox image at /opt/devclaw/skills/. Layout:
#   _common.md          → always prepended
#   _writes-code/*.md   → for kinds that write code (implement_feature, fix_bug)
#   <kind>/*.md         → kind-specific (review_repository, onboard, …)
#   craft/*.md          → self-selected reference (NOT concatenated) — how-to
#                         guides the agent discovers by `ls`/`cat` when a task
#                         calls for them (frontend-design, playwright). Kept out
#                         of the always-on brief on purpose: the globs below only
#                         reach _common/_writes-code/<kind>/root-level *.md, so a
#                         sibling subdir like craft/ is never pulled in.
# The always-on tiers are DOCTRINE (follow every task); craft/ is CRAFT (read
# when relevant). Files inside a tier are sorted lexicographically so a leading
# number controls order. Repo-specific guidance still lives in the target repo's
# AGENTS.md — the skills carry devclaw's cross-repo doctrine; AGENTS.md carries
# this repo's facts.
_SKILLS_DIR = os.environ.get("DEVCLAW_SKILLS_DIR", "/opt/devclaw/skills")
_HOOKS_DIR = os.environ.get("DEVCLAW_HOOKS_DIR", "/opt/devclaw/hooks")
_WRITES_CODE_KINDS = {"implement_feature", "fix_bug"}
#: Kinds with a skill bundle under ``runner/skills/``. Anything else is
#: briefed as implement_feature. Kept as an explicit set rather than
#: inferred from the directory listing so an image that bakes a partial
#: bundle raises in _wrap_goal instead of silently downgrading the kind.
_KNOWN_KINDS = {
    "implement_feature", "fix_bug", "review_repository", "onboard",
    # validate_product (spec 015) is agent-less: no skill bundle, no ACP
    # session — main() branches into _run_validate_product before _wrap_goal.
    "validate_product",
}
_HOOK_TIMEOUT_S = 30


def _read_skill(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _skill_paths_for_root(root: str, kind: str) -> list[str]:
    """Return the ordered list of skill files under a given root, for a kind.

    Order: ``_common.md`` → ``_writes-code/*.md`` (only for kinds that write
    code) → ``<kind>/*.md`` → any other ``*.md`` at the root (catch-all for
    per-repo observation files that don't fit a tier, e.g. closeloop's
    ``frontend-structure.md``). Files within a tier are sorted
    lexicographically so a leading number controls order. Missing tiers are
    silently skipped — a partial layout can't crash the runner.
    """
    paths: list[str] = []
    common = os.path.join(root, "_common.md")
    if os.path.exists(common):
        paths.append(common)
    if kind in _WRITES_CODE_KINDS:
        paths.extend(sorted(_glob.glob(os.path.join(root, "_writes-code", "*.md"))))
    paths.extend(sorted(_glob.glob(os.path.join(root, kind, "*.md"))))
    # Catch-all: any *.md at the skill root not already picked up. Useful for
    # per-repo observation files that aren't per-kind (the project's overall
    # state, e.g. "App.tsx is a known monolith"). _common.md is already
    # included above; skip it here to avoid double-loading.
    already = set(paths)
    for path in sorted(_glob.glob(os.path.join(root, "*.md"))):
        if path not in already and os.path.basename(path) != "_common.md":
            paths.append(path)
    return paths


def _load_skills(kind: str, workspace_dir: str | None = None) -> str:
    """Concatenate the skill bundle for a given task kind.

    Loads universal devclaw skills from ``/opt/devclaw/skills/`` (baked into
    the image), then appends per-repo skills from ``<workspace>/.agent/skills/``
    if a workspace is provided. The per-repo layer carries project-specific
    observations the universal skills can't (e.g. "App.tsx is a 1827-line
    monolith") and evolves at the repo's pace — symmetric with the per-repo
    hook discovery in :func:`_run_hook`. Universal skills come FIRST so the
    repo can lean on doctrine the agent already has.

    Empty paths and missing files are tolerated; at worst the agent just
    gets less briefing.
    """
    paths: list[str] = _skill_paths_for_root(_SKILLS_DIR, kind)
    if workspace_dir:
        paths.extend(_skill_paths_for_root(
            os.path.join(workspace_dir, ".agent", "skills"), kind,
        ))
    blocks = [b for b in (_read_skill(p) for p in paths) if b]
    return "\n\n---\n\n".join(blocks)


def _run_one_hook(path: str, args: tuple[str, ...]) -> tuple[bool, str]:
    """Run a single hook script (best-effort). Returns (ran, captured_output)."""
    if not os.path.exists(path):
        return False, ""
    try:
        proc = subprocess.run(
            ["bash", path, *args],
            capture_output=True,
            text=True,
            timeout=_HOOK_TIMEOUT_S,
            preexec_fn=_raise_own_oom_score,  # workload, not supervisor (spec 020)
        )
        return True, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return True, f"hook timed out after {_HOOK_TIMEOUT_S}s"
    except OSError as exc:
        return True, f"hook failed to start: {exc}"


def _run_hook(name: str, *args: str) -> list[str]:
    """Run the universal devclaw hook then the per-repo hook (if either exists).

    Universal hooks live in /opt/devclaw/hooks/ (baked into the sandbox image,
    devclaw-owned). Per-repo hooks live in <workspace>/.agent/hooks/ (project-
    owned, evolves at the repo's pace). Both contribute to the warnings list
    with a tagged prefix so the goal layer can tell them apart.

    Returns a list of warning lines (possibly empty). Hook failures are NOT
    fatal — they're advisory; the verify gate is the source of truth.
    """
    warnings: list[str] = []
    # Universal: devclaw-owned doctrine baked into the image.
    universal_path = os.path.join(_HOOKS_DIR, f"{name}.sh")
    ran, out = _run_one_hook(universal_path, args)
    if ran and out:
        warnings.append(f"[{name}] {out}")
    # Per-repo: project-owned, lives in the workspace alongside AGENTS.md.
    # args[0] is workspace_dir by convention; if no args we can't locate it.
    if args:
        repo_path = os.path.join(args[0], ".agent", "hooks", f"{name}.sh")
        ran, out = _run_one_hook(repo_path, args)
        if ran and out:
            warnings.append(f"[{name}:repo] {out}")
    return warnings


# final thing the engineer reads before finishing. It reports the OUTCOME of the
# work and never prescribes HOW to do it, so it does not fight _QUALITY_BAR's
# "form your own opinion as a senior engineer." Vendor-neutral plain markdown —
# the model-agnostic worker layer carries no vendor tool-wiring.
_RETURN_CONTRACT = (
    "## When you finish — hand back a structured summary\n\n"
    "End your final message with a hand-back in exactly this shape — one line "
    "per field, plain text, no code fence — so devclaw can read your result "
    "without guessing:\n\n"
    "STATUS: DONE  — or  BLOCKED: <one-line reason>  if you could not finish.\n"
    "CHANGED: the files/areas you changed, one clause each, and what each change does.\n"
    "VERIFIED: the checks you ACTUALLY ran and their result — tests, lint, "
    "type-check, build (name the commands).\n"
    "ACCEPTANCE: for each acceptance criterion stated in the Goal, whether it is "
    "met and the evidence; write 'none stated' if the Goal listed none.\n"
    "FOLLOW-UPS: anything you had to work around, left unfinished, or that needs "
    "a human — or 'none'.\n"
    "REPO NOTES: durable repo-level facts a future engineer on a DIFFERENT task "
    "in this repository would need — build/test quirks, non-obvious commands, "
    "environment gotchas — as short semicolon-separated clauses, or 'none'. "
    "Never task-specific detail (that belongs in CHANGED), never speculation.\n\n"
    "Report only checks you truly ran, not ones you intended to. If you write "
    "BLOCKED, still fill CHANGED / VERIFIED / ACCEPTANCE with how far you got."
)


def _wrap_goal(kind: str, goal: str, workspace_dir: str | None = None) -> str:
    """Skills prepended, then the goal under a clear marker.

    ``runner/skills/`` is the ONE home for worker-kind instructions — baked to
    ``/opt/devclaw/skills/`` in the sandbox image, pointed at in-repo by the
    host engine. Per-repo skills from ``<workspace>/.agent/skills/`` are
    appended when a workspace is provided.

    Raises when no skill file resolves. There used to be a second copy of every
    instruction embedded here as a fallback, and it was not harmless: a prompt
    edit could land in the copy production never reads while the canonical skill
    said something else, with nothing to tell the two apart (#610). Substituting
    different text for a worker that then runs unattended is the silent
    degradation the hardening philosophy forbids — a missing bundle is a broken
    image or a mis-wired engine, and it fails LOUD.
    """
    # Unknown kinds are briefed as implement_feature — the widest contract, and
    # the historical equivalence.
    effective_kind = kind if kind in _KNOWN_KINDS else "implement_feature"
    skills = _load_skills(effective_kind, workspace_dir=workspace_dir)
    if not skills:
        raise RuntimeError(
            f"no skill files found for kind {effective_kind!r} under "
            f"{_SKILLS_DIR!r} (DEVCLAW_SKILLS_DIR). The worker will not run on "
            f"an unbriefed prompt. In the sandbox this means the image did not "
            f"bake runner/skills/; on the host it means DEVCLAW_SKILLS_DIR is "
            f"unset or wrong."
        )
    brief = f"{skills}\n\n---\n\n## Goal\n\n{goal}"
    # Structured return contract — code-writing kinds only. review_repository and
    # onboard already carry their own report contract (a written review / doc
    # set), so appending the code hand-back would fight it. Rendered LAST so the
    # engineer reads it right before finishing.
    if effective_kind in _WRITES_CODE_KINDS:
        brief = f"{brief}\n\n---\n\n{_RETURN_CONTRACT}"
    return brief


def _run_verify(cmd: str, workspace_dir: str, timeout: int = _VERIFY_TIMEOUT_S) -> dict:
    """Run the verify gate in the workspace AFTER the agent finishes and return a
    verdict. The agent saying "done" isn't trusted — the project's own
    test/build command exiting 0 is what "done" means. Run via the shell so a
    full command line works ("npm run build && npm run test:ci"); combined
    stdout+stderr, tail-truncated. Never raises — a crash/timeout is a failed
    gate, not a runner crash.

    Run under ``bash -o pipefail``, not the default ``sh -c``: without it a
    piped gate reports the LAST stage's status, so the extremely natural
    ``pytest … | tail -20`` exits 0 on a red suite and the gate passes a failing
    build. That is a fail-OPEN verification gate — the one thing the hardening
    philosophy forbids — and it shipped live (a devclaw self-fix settled `done`
    with a failing test in the gate's own captured output, 2026-08-20)."""
    try:
        proc = subprocess.run(
            _VERIFY_SHELL + [cmd],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_raise_own_oom_score,  # workload, not supervisor (spec 020)
        )
    except subprocess.TimeoutExpired as exc:
        out_part = exc.output if isinstance(exc.output, str) else ""
        err_part = exc.stderr if isinstance(exc.stderr, str) else ""
        partial = out_part + err_part
        return {
            "ran": True, "cmd": cmd, "passed": False, "exit_code": None,
            "timed_out": True, "output": partial[-4000:],
        }
    except OSError as exc:
        return {
            "ran": True, "cmd": cmd, "passed": False, "exit_code": None,
            "timed_out": False, "output": f"failed to run verify command: {exc}",
        }
    combined = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ran": True, "cmd": cmd, "passed": proc.returncode == 0,
        "exit_code": proc.returncode, "timed_out": False, "output": combined[-4000:],
    }


#: Where the browser-E2E gate's proof-of-execution lands. The runner points
#: Playwright's JSON reporter here (via PLAYWRIGHT_JSON_OUTPUT_NAME) so a browser
#: run's real pass/fail counts survive to the host — the 4 KB-truncated verify
#: `output` can't carry them. A devclaw-owned path (not the project's default)
#: so it reflects THIS run, never a stale artifact from a prior attempt.
_BROWSER_REPORT_REL = os.path.join(".devclaw", "playwright-report.json")


def _read_browser_report(workspace_dir: str) -> "dict | None":
    """Parse the Playwright JSON reporter artifact the verify gate produced (if
    any) into the compact {expected, unexpected, flaky, skipped} summary the
    host browser-gate keys off. Best-effort: a missing/garbled artifact returns
    None, which the host reads as "no browser run" — fail-closed for a frontend
    change, never a false pass."""
    path = os.path.join(workspace_dir, _BROWSER_REPORT_REL)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    stats = data.get("stats") if isinstance(data, dict) else None
    if not isinstance(stats, dict):
        return None
    if not any(k in stats for k in ("expected", "unexpected", "flaky", "skipped")):
        return None
    return {
        "expected": int(stats.get("expected", 0) or 0),
        "unexpected": int(stats.get("unexpected", 0) or 0),
        "flaky": int(stats.get("flaky", 0) or 0),
        "skipped": int(stats.get("skipped", 0) or 0),
    }


# --- validate_product (spec 015) ---------------------------------------------
# The agent-less branch: boot the repo-declared contract, run the accumulated
# acceptance suites, report — no ACP session, no skills, zero LLM (FR-005).

#: Per-step wall clock for the boot and suites commands. Both must fit inside
#: the host's overall TASK_TIMEOUT_S; a cut suite is reported as explicit
#: partial coverage, never a silent truncation.
_VALIDATION_STEP_TIMEOUT_S = int(
    os.environ.get("DEVCLAW_VALIDATION_STEP_TIMEOUT_S", "900")
)


def _extract_failing_tests(workspace_dir: str) -> list[str]:
    """Failing test titles from the Playwright JSON report's suites tree —
    the per-scenario identity the host turns into finding fingerprints.
    Best-effort: no/garbled report ⇒ [] (the host degrades to one run-level
    finding on a red exit code)."""
    path = os.path.join(workspace_dir, _BROWSER_REPORT_REL)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    titles: list[str] = []

    def _walk(node, crumbs):
        if not isinstance(node, dict):
            return
        title = str(node.get("title") or "").strip()
        here = crumbs + [title] if title else crumbs
        for spec in node.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            spec_title = str(spec.get("title") or "").strip()
            tests = spec.get("tests") or []
            failed = any(
                isinstance(t, dict) and t.get("status") == "unexpected"
                for t in tests
            )
            if failed:
                titles.append(" > ".join([*here, spec_title]).strip(" >"))
        for child in node.get("suites") or []:
            _walk(child, here)

    for suite in (data.get("suites") or []) if isinstance(data, dict) else []:
        _walk(suite, [])
    return titles


def _step_summary(step: dict) -> dict:
    return {
        "passed": bool(step.get("passed")),
        "exit_code": step.get("exit_code"),
        "timed_out": bool(step.get("timed_out")),
        "output_tail": (step.get("output") or "")[-2000:],
    }


def _run_validate_product(req: dict, workspace_dir: str) -> None:
    """Run one validation: boot → suites → report. Emits the terminal result
    itself (status ok with a ``validation_report``; the HOST decides the task
    verdict and files findings — the sandbox holds no GitHub credential)."""
    contract = req.get("validation") or {}
    boot_cmd = (contract.get("boot") or "").strip()
    suites_cmd = (contract.get("suites") or "").strip()
    vr: dict = {
        "contract_ran": False, "boot": None, "suites": None,
        "browser_report": None, "failing_tests": [], "partial": False, "note": "",
    }

    def _finish(message: str) -> None:
        _emit_result({
            "status": "ok", "workspaceDir": workspace_dir,
            "message": message, "validation_report": vr,
        })

    if not boot_cmd or not suites_cmd:
        vr["note"] = "missing contract: payload carried no validation.boot/suites"
        _finish("validation: missing contract")
        return

    try:
        _provision_toolchain(workspace_dir)
    except Exception as exc:  # noqa: BLE001 — loud in the report, not a crash
        vr["note"] = f"toolchain_provision_failed: {exc}"
        _finish("validation: toolchain provisioning failed")
        return

    # Per-run report hygiene: point Playwright at the devclaw-owned path and
    # clear any stale artifact so titles reflect THIS run only.
    report_path = os.path.join(workspace_dir, _BROWSER_REPORT_REL)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    os.environ["PLAYWRIGHT_JSON_OUTPUT_NAME"] = report_path
    try:
        os.remove(report_path)
    except OSError:
        pass
    _resync_mise_env(workspace_dir)

    boot = _run_verify(boot_cmd, workspace_dir, timeout=_VALIDATION_STEP_TIMEOUT_S)
    vr["boot"] = _step_summary(boot)
    vr["contract_ran"] = True
    if not boot.get("passed"):
        vr["note"] = (
            "boot timed out" if boot.get("timed_out")
            else "boot failed — the product did not come up"
        )
        _finish("validation: boot failed")
        return

    suites = _run_verify(suites_cmd, workspace_dir, timeout=_VALIDATION_STEP_TIMEOUT_S)
    vr["suites"] = _step_summary(suites)
    vr["browser_report"] = _read_browser_report(workspace_dir)
    vr["failing_tests"] = _extract_failing_tests(workspace_dir)
    if suites.get("timed_out"):
        vr["partial"] = True
        vr["note"] = (
            f"partial coverage: suites cut by the {_VALIDATION_STEP_TIMEOUT_S}s "
            "step timeout — results reflect only what ran"
        )
    elif suites.get("passed"):
        report = vr["browser_report"]
        executed = (
            report["expected"] + report["unexpected"] + report["flaky"]
            if report else None
        )
        if executed == 0:
            vr["note"] = "green-by-vacuity: no acceptance tests accumulated yet"
        elif report is None:
            vr["note"] = "suites exit 0; no per-scenario report found"
    _finish("validation: suites ran")


# --- usage-limit detection ---------------------------------------------------
# Vendored subset — keep in sync with devclaw/loom/limits.py. The runner runs
# inside the sandbox WITHOUT the devclaw package installed, so it cannot import
# the canonical classifier; these are verbatim copies of its AUTH/QUOTA/RATE
# patterns and the relative retry-after parser. Purpose: when the agent loop
# dies on a clear model usage/rate limit, surface it STRUCTURALLY as
# status="rate_limited" (+ retry_after seconds when the text states one) so the
# host pauses instead of retry-burning quota. This is belt on top of suspenders:
# anything ambiguous stays status="error" and the host-side regex fallback
# (loom/limits.classify_failure) still sees the original text — false negatives
# are fine, false positives are not.

# AUTH: never tagged rate_limited HERE — auth text flows through as a plain
# error so the HOST classifier (loom/limits.py) sees the original wording and
# routes it onto the AUTH pause path (fixed re-probe + actionable owner ping,
# 2026-07-20 night incident). Checked FIRST, mirroring limits.py's priority
# order; keep the pattern in sync with limits.py's _AUTH.
_LIMIT_AUTH = re.compile(
    r"\b401\b|invalid authentication|failed to authenticate|unauthor|"
    r"authentication[ _]required|"
    r"authentication_error|oauth.*(expired|invalid)|please run /login",
    re.IGNORECASE,
)
# QUOTA: the longer "you're out for a while" caps (Claude Pro/Max usage limits).
_LIMIT_QUOTA = re.compile(
    r"usage limit|weekly limit|quota|out of (extra )?usage|ran out of \w*\s*usage|"
    r"limit reached|reached your (usage|plan) limit|you'?ve reached|"
    r"plan limit|insufficient_quota|credit balance|"
    r"session limit|hit your [\w ]{0,16}limit",
    re.IGNORECASE,
)
# RATE: short-term throttling (per-min / 5-hour) + HTTP 429.
_LIMIT_RATE = re.compile(
    r"\b429\b|rate[ _-]?limit|too many requests|5[ -]?hour limit|"
    r"slow down|requests per",
    re.IGNORECASE,
)

_LIMIT_UNITS = {"s": 1, "m": 60, "h": 3600}

# "try again in 5 minutes", "reset in 10m", "wait 2 hours" (number + unit) …
_LIMIT_RETRY_AFTER_UNIT = re.compile(
    r"(?:retry[- ]after|try again in|reset[s]? in(?: about)?|wait)\D{0,8}?"
    r"(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|h|m|s)\b",
    re.IGNORECASE,
)
# … and the bare HTTP header form "Retry-After: 30" (seconds, no unit word).
_LIMIT_RETRY_AFTER_HEADER = re.compile(r"retry[- ]after:?\s*(\d+)\b", re.IGNORECASE)


def _parse_retry_after(text: str) -> int | None:
    """Best-effort parse of a stated reset delay → seconds. None if not stated.
    (Absolute reset *times* like 'resets at 10pm' are intentionally not parsed —
    that needs a clock/timezone; the host applies its default backoff instead.)"""
    t = text or ""
    m = _LIMIT_RETRY_AFTER_UNIT.search(t)
    if m:
        return int(m.group(1)) * _LIMIT_UNITS[m.group(2)[0].lower()]
    m = _LIMIT_RETRY_AFTER_HEADER.search(t)
    if m:
        return int(m.group(1))  # bare Retry-After is seconds
    return None


def _detect_usage_limit(text: str | None) -> tuple[bool, int | None]:
    """Is this error text CLEARLY a model usage/rate limit? → (matched, retry_after_s).

    Conservative on purpose: auth-shaped text is never a limit (waiting can't fix
    an expired login), and anything not matching the vendored QUOTA/RATE wording
    returns (False, None) so it flows through as a plain error for the host regex
    to classify. retry_after is None when the text states no relative delay.
    """
    t = text or ""
    if _LIMIT_AUTH.search(t):
        return False, None
    if _LIMIT_QUOTA.search(t) or _LIMIT_RATE.search(t):
        return True, _parse_retry_after(t)
    return False, None


def _failure_result(error_text: str, **extra) -> dict:
    """Build the terminal result payload for an agent/conversation failure.

    A clear usage/rate-limit wording becomes status="rate_limited" with the
    ORIGINAL error text preserved (the host falls back to regex-classifying it
    when retry_after is absent) plus retry_after seconds or None. Everything
    else is the plain status="error" payload, byte-for-byte as before.
    """
    matched, retry_after = _detect_usage_limit(error_text)
    payload: dict = {"status": "error", "error": error_text, **extra}
    if matched:
        payload["status"] = "rate_limited"
        payload["retry_after"] = retry_after
    return payload


# ─── Sandbox OOM evidence (spec 020, US1) ────────────────────────────────────
# The runner shares the container cgroup with the agent and provably survives
# the agent's OOM death, so IT captures the kernel's evidence — one file read,
# no docker lifecycle involvement. Contract with the queue's settle path:
# specs/020-sandbox-oom-legibility/contracts/runner-oom-marker.md. Everything
# here is best-effort and never-raises: unreadable cgroup files mean NO
# evidence, and the terminal error stays byte-identical to the pre-020 shape.

_CGROUP_MEMORY_EVENTS = "/sys/fs/cgroup/memory.events"
_CGROUP_MEMORY_MAX = "/sys/fs/cgroup/memory.max"


def _read_oom_kill_count() -> "int | None":
    """The cgroup v2 ``oom_kill`` counter, or None when unreadable/absent."""
    try:
        with open(_CGROUP_MEMORY_EVENTS, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("oom_kill "):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _sandbox_mem_cap_label() -> str:
    """Human-readable memory cap: the engine-declared env value when present
    (single source: the launch parameters), else the raw cgroup limit."""
    declared = os.environ.get("DEVCLAW_SANDBOX_MEMORY", "").strip()
    if declared:
        return declared
    try:
        with open(_CGROUP_MEMORY_MAX, encoding="utf-8") as fh:
            raw = fh.read().strip()
        if raw and raw != "max":
            return f"{raw} bytes"
    except OSError:
        pass
    return "unknown"


def _oom_annotate(error_text: str, baseline: "int | None") -> str:
    """Prefix the ``sandbox OOM-killed`` marker iff the cgroup recorded a NEW
    OOM kill since ``baseline``. No evidence ⇒ the text passes through
    unchanged (conservative: exit-137 alone is any SIGKILL, never proof)."""
    if baseline is None:
        return error_text
    now = _read_oom_kill_count()
    if now is None or now <= baseline:
        return error_text
    return (
        f"sandbox OOM-killed (cap={_sandbox_mem_cap_label()}, "
        f"oom_kill={now - baseline}): {error_text}"
    )


# ─── OOM-killer shield (spec 020, US2) ───────────────────────────────────────
# The runner, the agent, and the workload share ONE cgroup, so on memory
# exhaustion the kernel picks a victim among all three — and it took the
# supervisor twice on 2026-08-26. The container runs non-root, so scores can
# only be RAISED: every WORKLOAD process the runner spawns marks ITSELF as the
# preferred victim pre-exec, leaving the runner and agent at the default. A
# workload kill then surfaces as ordinary failed-command output the agent can
# read and adapt to, instead of a dead session. The agent's own bash children
# get the same self-raise via BASH_ENV (see _OOM_SHIELD_SCRIPT below).

#: oom_score_adj written by workload processes — ≈ +80% of total memory in the
#: kernel's badness score, dominating any realistic RSS difference.
_OOM_SCORE_WORKLOAD = "800"

#: The self-raise script baked into the sandbox image; sourced by every
#: non-interactive bash the agent spawns (BASH_ENV). Absent outside the image
#: (host-engine runs) — the env entry is then simply not set.
_OOM_SHIELD_SCRIPT = "/opt/devclaw/oom-shield.sh"


def _raise_own_oom_score() -> None:  # pragma: no cover — exercised pre-exec
    """preexec_fn: mark THIS (child) process as the preferred OOM victim.
    Raising is unprivileged; every failure is swallowed — the shield must
    never be the reason a workload fails to start."""
    try:
        with open("/proc/self/oom_score_adj", "w") as fh:
            fh.write(_OOM_SCORE_WORKLOAD)
    except OSError:
        pass



# ─── Chunk-slice watcher (spec 021, US1) ─────────────────────────────────────
# One worker session executes ONE story-slice of the speckit plan, and the
# limit is harness-enforced (clarified 2026-08-26): the watcher reads the
# worker's own specs/*/tasks.md (never writes it — the worker stays the single
# writer) and ends the turn when a full slice has flipped complete AND the
# agent then touches task rows OUTSIDE every advanced slice. The grammar
# mirrors devclaw/goal/slice_guard.py and is frozen in
# specs/021-worker-context-budget/contracts/chunk-grammar.md — the runner is a
# zero-dep standalone file and cannot import the host module, so the shared
# fixtures in tests/ hold the two parsers together.

_CHUNK_TASK_LINE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<rest>.+?)\s*$")
_CHUNK_TASK_ID = re.compile(r"\bT\d+\b")
_CHUNK_STORY_TAG = re.compile(r"\bUS\d+\b")

#: The follow-up prompt of the watcher's landing sequence (an ACP-level
#: message, not vendor wiring): the agent keeps all in-flight file state
#: because the session survives the turn cancel.
_LAND_SLICE_PROMPT = (
    "STOP — the story-slice you completed is this session's whole scope "
    "(one slice per session; the harness enforces the stop). Do not start "
    "any further slice. Land what you have now: make tasks.md honest about "
    "exactly what is done, run the relevant checks, commit the work with the "
    "specs/ artifacts, and end with the structured hand-back."
)


# ─── Context tripwire (spec 021, US2) ────────────────────────────────────────
# The graceful-degradation backstop under the slice watcher: when the agent's
# own usage reports say the context window is nearly spent, end the turn and
# land a coherent partial increment instead of running into the wall (the
# "Prompt is too long" wedge class, devclaw#707). Signal: the agent's
# `usage_update` session notifications carrying {used, size} — observed live
# from claude-agent-acp; an agent that reports nothing leaves the tripwire
# INERT with one loud note in the result (never fabricated, never guessed).

#: Fire threshold as a percentage of the reported window; 0 disables. The
#: engine declares it into the sandbox env (config doorway excludes runner/).
def _tripwire_threshold_pct() -> int:
    try:
        return int(os.environ.get("DEVCLAW_CONTEXT_TRIPWIRE_PCT", "75"))
    except ValueError:
        return 75


_LAND_BUDGET_PROMPT = (
    "STOP — your context budget is nearly spent (the harness measured it). "
    "Do not start anything new. Land a coherent partial increment now: make "
    "tasks.md honest about exactly what is done, run the relevant checks, "
    "commit the work with the specs/ artifacts, and end with the structured "
    "hand-back. Anything unfinished goes to FOLLOW-UPS — the next session "
    "continues from the workspace."
)


class _ContextTripwire:
    """Tracks the latest {used, size} from `usage_update` notifications and
    fires at most once when used/size crosses the threshold."""

    def __init__(self, threshold_pct: int) -> None:
        self.threshold_pct = threshold_pct
        self.fired = False
        self.landed = False
        self.seen = False
        self.used: "int | None" = None
        self.size: "int | None" = None

    def observe(self, params: object) -> bool:
        """Feed one session/update's params. True exactly once — at the
        observation that crosses the threshold."""
        if not isinstance(params, dict):
            return False
        upd = params.get("update")
        if not isinstance(upd, dict) or upd.get("sessionUpdate") != "usage_update":
            return False
        used, size = upd.get("used"), upd.get("size")
        if (
            not isinstance(used, (int, float)) or isinstance(used, bool)
            or not isinstance(size, (int, float)) or isinstance(size, bool)
            or size <= 0
        ):
            return False
        self.seen = True
        self.used, self.size = int(used), int(size)
        if self.threshold_pct <= 0 or self.fired:
            return False
        if used * 100 >= size * self.threshold_pct:
            self.fired = True
            return True
        return False


def _chunk_task_rows(text: str) -> "list[tuple[str, str | None, bool]]":
    """Mirror of slice_guard._task_rows: (key, story, checked) per checkbox
    line — key is the stable ``T<id>`` when present, else the trimmed label;
    story is the ``US<n>`` tag or None. Pure and total."""
    rows: "list[tuple[str, str | None, bool]]" = []
    for line in (text or "").splitlines():
        m = _CHUNK_TASK_LINE.match(line)
        if not m:
            continue
        rest = m.group("rest").strip()
        idm = _CHUNK_TASK_ID.search(rest)
        sm = _CHUNK_STORY_TAG.search(rest)
        key = idm.group(0) if idm else rest
        rows.append((key, sm.group(0) if sm else None, m.group("mark") in ("x", "X")))
    return rows


class _SliceWatcher:
    """Session-stop enforcement for one-chunk-per-session (spec 021 FR-001).

    Armed only when the workspace carries ≥2 incomplete story-slices at
    session start (FR-005: a single-chunk ask has zero ceremony). Re-reads
    each ``specs/*/tasks.md`` at tool-call boundaries (small files; a
    stat-gate provably misses same-size flips). Untagged (setup/polish) rows
    never trigger the stop
    — they ride whatever slice ships — so the worker's own wrap-up is never
    truncated. Everything is best-effort: an unreadable file during the
    session disarms detection for that file, never crashes the turn."""

    def __init__(self, workspace_dir: str) -> None:
        self.workspace_dir = workspace_dir
        self.armed = False
        self.stop_requested = False
        self.feature = ""
        self._start: "dict[tuple[str, str | None, str], bool]" = {}
        self._post_advance: "dict[tuple[str, str | None, str], bool] | None" = None
        self._advanced: "set[tuple[str, str]]" = set()

    def _tasks_paths(self) -> "list[str]":
        try:
            return sorted(
                _glob.glob(os.path.join(self.workspace_dir, "specs", "*", "tasks.md"))
            )
        except Exception:
            return []

    def _read_rows(self) -> "dict[tuple[str, str | None, str], bool]":
        """Current checkbox state across every tasks.md, keyed
        (feature, story, task-key) → checked. Re-read on every observation —
        the files are small, observations happen at tool-call boundaries, and
        an mtime/size gate provably misses a same-size flip inside one
        timestamp granule (`[ ]`→`[x]` is byte-length-neutral). An unreadable
        file contributes nothing."""
        rows: "dict[tuple[str, str | None, str], bool]" = {}
        for path in self._tasks_paths():
            feature = os.path.basename(os.path.dirname(path))
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    parsed = _chunk_task_rows(fh.read())
            except OSError:
                continue
            for key, story, checked in parsed:
                rows[(feature, story, key)] = checked
        return rows

    def arm(self) -> None:
        """Snapshot session-start state; arm iff ≥2 incomplete slices exist."""
        self._start = self._read_rows()
        incomplete: "set[tuple[str, str]]" = set()
        latest: "tuple[float, str] | None" = None
        for path in self._tasks_paths():
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            cand = (mtime, os.path.basename(os.path.dirname(path)))
            if latest is None or cand > latest:
                latest = cand
        self.feature = latest[1] if latest else ""
        for (feature, story, _key), checked in self._start.items():
            if story is not None and not checked:
                incomplete.add((feature, story))
        self.armed = len(incomplete) >= 2

    def _complete_slices(
        self, rows: "dict[tuple[str, str | None, str], bool]"
    ) -> "set[tuple[str, str]]":
        by_slice: "dict[tuple[str, str], bool]" = {}
        for (feature, story, _key), checked in rows.items():
            if story is None:
                continue
            slice_id = (feature, story)
            by_slice[slice_id] = by_slice.get(slice_id, True) and checked
        return {s for s, complete in by_slice.items() if complete}

    def advanced_slices(self) -> "list[str]":
        return sorted(story for (_f, story) in self._advanced)

    def active_slice(self) -> str:
        """The smallest incomplete slice of the current feature at session
        start — best-effort naming for the oversized-slice mark (FR-008)."""
        stories = sorted(
            story
            for (feature, story, _key), checked in self._start.items()
            if story is not None and feature == self.feature and not checked
        )
        return stories[0] if stories else ""

    def check(self) -> bool:
        """Advance the watcher one observation. True exactly once, at the
        moment the stop condition is first met."""
        if not self.armed or self.stop_requested:
            return False
        rows = self._read_rows()
        start_complete = self._complete_slices(self._start)
        now_complete = self._complete_slices(rows)
        advanced = {
            s for s in now_complete - start_complete
            # a slice with no rows at start (brand-new) still counts: it went
            # from not-complete (absent) to complete — build-ahead either way
        }
        if not advanced:
            return False
        if self._post_advance is None:
            self._advanced = advanced
            self._post_advance = rows
            return False
        # Stop when a STORY-tagged row outside every advanced slice changed
        # (flip either way, or a new row) since the slice completed.
        for (feature, story, key), checked in rows.items():
            if story is None or (feature, story) in self._advanced:
                continue
            if self._post_advance.get((feature, story, key)) != checked:
                self.stop_requested = True
                return True
        return False


# The engineer's return-contract (_RETURN_CONTRACT) tells it to end its final
# message with a STATUS field whose value is either ``DONE`` or
# ``BLOCKED: <one-line reason>`` when it genuinely cannot finish (a missing
# capability, contradictory/impossible instructions). We honor that self-report
# as a first-class terminal status instead of letting it ride invisibly inside
# agent_output. Anchored to the START of a line (after an optional ``STATUS:``
# prefix and light markdown decoration like ``**``/``>``) and case-sensitive on
# the uppercase keyword the contract prescribes — so prose like "the run was
# blocked: on X but I fixed it" mid-sentence can't false-positive. Model-agnostic:
# it parses the agent's OWN plain-text hand-back, no vendor tool-wiring.
_BLOCKED_LINE_RE = re.compile(
    r"^[ \t>#*_-]*(?:STATUS:[ \t]*)?BLOCKED:[ \t]*(.*?)[ \t*_]*$",
    re.MULTILINE,
)


def _parse_blocked_reason(agent_message: str | None) -> str | None:
    """If the agent's final hand-back self-reports ``BLOCKED``, return the reason.

    Returns None when there is no blocked self-report (the normal path). The LAST
    matching line wins — the hand-back is rendered last, so a later BLOCKED line
    is the authoritative terminal signal. A reason that parses to empty still
    returns a non-None placeholder: an honest "I'm blocked" with no stated reason
    must still surface as a block, never be lost as "no reason ⇒ not blocked".
    """
    if not agent_message:
        return None
    matches = _BLOCKED_LINE_RE.findall(agent_message)
    if not matches:
        return None
    reason = matches[-1].strip().strip("*_ ").strip()
    return reason or "worker reported BLOCKED without a stated reason"


# The hand-back's REPO NOTES field — durable repo-level facts for FUTURE tasks
# on the same repo (build/test quirks, non-obvious commands). Same parsing
# philosophy as the BLOCKED line: anchored to line start, light markdown
# decoration tolerated, the agent's OWN final message only, model-agnostic
# plain text. "none"/empty degrade to None — absence of notes is the normal
# case, never an error.
_REPO_NOTES_LINE_RE = re.compile(
    r"^[ \t>#*_-]*REPO NOTES:[ \t]*(.*?)[ \t*_]*$",
    re.MULTILINE,
)


def _parse_repo_notes(agent_message: str | None) -> str | None:
    """If the agent's final hand-back carries REPO NOTES, return them.

    The LAST matching line wins (mirrors ``_parse_blocked_reason``). A value
    of 'none' (any case) or empty returns None — the contract asks for 'none'
    explicitly, and an unfilled field must read as "nothing to record", not
    ride to the host as literal prose."""
    if not agent_message:
        return None
    matches = _REPO_NOTES_LINE_RE.findall(agent_message)
    if not matches:
        return None
    notes = matches[-1].strip().strip("*_ ").strip()
    if not notes or notes.lower().rstrip(".") == "none":
        return None
    return notes


def _agent_last_words(final_message: str, transcript: str, keep: int = 20_000) -> str:
    """The ``agent_output`` the result contract ships: the agent's last words.

    The agent's own final message IS the result (the structured hand-back for
    code kinds, the filled report for reviews). The captured decorative stdout
    is a transcript whose HEAD is banner + a verbatim echo of the wrapped
    prompt — never ship it as the result. Fall back to the transcript TAIL
    only when the agent never produced a message (a run that died mid-flight):
    the tail holds the last actions before death, the head only decoration.
    """
    if final_message and final_message.strip():
        return final_message.strip()
    return transcript[-keep:]


# `sys.__stdout__` is the original stdout the process was started with.
# The prefixed protocol lines (`event:` / `result:`) go straight to it so a
# stray library print into a swapped `sys.stdout` can never swallow them.
if sys.__stdout__ is None:  # pragma: no cover — no real interpreter hits this
    raise RuntimeError("runner requires a real stdout for the line protocol")
_PROTO_OUT: "TextIO" = sys.__stdout__


def _emit_result(payload: dict) -> None:
    """Write the final terminating `result: <json>` line and flush.

    The TS caller treats the first `result:` line as the run's verdict.
    Anything written to stdout AFTER this line is ignored.
    """
    _PROTO_OUT.write("result: " + json.dumps(payload) + "\n")
    _PROTO_OUT.flush()


def _emit_event(payload: dict) -> None:
    """Write one `event: <json>` line and flush.

    Flushing matters: the TS caller streams stdout line-by-line and writes
    each event to the events table the moment it arrives. Without flush
    we'd see a flood of events only at process exit.
    """
    _PROTO_OUT.write("event: " + json.dumps(payload) + "\n")
    _PROTO_OUT.flush()


# The stock worker agent: Anthropic's ACP bridge for Claude Code. Kept as a
# tuple so the default can't be mutated by a caller.
_DEFAULT_ACP_COMMAND = ("claude-agent-acp",)


def _resolve_acp_command(req: dict) -> list[str]:
    """The ACP agent command the worker session runs on.

    Payload first (the host passes it exactly like `model` — host env vars do
    NOT cross the container boundary, so an env-only override would silently
    do nothing in the sandbox), then DEVCLAW_ACP_COMMAND for a manual
    `docker run` / host-engine run, then the claude-agent-acp default. A
    string spec is shlex-split so quoted arguments survive
    (`my-acp --profile 'a b'` → 3 argv entries).
    """
    raw = (req.get("acp_command") or os.environ.get("DEVCLAW_ACP_COMMAND") or "").strip()
    if not raw:
        return list(_DEFAULT_ACP_COMMAND)
    return shlex.split(raw)


def _load_acp_client():
    """Import the sibling ``acp_client`` module file-relative (spec 011 D8).

    Works identically whether runner.py lives at /opt/devclaw (sandbox),
    runner/ (host engine mode), or was itself loaded via
    ``spec_from_file_location`` (tests) — no sys.path mutation, no package.
    """
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acp_client.py")
    spec = importlib.util.spec_from_file_location("devclaw_acp_client", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load acp_client from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- toolchain provisioning (ADR 0005) --------------------------------------
# The sandbox image ships NO language SDKs beyond python+node; the project's
# DECLARED toolchain is provisioned here, before the agent starts, and its
# environment exported into this process so the agent's shells AND the verify
# gate inherit it. Fail-closed: a declared toolchain that can't be provisioned
# settles the task `error` with a legible reason — silently running a .NET
# goal on a python+node box is exactly the silent degradation the hardening
# philosophy forbids.

# Wall-clock cap for `mise install`: the first task per toolchain version
# downloads whole SDKs (minutes); later tasks hit the per-project cache volume
# and finish in seconds.
_TOOLCHAIN_INSTALL_TIMEOUT_S = 900
#: mise-native declaration files — mise reads these directly, no translation.
_MISE_NATIVE_FILES = (".mise.toml", "mise.toml", ".tool-versions")


class ToolchainError(Exception):
    """A declared toolchain that cannot be provisioned. The message is the
    owner-actionable reason that rides the error result."""


def _translate_global_json(workspace_dir: str) -> dict:
    """``global.json`` ``sdk.version`` → ``{"dotnet": "<major.minor>"}``.

    Fuzzy major.minor, not the exact patch: global.json almost always rides
    rollForward semantics where any 9.0.x SDK satisfies "9.0.203", and exact
    patch pins frequently don't exist as installable versions. Present-but-
    unparseable raises (fail closed with the better message — dotnet itself
    would refuse the file later anyway); a global.json that pins no
    sdk.version declares nothing.
    """
    path = os.path.join(workspace_dir, "global.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ToolchainError(f"global.json is unreadable/invalid: {exc}")
    version = str((data.get("sdk") or {}).get("version") or "")
    m = re.match(r"(\d+)\.(\d+)", version)
    if not m:
        return {}
    return {"dotnet": f"{m.group(1)}.{m.group(2)}"}


def _translate_package_json(workspace_dir: str) -> dict:
    """``package.json`` ``engines.node`` → ``{"node": "<version prefix>"}`` —
    the first numeric component of the range (``^20.11`` → ``20.11``,
    ``>=20`` → ``20``, i.e. the minimum the project supports)."""
    path = os.path.join(workspace_dir, "package.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ToolchainError(f"package.json is unreadable/invalid: {exc}")
    engines = data.get("engines")
    spec = str(engines.get("node") or "") if isinstance(engines, dict) else ""
    m = re.search(r"(\d+(?:\.\d+)?)", spec)
    if not m:
        return {}
    return {"node": m.group(1)}


#: Directories never scanned for marker files (vendored deps / build output /
#: VCS): a ``.csproj`` under ``node_modules`` or a ``go.mod`` under ``vendor``
#: is not the project's OWN declaration. Hidden dirs (``.git``/``.venv``/…) are
#: skipped separately by the leading-dot check.
_MARKER_SCAN_SKIP = {"node_modules", "obj", "bin", "vendor", "target", "dist"}


def _iter_marker_files(workspace_dir: str, filename_globs: tuple) -> list:
    """Paths matching any of ``filename_globs`` at the workspace ROOT or in an
    immediate subdirectory (depth 1). Depth 1 covers the common monorepo layout
    — ``backend/Foo.csproj``, ``services/api/go.mod`` — the smoke case needs
    (lifekit-dashboard verifies with ``cd backend && dotnet test``, so its
    ``.csproj`` is one level down) without walking vendored/build trees. PURE:
    reads only the workspace's own declaration files, no host/process
    inference. Deterministic order (sorted) so a multi-project repo resolves the
    same version every run."""
    roots = [workspace_dir]
    try:
        for entry in sorted(os.listdir(workspace_dir)):
            full = os.path.join(workspace_dir, entry)
            if (
                os.path.isdir(full)
                and not entry.startswith(".")
                and entry not in _MARKER_SCAN_SKIP
            ):
                roots.append(full)
    except OSError:
        pass
    hits: list = []
    for root in roots:
        for pat in filename_globs:
            hits.extend(sorted(_glob.glob(os.path.join(root, pat))))
    return hits


def _dotnet_version_from_csproj(paths: list) -> "str | None":
    """First ``<TargetFramework(s)>net<major.minor></…>`` across the given
    ``.csproj`` files → ``"<major.minor>"`` (``net9.0`` → ``9.0``). Best-effort:
    an unreadable file or a non-``netX.Y`` framework (``netstandard2.0``,
    ``net48``) yields ``None`` — the caller then degrades the *version* to
    ``latest`` (marker presence still declares dotnet; version parsing must
    never error, per #424)."""
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"<TargetFrameworks?>[^<]*?net(\d+\.\d+)", text)
        if m:
            return m.group(1)
    return None


def _go_version_from_gomod(path: str) -> "str | None":
    """``go.mod`` ``go <x.y[.z]>`` directive → that version, else ``None``
    (bare/unreadable go.mod → caller degrades to ``latest``)."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"(?m)^go\s+(\d+\.\d+(?:\.\d+)?)", text)
    return m.group(1) if m else None


def _python_version_from_markers(py_version_paths: list, pyproject_paths: list) -> "str | None":
    """Python version from an explicit pin: ``.python-version`` (bare version,
    first line) wins, else ``pyproject.toml`` ``requires-python`` minimum
    (``>=3.11`` → ``3.11``). ``None`` when no version is pinned — python lives
    in the base image, so a version-LESS declaration is satisfied by the base
    (no provisioning), unlike dotnet/go/rust which degrade to ``latest``."""
    for path in py_version_paths:
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline().strip()
        except OSError:
            continue
        m = re.match(r"(\d+\.\d+(?:\.\d+)?)", first)
        if m:
            return m.group(1)
    for path in pyproject_paths:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r'(?m)^\s*requires-python\s*=\s*["\'][^"\']*?(\d+\.\d+)', text)
        if m:
            return m.group(1)
    return None


def _detect_marker_tools(workspace_dir: str) -> dict:
    """Generic marker-file → tool detection (#424): one data-driven table so a
    new stack is a row, not a code path. FALLBACK to the idiomatic translators
    (``global.json`` → dotnet, ``package.json`` → node): a version those already
    declared wins, because :func:`_detect_toolchain` merges this with
    ``setdefault``. dotnet/go/rust are absent from the base python+node image, so
    marker presence with no parseable version degrades the version to ``latest``
    (mise picks a sane default — never an error). python's version-less case
    yields nothing (the base python satisfies it). PURE over the workspace's
    declaration files."""
    tools: dict = {}
    if _iter_marker_files(workspace_dir, ("*.csproj", "*.sln")):
        tools["dotnet"] = (
            _dotnet_version_from_csproj(
                _iter_marker_files(workspace_dir, ("*.csproj",))
            )
            or "latest"
        )
    go_mods = _iter_marker_files(workspace_dir, ("go.mod",))
    if go_mods:
        tools["go"] = _go_version_from_gomod(go_mods[0]) or "latest"
    if _iter_marker_files(workspace_dir, ("Cargo.toml",)):
        tools["rust"] = "latest"
    py = _iter_marker_files(workspace_dir, (".python-version",))
    pyproject = _iter_marker_files(workspace_dir, ("pyproject.toml",))
    if py or pyproject:
        version = _python_version_from_markers(py, pyproject)
        if version:
            tools["python"] = version
    return tools


def _detect_toolchain(workspace_dir: str) -> tuple:
    """``(native, tools)`` for the workspace's declared toolchain.

    ``native=True`` → a mise-native file is present; mise reads it as-is and
    ``tools`` stays empty. Otherwise ``tools`` maps tool→version translated
    from idiomatic declarations (``global.json`` → dotnet, ``package.json`` →
    node) plus the generic marker-file table (#424: ``.csproj``/``.sln`` →
    dotnet, ``go.mod`` → go, ``Cargo.toml`` → rust, ``pyproject.toml``/
    ``.python-version`` → python). The idiomatic translators take precedence
    over the marker table (``setdefault``): a ``global.json`` that pins a dotnet
    version wins over the version parsed from a sibling ``.csproj``.
    ``(False, {})`` → nothing declared: provisioning is a zero-cost no-op (the
    base python+node image behavior).
    """
    for name in _MISE_NATIVE_FILES:
        if os.path.exists(os.path.join(workspace_dir, name)):
            return True, {}
    tools: dict = {}
    tools.update(_translate_global_json(workspace_dir))
    tools.update(_translate_package_json(workspace_dir))
    for tool, version in _detect_marker_tools(workspace_dir).items():
        tools.setdefault(tool, version)
    return False, tools


def _write_translated_mise_config(tools: dict) -> str:
    """Record TRANSLATED tools in a mise config OUTSIDE the workspace and
    OUTSIDE the user's real global config.

    Not in /workspace: a generated file there would dirty the diff the review
    gate and delivery see. Not ~/.config/mise/config.toml: a host-engine run
    must never clobber the developer's own mise setup. MISE_GLOBAL_CONFIG_FILE
    points mise at the tempfile; set in ``os.environ`` so every later mise
    call — and the agent's own shells — resolve the same declaration."""
    fd, path = tempfile.mkstemp(prefix="devclaw-mise-", suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("[tools]\n")
        for name in sorted(tools):
            fh.write(f'{name} = "{tools[name]}"\n')
    os.environ["MISE_GLOBAL_CONFIG_FILE"] = path
    return path


def _mise_run(args: list, workspace_dir: str, timeout: float) -> "subprocess.CompletedProcess":
    """One bounded mise invocation, cwd=workspace so mise sees the project's
    own config files. MISE_TRUSTED_CONFIG_PATHS covers the workspace (mise
    refuses untrusted config in non-interactive runs otherwise); MISE_YES
    kills any residual prompt."""
    env = dict(os.environ)
    env.setdefault("MISE_TRUSTED_CONFIG_PATHS", workspace_dir)
    env["MISE_YES"] = "1"
    return subprocess.run(
        ["mise", *args],
        cwd=workspace_dir,
        env=env,
        capture_output=True,
        preexec_fn=_raise_own_oom_score,  # workload, not supervisor (spec 020)
        text=True,
        timeout=timeout,
    )


def _provision_toolchain(workspace_dir: str) -> dict | None:
    """Detect and provision the project-declared toolchain.

    Returns a summary dict for the observability event (tool list + duration —
    the data that decides whether cold-start ever needs optimizing), or None
    when nothing is declared and nothing was done. Raises :class:`ToolchainError`
    on ANY failure — the caller settles the task error, never a silent skip.
    """
    native, tools = _detect_toolchain(workspace_dir)
    if not native and not tools:
        return None
    if shutil.which("mise") is None:
        # A declared toolchain with no mise on PATH is a stale sandbox image
        # or an unprepared host-engine run — the deploy-skew class
        # (lifekit-stack#93). Loud, never a silent python+node fallback.
        raise ToolchainError(
            "project declares a toolchain but `mise` is not on PATH "
            "(stale sandbox image, or DEVCLAW_ENGINE=host without mise "
            "installed on the host)"
        )
    if tools:
        _write_translated_mise_config(tools)
    started = time.time()
    try:
        install = _mise_run(["install"], workspace_dir, _TOOLCHAIN_INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ToolchainError(
            f"`mise install` exceeded {_TOOLCHAIN_INSTALL_TIMEOUT_S}s"
        )
    if install.returncode != 0:
        tail = (install.stderr or install.stdout or "").strip()[-2000:]
        raise ToolchainError(f"`mise install` failed (exit {install.returncode}): {tail}")
    # Export the provisioned environment (PATH shims, DOTNET_ROOT, …) into
    # THIS process so the agent's shells and the verify gate inherit the same
    # toolchain — "`dotnet test` must find the mise-installed SDK" is handled
    # here structurally, not per-stack.
    try:
        envp = _mise_run(["env", "--json"], workspace_dir, 60)
    except subprocess.TimeoutExpired:
        raise ToolchainError("`mise env --json` timed out")
    if envp.returncode != 0:
        tail = (envp.stderr or envp.stdout or "").strip()[-2000:]
        raise ToolchainError(f"`mise env --json` failed (exit {envp.returncode}): {tail}")
    try:
        env_map = json.loads(envp.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ToolchainError(f"`mise env --json` returned non-JSON: {exc}")
    if not isinstance(env_map, dict):
        raise ToolchainError("`mise env --json` returned a non-object")
    for key, value in env_map.items():
        # mise honors [env] tables in the (trusted) workspace's own config, so
        # this map is workspace-controlled — re-apply the OAuth-only denylist:
        # a project's .mise.toml must not reintroduce a metered API key AFTER
        # _refuse_api_key() already passed.
        if key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            continue
        if isinstance(value, str):
            os.environ[key] = value
    return {
        "native": native,
        "tools": tools,
        "seconds": round(time.time() - started, 1),
    }


def _resync_mise_env(workspace_dir: str) -> None:
    """Re-derive the mise-provisioned toolchain env and re-apply it to
    ``os.environ`` RIGHT BEFORE the verify gate.

    The pre-agent :func:`_provision_toolchain` export is a NO-OP for a repo that
    pins its SDK the native way — a .NET project with a ``.csproj``/``.sln`` but
    no ``global.json``/``.mise.toml`` declares nothing ``_detect_toolchain``
    recognizes, so nothing is installed up front. The agent then declares +
    installs the toolchain itself mid-task (writes a ``mise.toml``, ``mise
    install``), but that happened AFTER our pre-agent env snapshot — so the
    gate's raw ``dotnet test`` exits 127 ("command not found") even though the
    SDK is installed. Re-syncing here closes the gap structurally (ADR 0005's
    own intent: "the agent's shells AND the verify gate inherit the same
    toolchain").

    Two mechanisms, both best-effort — mise absent / no config / any error leaves
    the env untouched and the gate still runs (a genuinely missing toolchain then
    fails the gate LOUDLY, never a silent skip):
      - put mise's shims dir on PATH (cwd-robust: a shim resolves the tool
        version from whichever config is nearest at exec time, so it works even
        when the gate ``cd``s into a subdir before invoking the tool);
      - fold in ``mise env --json`` (``DOTNET_ROOT`` + tool bin dirs) for the
        workspace's now-current config.
    The OAuth-only denylist is re-applied (a workspace ``.mise.toml`` ``[env]``
    table must not reintroduce a metered key after ``_refuse_api_key`` passed).
    """
    if shutil.which("mise") is None:
        return
    # 1) Fold in `mise env --json` (DOTNET_ROOT + the tool bin dirs it prepends
    #    to PATH) for the workspace's now-current config. Best-effort.
    try:
        envp = _mise_run(["env", "--json"], workspace_dir, 60)
    except (subprocess.TimeoutExpired, OSError):
        envp = None
    if envp is not None and envp.returncode == 0:
        try:
            env_map = json.loads(envp.stdout or "{}")
        except json.JSONDecodeError:
            env_map = None
        if isinstance(env_map, dict):
            for key, value in env_map.items():
                if key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                    continue
                if isinstance(value, str):
                    os.environ[key] = value
    # 2) Prepend mise's shims dir LAST so it always wins — cwd-robust tool
    #    resolution that survives even if `mise env` returned no PATH (a config
    #    mise couldn't resolve from the workspace root). Done after (1) so the
    #    mise-env PATH can't clobber it.
    mise_data = os.environ.get("MISE_DATA_DIR") or os.path.expanduser(
        "~/.local/share/mise"
    )
    shims = os.path.join(mise_data, "shims")
    if os.path.isdir(shims) and shims not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = shims + os.pathsep + os.environ.get("PATH", "")


def _ensure_mise_shims_on_path() -> None:
    """Prepend mise's shims dir to PATH so the AGENT's shells find a toolchain it
    provisions itself mid-task.

    The verify-gate twin of this (re-syncing the gate's env) is a separate
    concern; this is the agent side. For a repo that pins its SDK the native way
    — a .NET project with a ``.csproj``/``.sln`` but no ``global.json``/
    ``.mise.toml`` — :func:`_detect_toolchain` sees nothing, so the pre-agent
    :func:`_provision_toolchain` is a no-op and never puts mise shims on PATH.
    The agent's shell then inherits the bare image PATH, hits ``dotnet: command
    not found``, and (2026-07-29 v0.1 smoke, lifekit-hq/lifekit-dashboard) can
    derail into "fixing" the environment instead of doing the ticket.

    Prepending the shims dir up front is cwd-robust and harmless when empty: a
    shim resolves its tool+version lazily at exec time from whichever config is
    nearest, so once the agent runs ``mise install`` the tool is immediately on
    PATH — no ``PATH=…/shims:$PATH`` hand-prefixing. Deliberately does NOT gate
    on the dir existing yet (mise creates it at install time, AFTER this runs).
    Best-effort: no mise ⇒ PATH untouched; idempotent (won't double-prepend).
    """
    if shutil.which("mise") is None:
        return
    mise_data = os.environ.get("MISE_DATA_DIR") or os.path.expanduser(
        "~/.local/share/mise"
    )
    shims = os.path.join(mise_data, "shims")
    path = os.environ.get("PATH", "")
    if shims not in path.split(os.pathsep):
        os.environ["PATH"] = shims + os.pathsep + path


def _refuse_api_key() -> None:
    """Refuse to run if an API key snuck into the env — preserves the
    Pro-subscription cost model (memory: pro-subscription-is-the-design)."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(var):
            _emit_result(
                {
                    "status": "error",
                    "error": (
                        f"{var} is set in the environment. DevClaw v2 runs "
                        "exclusively through Claude Code OAuth — refusing to "
                        "spend metered credits."
                    ),
                }
            )
            sys.exit(2)


def main() -> None:
    _refuse_api_key()

    if len(sys.argv) != 2:
        _emit_result({"status": "error", "error": "expected one JSON arg"})
        sys.exit(2)

    try:
        req = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        _emit_result({"status": "error", "error": f"invalid JSON: {exc}"})
        sys.exit(2)

    workspace_dir = req.get("workspace_dir")
    goal = req.get("goal")
    kind = req.get("kind", "implement_feature")
    # Model tier for the agent. The host passes it in the payload; fall back to
    # DEVCLAW_EXEC_MODEL for a manual `docker run`. None → the ACP server default.
    acp_model = req.get("model") or os.environ.get("DEVCLAW_EXEC_MODEL") or None
    # The ACP agent binary itself — payload → env → claude-agent-acp default.
    try:
        acp_command = _resolve_acp_command(req)
    except ValueError as exc:
        # shlex refuses e.g. an unbalanced quote. Fail loud with the knob's
        # name instead of a bare traceback — an operator typo would otherwise
        # fail every dispatch with no hint where the bad spec lives.
        _emit_result(
            {
                "status": "error",
                "error": (
                    "invalid ACP command spec (payload acp_command / "
                    f"DEVCLAW_ACP_COMMAND): {exc}"
                ),
            }
        )
        sys.exit(2)
    verify_cmd = req.get("verify_cmd")  # optional gate run after the agent finishes
    if not workspace_dir or not goal:
        _emit_result(
            {
                "status": "error",
                "error": "request must include workspace_dir and goal",
            }
        )
        sys.exit(2)

    if kind not in _KNOWN_KINDS:
        _emit_result({"status": "error", "error": f"unknown kind: {kind}"})
        sys.exit(2)

    if kind == "validate_product":
        # Agent-less by construction (spec 015 FR-005): no skills, no ACP
        # session, no LLM — boot the declared contract, run the suites, report.
        _run_validate_product(req, workspace_dir)
        return

    # Wrap the user's goal with kind-specific operating instructions. The
    # ACP-driven agent session reads this as the user message,
    # so prepending instructions here is the cheapest way to bias behavior
    # without a custom system prompt. Skills now live in /opt/devclaw/skills/
    # and are loaded per-kind by _wrap_goal.
    try:
        wrapped_goal = _wrap_goal(kind, goal, workspace_dir=workspace_dir)
    except RuntimeError as exc:
        # Loud, but still legible: the host parses `result:` lines, and a bare
        # traceback settles the task as an opaque crash instead of naming the
        # broken image / mis-wired engine. Same shape as the toolchain gate below.
        _emit_result({"status": "error", "error": f"skills_missing: {exc}"})
        sys.exit(2)

    os.makedirs(workspace_dir, exist_ok=True)

    # Provision the project-declared toolchain (ADR 0005) BEFORE the agent
    # starts, so its shells and the verify gate inherit it. Fail CLOSED with a
    # legible reason — a declared-but-unprovisionable toolchain must never
    # silently degrade to the base python+node image.
    try:
        provisioned = _provision_toolchain(workspace_dir)
    except ToolchainError as exc:
        _emit_result({"status": "error", "error": f"toolchain_provision_failed: {exc}"})
        sys.exit(2)
    except Exception as exc:  # unexpected — still fail closed, still legible
        _emit_result(
            {
                "status": "error",
                "error": (
                    f"toolchain_provision_failed: unexpected "
                    f"{exc.__class__.__name__}: {exc}"
                ),
            }
        )
        sys.exit(2)
    if provisioned is not None:
        # Observability: tool list + duration — the measurement that decides
        # whether cold-start ever warrants baked-image optimization.
        _emit_event(
            {
                "id": None,
                "type": "ToolchainProvision",
                "source": "runner",
                "ts": int(time.time() * 1000),
                "payload": provisioned,
            }
        )

    # Put mise's shims dir on PATH BEFORE the agent starts, so a toolchain the
    # agent provisions itself mid-task (a .csproj/.sln repo the pre-agent step
    # can't detect) lands on its shell PATH without hand-prefixing. MUST run
    # before the AcpClient env below reads os.environ["PATH"].
    _ensure_mise_shims_on_path()

    # Drop the sandbox-only MCP config into the workspace so claude auto-
    # discovers it at project scope. The image bakes /opt/devclaw/sandbox-mcp.json
    # (Playwright MCP only); we don't mount the host's mcpServers because the
    # sandcastle allowlist deliberately excludes them. Skip if the workspace
    # already has its own .mcp.json so a project can override. Mark the file
    # as locally-ignored via .git/info/exclude so the agent's `git add .` can't
    # accidentally commit it; we also remove it in the finally block below in
    # case the workspace isn't a git repo.
    _baked_mcp = "/opt/devclaw/sandbox-mcp.json"
    _workspace_mcp = os.path.join(workspace_dir, ".mcp.json")
    _mcp_dropped = False
    if os.path.exists(_baked_mcp) and not os.path.exists(_workspace_mcp):
        try:
            shutil.copyfile(_baked_mcp, _workspace_mcp)
            _mcp_dropped = True
        except OSError:
            # Best-effort: a read-only workspace mount shouldn't fail the run.
            pass
    if _mcp_dropped:
        _exclude = os.path.join(workspace_dir, ".git", "info", "exclude")
        if os.path.isdir(os.path.dirname(_exclude)):
            try:
                with open(_exclude, "a", encoding="utf-8") as fh:
                    fh.write("\n.mcp.json\n")
            except OSError:
                pass

        def _cleanup_mcp() -> None:
            try:
                os.remove(_workspace_mcp)
            except OSError:
                pass

        atexit.register(_cleanup_mcp)

    # Hook warnings accumulated across pre/post hooks. Surfaced in the result
    # payload so the goal layer's evaluator can read them (e.g. "you added
    # e2e tests but verify_cmd does not run them"). Hooks are best-effort —
    # their warnings are advisory, the verify gate is the source of truth.
    # Pre-run hook fires AFTER the MCP config drop so it sees the final
    # workspace state. _run_hook fires both the universal hook AND any per-repo
    # hook in <workspace>/.agent/hooks/, returning a list of tagged warnings.
    task_id = str(req.get("task_id") or "")
    hook_warnings: list[str] = []
    hook_warnings.extend(_run_hook("pre-run", workspace_dir, kind, task_id))

    # Default to a PATH lookup — inside the sandbox the Dockerfile sets
    # CLAUDE_CODE_EXECUTABLE=/usr/bin/claude, so this fallback only matters for
    # host/misconfigured runs. (Was a hardcoded personal path — a leak + footgun.)
    claude_exec = os.environ.get("CLAUDE_CODE_EXECUTABLE") or "claude"
    claude_cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")

    # The ACP client — devclaw's own agent-drive seam (spec 011). Loaded
    # file-relative so it resolves identically in the sandbox image
    # (/opt/devclaw/), host engine mode (runner/), and the test
    # suite's spec_from_file_location pattern.
    try:
        acp = _load_acp_client()
    except Exception as exc:
        _emit_result(
            {
                "status": "error",
                "error": (
                    "acp_client.py not loadable next to runner.py — the "
                    "runner install is incomplete."
                ),
                "trace": f"{exc.__class__.__name__}: {exc}",
            }
        )
        sys.exit(2)

    # The agent subprocess env is an explicit ALLOWLIST: beyond _refuse_api_key
    # above, a refused credential can never ride an inherited environ into the
    # agent. ANTHROPIC_MODEL tiers the agent (ACP has no standard model field;
    # the claude CLI honors the env var); unset → the agent's default.
    acp_env = {
        "CLAUDE_CODE_EXECUTABLE": claude_exec,
        "CLAUDE_CONFIG_DIR": claude_cfg,
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    # The ONE sanctioned credential (#644): the instance's subscription
    # setup-token, injected into the container env by the engine. It must be
    # forwarded EXPLICITLY — this env is an allowlist, and without this entry
    # the agent never sees the token and silently falls back to the mounted
    # ~/.claude login, whose overnight expiry is exactly the outage class the
    # setup-token exists to end (live-found 2026-08-24). The refused metered
    # keys (ANTHROPIC_API_KEY/AUTH_TOKEN) stay out by construction.
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if oauth_token:
        acp_env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    # OOM shield for the agent's OWN bash children (spec 020 US2): every
    # non-interactive bash sources $BASH_ENV, so each tool child self-raises
    # its oom_score_adj and the kernel prefers the workload over the agent.
    # Agent-agnostic (plain bash mechanism, no vendor wiring); set only when
    # the sandbox image ships the script — host-engine runs stay untouched.
    if os.path.exists(_OOM_SHIELD_SCRIPT):
        acp_env["BASH_ENV"] = _OOM_SHIELD_SCRIPT
    # Cage visibility (spec 020 US3): the engine declares the ENFORCED
    # sandbox allocation; forward it so the agent sizes its tooling from the
    # truth instead of the host-lying /proc/meminfo and nproc.
    for _sizing_var in ("DEVCLAW_SANDBOX_MEMORY", "DEVCLAW_SANDBOX_CPUS"):
        _sizing_val = os.environ.get(_sizing_var, "").strip()
        if _sizing_val:
            acp_env[_sizing_var] = _sizing_val
    if acp_model:
        acp_env["ANTHROPIC_MODEL"] = acp_model

    # Chunk-slice watcher (spec 021 US1): armed only for a multi-slice
    # workspace; observes tool-call boundaries via the event stream and ends
    # the turn mechanically when a completed slice is left behind for the
    # next one. `client` is assigned below — the closure reads it at call
    # time, after construction.
    watcher = _SliceWatcher(workspace_dir)
    watcher.arm()

    def _emit_and_watch(ev: dict) -> None:
        _emit_event(ev)
        if (
            watcher.armed
            and not watcher.stop_requested
            and ev.get("type") == "ACPToolCallEvent"
        ):
            if watcher.check():
                client.cancel_turn()

    # Context tripwire (spec 021 US2): reads the agent's own usage stream via
    # the client's on_update seam; fires at most once per session.
    tripwire = _ContextTripwire(_tripwire_threshold_pct())

    def _observe_update(params: dict) -> None:
        if tripwire.observe(params):
            _emit_event(
                {
                    "id": None,
                    "type": "ContextTripwire",
                    "source": "runner",
                    "ts": int(time.time() * 1000),
                    "payload": {
                        "used": tripwire.used,
                        "size": tripwire.size,
                        "threshold_pct": tripwire.threshold_pct,
                        "active_slice": watcher.active_slice() or None,
                    },
                }
            )
            client.cancel_turn()

    client = acp.AcpClient(
        acp_command, acp_env, on_event=_emit_and_watch, on_update=_observe_update
    )

    usage: dict | None = None
    # Snapshot the cgroup OOM counter before the agent runs so a kill DURING
    # this session is distinguishable from pre-existing history (spec 020).
    oom_baseline = _read_oom_kill_count()
    try:
        try:
            outcome = client.run(workspace_dir, wrapped_goal)
            if client.turn_cancel_requested and outcome.stop_reason == "cancelled":
                # The landing sequence (spec 021): the session survives the
                # turn cancel with all file state intact; the follow-up turn
                # commits and hands back, then the normal verify/result path
                # runs on ITS outcome. The slice watcher's prompt wins when
                # both it and the tripwire stopped the turn.
                land_prompt = (
                    _LAND_SLICE_PROMPT if watcher.stop_requested else _LAND_BUDGET_PROMPT
                )
                outcome = client.prompt(land_prompt)
                if tripwire.fired:
                    tripwire.landed = outcome.stop_reason != "cancelled"
            elif tripwire.fired and outcome.stop_reason == "end_turn":
                # The agent finished its turn before the cancel took effect —
                # nothing was interrupted, so the session counts as landed.
                tripwire.landed = True
            if outcome.stop_reason == "cancelled" and not client.turn_cancel_requested:
                # Fail CLOSED: an externally-cancelled turn used to leak
                # through as status="ok" on whatever half-finished state the
                # agent left behind (the pre-021 hole). Only the runner's own
                # landing sequence may ride a cancelled stopReason.
                raise acp.AcpError(
                    "agent turn was cancelled without a runner-initiated "
                    "landing — treating as a failure, never an ok result"
                )
        finally:
            client.close()
        usage = outcome.usage
        if outcome.stop_reason == "refusal":
            # A refusal is a failed task with the agent's own words as the
            # reason — never a silent success.
            raise acp.AcpError(
                "agent refused the task: "
                + (outcome.last_agent_message or "(no final message)")
            )
    except Exception as exc:
        # A clear usage/rate limit becomes status="rate_limited" so the host
        # pauses-and-resumes instead of retry-burning quota; anything ambiguous
        # stays status="error" (the host regex fallback classifies the text).
        # OOM evidence check (spec 020): if the cgroup recorded a kill during
        # this session, stamp the marker so the queue classifies the failure
        # as a deterministic environment cap instead of retrying it.
        # Oversized-slice mark (spec 021 FR-008): a context overflow while the
        # watcher was armed names the slice being worked, so the goal layer's
        # next brief demands a re-slice of THAT slice instead of a blind
        # "smaller scope". Appended into the error text — the settle path
        # matches its markers with `in`, so a suffix is safe.
        err_text = str(exc)
        if (
            watcher.armed
            and "Prompt is too long" in err_text
            and watcher.active_slice()
        ):
            err_text += (
                f" [active_slice: {watcher.feature} {watcher.active_slice()}]"
            )
        err_payload = _failure_result(
            _oom_annotate(err_text, oom_baseline),
            trace=traceback.format_exc(),
            agent_output=_agent_last_words(
                client.last_agent_message, client.stderr_tail()
            ),
        )
        if tripwire.fired:
            # The landing sequence did not complete — the firing still rides
            # the result so the host counts it (recovered=False).
            err_payload["tripwire"] = {
                "threshold_pct": tripwire.threshold_pct,
                "used": tripwire.used,
                "size": tripwire.size,
                "active_slice": watcher.active_slice() or None,
                "landed": False,
            }
        if hook_warnings:
            err_payload["hook_warnings"] = hook_warnings
        _emit_result(err_payload)
        sys.exit(1)

    # Honest-exit: the engineer's return-contract lets it self-report
    # `BLOCKED: <reason>` when it genuinely cannot finish (a missing capability,
    # contradictory or impossible instructions). Promote that self-report to a
    # first-class terminal status so the host can surface it as a legible block
    # instead of retry-burning a doomed generic failure — a block is NOT an
    # approval, so we short-circuit BEFORE the verify gate and NEVER settle
    # "ok" (fail-closed). Parsed from the agent's OWN final message, not the
    # captured decorative stdout (which echoes the prompt's contract text).
    blocked_reason = _parse_blocked_reason(client.last_agent_message)
    repo_notes = _parse_repo_notes(client.last_agent_message)
    if blocked_reason is not None:
        blocked_payload: dict = {
            "status": "blocked",
            "reason": blocked_reason,
            "workspace_dir": workspace_dir,
            "agent_output": _agent_last_words(
            client.last_agent_message, client.stderr_tail()
        ),
        }
        if usage:
            blocked_payload["usage"] = usage
        if repo_notes:
            blocked_payload["repo_notes"] = repo_notes
        if hook_warnings:
            blocked_payload["hook_warnings"] = hook_warnings
        _emit_result(blocked_payload)
        return

    # Post-run hook: mechanical checks against what the agent shipped (e.g.
    # "you added browser tests but verify_cmd is still pytest-only"). Runs
    # BEFORE the verify gate so the hook can pass verify_cmd to its diff-aware
    # checks and so its warnings ride alongside the gate verdict in the result.
    hook_warnings.extend(
        _run_hook("post-run", workspace_dir, kind, task_id, verify_cmd or "")
    )

    result_payload = {
        "status": "ok",
        "workspace_dir": workspace_dir,
        "message": "agent run completed.",
        "agent_output": _agent_last_words(
            client.last_agent_message, client.stderr_tail()
        ),
    }
    if watcher.armed:
        # Optional field per contracts/runner-result.md (spec 021): absent
        # means the watcher never armed (single-chunk fast path, FR-005).
        watcher.check()  # final observation — catch a flip after the last tool call
        result_payload["chunk"] = {
            "feature": watcher.feature,
            "advanced_slices": watcher.advanced_slices(),
            "stopped_by_watcher": watcher.stop_requested,
        }
    # Context-budget observability (spec 021 US2, contracts/runner-result.md):
    # the last observed usage, the firing record, or the loud inert note —
    # never fabricated (no usage stream ⇒ no `context` field).
    if tripwire.seen:
        result_payload["context"] = {"used": tripwire.used, "size": tripwire.size}
    if tripwire.fired:
        result_payload["tripwire"] = {
            "threshold_pct": tripwire.threshold_pct,
            "used": tripwire.used,
            "size": tripwire.size,
            "active_slice": watcher.active_slice() or None,
            "landed": bool(tripwire.landed),
        }
    elif tripwire.threshold_pct > 0 and not tripwire.seen:
        result_payload["usage_absent_note"] = (
            f"context tripwire configured at {tripwire.threshold_pct}% but the "
            "agent reported no usage stream — tripwire inert this session"
        )
    if usage:
        result_payload["usage"] = usage
    if repo_notes:
        result_payload["repo_notes"] = repo_notes
    if hook_warnings:
        result_payload["hook_warnings"] = hook_warnings

    # Verify gate: the agent loop finished, but "done" means the project's own
    # test/build command passes — run it now and attach the verdict. The host
    # (TaskQueue) decides done-vs-failed from `verify.passed`; here we just run it
    # and report. Emitted as an event too so it shows in the live stream.
    if verify_cmd:
        # Point Playwright's JSON reporter at a devclaw-owned path so that IF the
        # verify gate runs browser E2E (`npx playwright test --reporter=json`),
        # the run's real counts survive to the host browser-gate. Set
        # unconditionally — harmless when the gate isn't a browser suite (nothing
        # writes the file, and the host reads its absence as fail-closed only for
        # a frontend change with a playwright config).
        report_path = os.path.join(workspace_dir, _BROWSER_REPORT_REL)
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        os.environ["PLAYWRIGHT_JSON_OUTPUT_NAME"] = report_path
        # Re-sync the mise toolchain env so a gate like `dotnet test` finds an
        # SDK the agent provisioned mid-task (a .csproj/.sln repo the pre-agent
        # step couldn't detect). MUST run before _run_verify, which inherits
        # os.environ. Best-effort — see _resync_mise_env.
        _resync_mise_env(workspace_dir)
        verify = _run_verify(verify_cmd, workspace_dir)
        browser_report = _read_browser_report(workspace_dir)
        if browser_report is not None:
            verify["browser_report"] = browser_report
        result_payload["verify"] = verify
        _emit_event(
            {
                "id": "verify",
                "type": "VerifyResult",
                "source": "devclaw",
                "ts": int(time.time() * 1000),
                "payload": {
                    "cmd": verify["cmd"],
                    "passed": verify["passed"],
                    "exit_code": verify["exit_code"],
                    "timed_out": verify["timed_out"],
                },
            }
        )

    _emit_result(result_payload)


if __name__ == "__main__":
    main()
