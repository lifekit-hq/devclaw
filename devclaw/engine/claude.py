"""The engine — ``claude -p --output-format stream-json`` in the goal's worktree.

One task is one Claude Code session run headless in the task's workspace (a
treehouse worktree leased per goal, :mod:`devclaw.engine.workspace`). The
stream is read frame by frame: every frame becomes an :class:`EngineEvent`,
the ``system/init`` frame's ``session_id`` is reported through
``on_session`` the moment it arrives, and the ``result`` frame carries the
final message and the usage block.

**Liveness is the stream, never a clock** (ruled 2026-09-17, Q3). A session
that emits no first frame within ``DEVCLAW_FIRST_FRAME_S`` or then goes silent
for ``DEVCLAW_SILENCE_S`` is killed and returned as an error — the queue
settles it ``INTERRUPTED`` and the next row starts a fresh session on the reset
goal branch; ``--resume`` continues a session only after a quota pause.
There is no duration cap.

**The worktree runs what CI runs before the session ends.** For a code kind
the engine runs ``.devclaw/verify`` after the session hands back and checks the
first-run manifest is TRACKED; a red run is handed back to the SAME session
(``--resume``), up to ``DEVCLAW_VERIFY_ROUNDS`` times.

**The branch ships through no-mistakes** (#953). Once verify is green and the
branch carries a commit ``origin`` has not seen, the SAME session runs
``no-mistakes axi run`` (review, test, document, lint, push, PR) and answers
its gates. Its CI step is skipped: the goal layer reads CI, and a red CI stops
the goal. The engine never takes the session's word that it shipped: the
branch's newest no-mistakes run must carry the session's head and have
passed, or the session is an error.

⚠ This is not a filesystem sandbox: the agent runs as devclaw's user in a
worktree. The credential strip (``credentials.session_env``) still holds —
the metered API keys and every host-only credential never reach its env.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import EngineEvent, EngineRequest, EngineResult
from .. import config as _config
from .. import credentials as _credentials
from ..git_identity import git_identity_env

#: A single stream-json frame can be large (a file read, a big diff). asyncio's
#: default 64 KiB line limit crashed a real run on one oversized frame.
STREAM_LINE_LIMIT = 64 * 1024 * 1024

#: The ONE home for worker-kind instructions: ``devclaw/skills/``.
#: ``_common.md`` always, ``_writes-code/*.md`` for code kinds, ``<kind>/*.md``,
#: then any other root ``*.md``; ``craft/`` is never concatenated — the session
#: reads it when a task calls for it. Per-repo skills under
#: ``<workspace>/.agent/skills/`` are appended in the same layout.
SKILLS_DIR = str(Path(__file__).resolve().parents[1] / "skills")
WRITES_CODE_KINDS = frozenset({"implement_feature", "fix_bug"})
_KNOWN_KINDS = frozenset({"implement_feature", "fix_bug", "review_repository"})

#: system frames that are noise in the event record (per-token estimates, the
#: operator's own hook chatter).
_QUIET_SYSTEM = ("thinking_tokens", "hook_")


def _skill_paths(root: str, kind: str) -> list[str]:
    paths: list[str] = []
    common = os.path.join(root, "_common.md")
    if os.path.exists(common):
        paths.append(common)
    if kind in WRITES_CODE_KINDS:
        paths.extend(sorted(glob.glob(os.path.join(root, "_writes-code", "*.md"))))
    paths.extend(sorted(glob.glob(os.path.join(root, kind, "*.md"))))
    already = set(paths)
    paths.extend(p for p in sorted(glob.glob(os.path.join(root, "*.md"))) if p not in already)
    return paths


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_skills(kind: str, workspace_dir: Optional[str] = None) -> str:
    paths = _skill_paths(SKILLS_DIR, kind)
    if workspace_dir:
        paths.extend(_skill_paths(os.path.join(workspace_dir, ".agent", "skills"), kind))
    text = "\n\n---\n\n".join(b for b in (_read(p) for p in paths) if b)
    return text.replace("{craft_dir}", os.path.join(SKILLS_DIR, "craft") + os.sep)


def wrap_goal(kind: str, goal: str, workspace_dir: Optional[str] = None) -> str:
    """Skills first, then the goal. A missing bundle fails LOUD — the worker
    never runs on an unbriefed prompt."""
    effective = kind if kind in _KNOWN_KINDS else "implement_feature"
    skills = load_skills(effective, workspace_dir)
    if not skills:
        raise RuntimeError(f"no skill files found for kind {effective!r} under {SKILLS_DIR!r}")
    return f"{skills}\n\n---\n\n## Goal\n\n{goal}"


# ---- the hand-back: BLOCKED ------------------------------------------------

#: Anchored to the start of a line, case-sensitive on the keyword — prose like
#: "the run was blocked: on X" mid-sentence never matches. Last match wins.
_BLOCKED_LINE_RE = re.compile(r"^[ \t>#*_-]*(?:STATUS:[ \t]*)?BLOCKED:[ \t]*(.*?)[ \t*_]*$", re.MULTILINE)
#: ``BLOCKED: env — <item>``: the ENVIRONMENT lacks something (spec 032 US2).
_ENV_BLOCK_RE = re.compile(r"^(?:env|environment)\s*[—:–-]\s*(.+)$", re.IGNORECASE)
#: ``BLOCKED: <question> — options: <a> | <b> — default: <a>`` (spec 047 US1).
_DEFAULT_SEP_RE = re.compile(r"\s+[—–-]+\s*default:\s*", re.IGNORECASE)
_OPTIONS_SEP_RE = re.compile(r"\s+[—–-]+\s*options:\s*", re.IGNORECASE)
_OPTION_LABEL_RE = re.compile(r"^\(?([a-dA-D])[\).:]\s*")
_LETTER_RE = re.compile(r"^(?:option\s+)?\(?([a-dA-D])\)?\.?$", re.IGNORECASE)


def parse_blocked_reason(text: Optional[str]) -> Optional[str]:
    """The reason of the final ``BLOCKED:`` line, or None. An empty reason is
    still a block — an honest "I'm blocked" is never lost."""
    if not text:
        return None
    found = _BLOCKED_LINE_RE.findall(text)
    if not found:
        return None
    reason = found[-1].strip().strip("*_ ").strip()
    return reason or "worker reported BLOCKED without a stated reason"


def classify_block(reason: Optional[str]) -> tuple[str, str]:
    """``("env", item)`` for the typed environment form, else ``("contract", "")``."""
    m = _ENV_BLOCK_RE.match((reason or "").strip())
    if m:
        item = m.group(1).strip().strip("*_ ").strip()
        if item:
            return "env", item
    return "contract", ""


def parse_block_line(reason: Optional[str]) -> tuple[str, list[str], str, int]:
    """``(question, options, default, recommended)``. Options are kept only when
    two to four non-empty ones parse; ``recommended`` is the index the default
    names (letter, exact text or prefix), else -1. Never invents an option."""
    text = (reason or "").strip()
    head, default = text, ""
    seps = list(_DEFAULT_SEP_RE.finditer(text))
    if seps:
        head, default = text[: seps[-1].start()].strip(), text[seps[-1].end():].strip()
    question, options = head, []
    opt_sep = _OPTIONS_SEP_RE.search(head)
    if opt_sep:
        cleaned = [_OPTION_LABEL_RE.sub("", o.strip()).strip() for o in head[opt_sep.end():].split("|")]
        if 2 <= len(cleaned) <= 4 and all(cleaned):
            question, options = head[: opt_sep.start()].strip(), cleaned
    recommended = -1
    if options and default:
        lm = _LETTER_RE.match(default)
        if lm:
            idx = ord(lm.group(1).lower()) - ord("a")
            if idx < len(options):
                recommended, default = idx, options[idx]
        else:
            d = default.lower()
            for i, o in enumerate(options):
                ol = o.lower()
                if ol == d or ol.startswith(d) or d.startswith(ol):
                    recommended = i
                    break
    return question, options, default, recommended


def blocked_result(reason: str, *, workspace_dir: str, agent_output: str, usage: Optional[dict]) -> EngineResult:
    block_kind, block_item = classify_block(reason)
    question, options, default, recommended = parse_block_line(reason) if block_kind == "contract" else (reason, [], "", -1)
    return {
        "status": "blocked", "reason": reason, "block_kind": block_kind, "block_item": block_item,
        "question": question, "options": options, "default": default, "recommended": recommended,
        "workspace_dir": workspace_dir, "agent_output": agent_output, "usage": usage,
    }


# ---- the in-session verify loop --------------------------------------------

VERIFY_SCRIPT_REL = os.path.join(".devclaw", "verify")
WORKFLOW_MANIFEST_REL = os.path.join(".devclaw", "workflow.md")
#: Checked in the git INDEX, never the working tree: a `.gitignore` that
#: swallows `.devclaw/` leaves both files on disk and carries neither onto the
#: branch (ruled 2026-09-14).
TRACKED_MANIFEST_RELS = (VERIFY_SCRIPT_REL, WORKFLOW_MANIFEST_REL)
VERIFY_MISSING_PROMPT = (
    "Before you finish, `.devclaw/verify` must exist: an executable script that "
    "runs what this project's CI runs (derive it from `.github/workflows`; keep "
    "it honest — a check CI runs that the script skips is a red CI later). "
    "Create it, run it until green, commit it, and hand back again with your "
    "exit line."
)
VERIFY_FAILED_PROMPT = (
    "`{cmd}` failed (exit {code}) — this is what CI would see. Fix the cause, "
    "never the check; re-run it until green; commit; then hand back again with "
    "your exit line. Output tail:\n\n{tail}"
)
UNTRACKED_MANIFEST_PROMPT = (
    "`{path}` is not tracked on this branch — git does not carry it, so the "
    "next session on this repository starts blind. Create it if it is missing; "
    "if `.gitignore` ignores `.devclaw/`, un-ignore it (`.gitignore` is a "
    "product file, not a gate input). Then `git add` it, commit, and hand back "
    "again with your exit line."
)
#: ``bash -o pipefail``: a piped gate (``pytest | tail``) must not report the
#: last stage's status — that passed a red suite live (2026-08-20).
_BASH = shutil.which("bash")
_VERIFY_SHELL = [_BASH, "-o", "pipefail", "-c"] if _BASH else ["/bin/sh", "-c"]

#: Prefixed to the shell that runs a session or a verify command: it, and
#: everything it starts, is the OOM killer's preferred victim over the server
#: sharing its cgroup. Never lowers the score; silent on refusal.
_OOM_SHIELD = ('read -r _oom 2>/dev/null </proc/self/oom_score_adj; '
               '[ "${_oom:-0}" -lt 800 ] 2>/dev/null && echo 800 2>/dev/null >/proc/self/oom_score_adj; ')


def discover_verify(workspace_dir: str) -> Optional[str]:
    path = os.path.join(workspace_dir, VERIFY_SCRIPT_REL)
    if not os.path.isfile(path):
        return None
    return f"./{VERIFY_SCRIPT_REL}" if os.access(path, os.X_OK) else f"bash {VERIFY_SCRIPT_REL}"


async def run_verify(cmd: str, workspace_dir: str, timeout: float, *, docs_only: bool = False) -> dict:
    """Never raises — a crash or a timeout is a failed gate. ``docs_only`` sets
    :data:`DOCS_ONLY_ENV` for the script (see :func:`docs_only_diff`)."""
    env = session_env()
    if docs_only:
        env[DOCS_ONLY_ENV] = "1"
    try:
        proc = await asyncio.create_subprocess_exec(
            *_VERIFY_SHELL, _OOM_SHIELD + cmd, cwd=workspace_dir, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return {"ran": True, "cmd": cmd, "passed": False, "exit_code": None,
                "timed_out": False, "output": f"failed to run verify command: {exc}"}
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _kill(proc)
        await proc.wait()
        return {"ran": True, "cmd": cmd, "passed": False, "exit_code": None,
                "timed_out": True, "output": f"verify timed out after {timeout:.0f}s"}
    _kill(proc)
    text = (out or b"").decode("utf-8", "replace")
    return {"ran": True, "cmd": cmd, "passed": proc.returncode == 0,
            "exit_code": proc.returncode, "timed_out": False, "output": text[-4000:]}


def _has_git_dir(workspace_dir: str) -> bool:
    """A `.git` at or above the workspace, decided on the filesystem — git's
    exit code says 128 for "dubious ownership" exactly as for "no repo"."""
    path = os.path.abspath(workspace_dir)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


async def untracked_manifest(workspace_dir: str) -> Optional[str]:
    """The first manifest path git does not carry, or None. Fails CLOSED: an
    unreadable index is not proof the manifest landed."""
    if not _has_git_dir(workspace_dir):
        return None
    for rel in TRACKED_MANIFEST_RELS:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "ls-files", "--error-unmatch", "--", rel, cwd=workspace_dir,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await asyncio.wait_for(proc.wait(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            return TRACKED_MANIFEST_RELS[0]
        if rc != 0:
            return rel
    return None


# ---- the hand-off: no-mistakes ---------------------------------------------

NO_MISTAKES = "no-mistakes"
#: run outcomes that pushed the branch and opened or updated its PR
SHIPPED_OUTCOMES = frozenset({"passed", "checks-passed", "passed-with-override", "passed-with-skips"})
HANDOFF_PROMPT = (
    "Verify is green. Ship the branch through no-mistakes: it reviews, tests, "
    "pushes and opens or updates the pull request. Never `git push` yourself.\n\n"
    "1. Commit everything; `git status` must be clean.\n"
    "2. Write the contract (every issue section of your brief, verbatim) to `{intent_file}`.\n"
    "3. Run `no-mistakes axi run --intent-file {intent_file} --skip ci --wait 100s{closes}`. "
    "Never pass `--yes`. If it returns while the run is still going, run "
    "`no-mistakes axi run --wait 100s` again to reattach; never abort the run.\n"
    "4. Answer each gate with `no-mistakes axi respond`: `--action fix` a finding that needs a "
    "change, approve one that needs none, and approve one that only says a contract clause is "
    "not met yet when this session is DELIVERED. Answer a reviewer question with "
    "`no-mistakes axi answer` when the contract settles it. A finding or question only the "
    "owner can settle ends this session BLOCKED with it as the question.\n"
    "5. When the run reports its outcome, end your final message with your exit line again."
)
_STATUS_KEY_RE = re.compile(r'^( *)([A-Za-z_][A-Za-z0-9_]*)(?:\[\d+\](?:\{[^}]*\})?)?:[ \t]*(.*?)[ \t]*$')
#: The run's own fields in ``axi status`` (TOON, v1.99.0; it has no
#: machine-readable mode): its outcome at column 0, its PR and its head under
#: ``run:`` (always printed), the head it was submitted at under
#: ``branch_sync: > pipeline:`` (printed only while the run is pre-push). A step
#: row or another branch's ``other_branch_run:`` block never matches.
_STATUS_RUN_FIELDS = {
    ("outcome",): "outcome",
    ("run", "pr"): "pr",
    ("run", "head_sha"): "run_head",
    ("branch_sync", "pipeline", "submitted_head"): "submitted_head",
}


def host_env() -> dict[str, str]:
    """The env no-mistakes runs under: the host's minus the refused metered
    keys. Its daemon pushes and opens the PR, so — unlike a session — it keeps
    the GitHub credential."""
    return {**_credentials.strip_refused(os.environ), **git_identity_env()}


async def _host(workspace_dir: str, *argv: str, timeout: float = 120.0) -> tuple[int, str]:
    """One host-side command in the worktree. Never raises: ``-1`` on a spawn
    failure or a timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=workspace_dir, env=host_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return -1, f"failed to run {argv[0]}: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, f"{' '.join(argv[:3])} timed out after {timeout:.0f}s"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


async def unshipped_head(workspace_dir: str) -> Optional[str]:
    """HEAD when the branch carries a commit no ``origin`` ref has seen, else None
    — a fresh goal branch still at its base ships nothing."""
    if not _has_git_dir(workspace_dir):
        return None
    rc_b, branch = await _host(workspace_dir, "git", "rev-parse", "--abbrev-ref", "HEAD")
    rc, unseen = await _host(workspace_dir, "git", "rev-list", "-n1", "HEAD", "--not", "--remotes=origin")
    if rc != 0 or rc_b != 0 or branch == "HEAD" or not unseen:
        return None
    return (await _host(workspace_dir, "git", "rev-parse", "HEAD"))[1] or None


def parse_status(text: str) -> dict[str, str]:
    """The run's ``outcome`` / ``pr`` / ``run_head`` / ``submitted_head`` from ``axi status``,
    each read only at its own position in the document."""
    fields: dict[str, str] = {}
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = _STATUS_KEY_RE.match(line)
        if not m:
            continue
        indent, key, value = len(m.group(1)), m.group(2), m.group(3).strip('"')
        while stack and stack[-1][0] >= indent:
            stack.pop()
        name = _STATUS_RUN_FIELDS.get((*(k for _, k in stack), key))
        if name:
            fields.setdefault(name, value)
        stack.append((indent, key))
    return fields


async def dirty_tree(workspace_dir: str) -> bool:
    """True when the worktree holds uncommitted or untracked files. Fails CLOSED."""
    if not _has_git_dir(workspace_dir):
        return False
    rc, out = await _host(workspace_dir, "git", "status", "--porcelain")
    return rc != 0 or bool(out)


async def shipped_run(workspace_dir: str, head: str) -> tuple[Optional[str], str]:
    """``(pr_url, "")`` when the branch's newest no-mistakes run carries the
    unshipped ``head`` and passed, else ``(None, why)``. Carrying it proves the
    run is this session's: no earlier run was submitted at a descendant of a
    commit the remote has never seen. ``axi status`` prints the submitted head
    only while the run is pre-push; once it has pushed or finished, the run's own
    ``head_sha`` is the only head printed — a descendant of the submitted one,
    which lives in the gate repo until fetched."""
    rc, out = await _host(workspace_dir, NO_MISTAKES, "axi", "status")
    if rc != 0:
        return None, f"no-mistakes axi status failed: {out[-300:]}"
    fields = parse_status(out)
    submitted = fields.get("submitted_head", "")
    if not submitted and fields.get("run_head"):
        # a failed fetch leaves the object missing, so the ancestry test fails closed
        await _host(workspace_dir, "git", "fetch", NO_MISTAKES)
        submitted = fields["run_head"]
    if not submitted or (await _host(workspace_dir, "git", "merge-base", "--is-ancestor", head, submitted))[0] != 0:
        return None, f"no no-mistakes run carries {head[:8]}"
    if fields.get("outcome") not in SHIPPED_OUTCOMES:
        return None, f"the no-mistakes run ended {fields.get('outcome') or 'without an outcome'}"
    if not fields.get("pr"):
        return None, "the no-mistakes run passed but names no PR"
    return fields["pr"], ""


# ---- host facts: what this host cannot run, what the diff touches -----------

#: Set to ``1`` in the verify run's env when the branch's diff against its base
#: touches only documentation, so the repo's script may leave its browser e2e out.
DOCS_ONLY_ENV = "DEVCLAW_DOCS_ONLY"
_DOC_SUFFIXES = (".md", ".mdx", ".rst", ".adoc")
_DOC_DIRS = frozenset({"docs", "doc", "specs", ".devclaw"})
_DOC_NAMES = frozenset({"README", "CHANGELOG", "CONTRIBUTING", "LICENSE", "NOTICE", "COPYING"})
_BASE_REFS = ("origin/HEAD", "origin/main", "origin/master")
_CHROMIUM_GLOBS = (
    "chromium-*/chrome-linux*/chrome",
    "chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell",
    "chromium_headless_shell-*/chrome-linux*/headless_shell",
)
_LDD_MISSING_RE = re.compile(r"^\s*(\S+)\s+=>\s+not found\s*$", re.MULTILINE)


def is_doc_path(path: str) -> bool:
    """Documentation: prose in a doc format at the repo root, under a docs
    directory, or named like a project doc. A markdown file elsewhere may be
    read by the product (a prompt, a skill), so it is not."""
    parts = path.split("/")
    name = parts[-1]
    stem = name.split(".")[0].upper()
    if not (name.endswith(_DOC_SUFFIXES) or ("." not in name and stem in _DOC_NAMES)):
        return False
    return len(parts) == 1 or parts[0] in _DOC_DIRS or stem in _DOC_NAMES


async def docs_only_diff(workspace_dir: str) -> bool:
    """True when everything the branch changed against its base — commits,
    working-tree edits and new files — is documentation. Fails CLOSED: no base,
    no change or an unreadable diff is not docs-only."""
    if not _has_git_dir(workspace_dir):
        return False
    for ref in _BASE_REFS:
        rc, base = await _host(workspace_dir, "git", "merge-base", "HEAD", ref)
        if rc == 0 and base:
            break
    else:
        return False
    rc_d, changed = await _host(workspace_dir, "git", "diff", "--name-only", base)
    rc_n, new = await _host(workspace_dir, "git", "ls-files", "--others", "--exclude-standard")
    if rc_d != 0 or rc_n != 0:
        return False
    paths = [p for p in (*changed.splitlines(), *new.splitlines()) if p]
    return bool(paths) and all(is_doc_path(p) for p in paths)


def _wants_browser(workspace_dir: str) -> bool:
    if glob.glob(os.path.join(workspace_dir, "playwright.config.*")) \
            or glob.glob(os.path.join(workspace_dir, "*", "playwright.config.*")):
        return True
    return "playwright" in _read(os.path.join(workspace_dir, VERIFY_SCRIPT_REL)).lower()


def _chromium_binary() -> Optional[str]:
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), os.path.expanduser("~/.cache/ms-playwright")]
    for root in (r for r in roots if r and r != "0"):
        found = sorted(p for pattern in _CHROMIUM_GLOBS for p in glob.glob(os.path.join(root, pattern)))
        if found:
            return found[-1]
    return None


async def browser_preflight(workspace_dir: str) -> Optional[str]:
    """A one-paragraph fact for the session prompt when the repository drives a
    browser and the host's Chromium cannot load a shared library, else None. It
    costs one ``ldd`` — the same gap otherwise surfaces only when the e2e step
    runs, at the end of a paid session. Inconclusive (no browser installed, no
    ``ldd``) is silent: the session installs what is missing."""
    if not _wants_browser(workspace_dir):
        return None
    binary = _chromium_binary()
    if binary is None:
        return None
    rc, out = await _host(workspace_dir, "ldd", binary, timeout=30.0)
    missing = sorted(set(_LDD_MISSING_RE.findall(out))) if rc == 0 else []
    if not missing:
        return None
    return (f"Host preflight: the Chromium at `{binary}` cannot load {', '.join(missing)}, so the "
            f"browser e2e step cannot run on this host. `.devclaw/verify` runs with `{DOCS_ONLY_ENV}=1` "
            "when the branch's diff touches only documentation.")


# ---- one claude -p turn ----------------------------------------------------

def session_env() -> dict[str, str]:
    """The host env minus the refused metered keys (OAuth only) and the
    host-only credentials, plus devclaw's pinned git identity so the session's
    commits are authored by devclaw."""
    return {**_credentials.session_env(os.environ), **git_identity_env()}


def claude_argv(prompt: str, *, resume: Optional[str]) -> list[str]:
    argv = [_config.CLAUDE_BIN, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--dangerously-skip-permissions"]
    if _config.EXEC_MODEL:
        argv += ["--model", _config.EXEC_MODEL]
    if _config.plugin_dir():
        argv += ["--plugin-dir", _config.plugin_dir()]
    if resume:
        argv += ["--resume", resume]
    return argv


@dataclass
class Turn:
    """What one ``claude -p`` run left: the session, the final message, how it ended."""

    session_id: str = ""
    text: str = ""
    ok: bool = False
    error: str = ""
    usage: Optional[dict] = None
    frames: int = 0
    resume_missing: bool = False
    stderr: list[str] = field(default_factory=list)


def _kill(proc: asyncio.subprocess.Process) -> None:
    """The session and every process it started (it leads its own group)."""
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _event_of(frame: dict) -> Optional[EngineEvent]:
    kind = str(frame.get("type") or "frame")
    sub = frame.get("subtype")
    if kind == "system" and isinstance(sub, str) and sub.startswith(_QUIET_SYSTEM):
        return None
    return EngineEvent(id=frame.get("uuid"), type=f"{kind}/{sub}" if sub else kind,
                       source="claude", ts=int(time.time() * 1000), payload=frame)


async def run_turn(req: EngineRequest, prompt: str, *, resume: Optional[str]) -> Turn:
    """One ``claude -p`` run, read frame by frame under the silence watchdog."""
    turn = Turn(session_id=resume or "")
    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", _OOM_SHIELD + 'exec "$@"', "sh", *claude_argv(prompt, resume=resume),
            cwd=req.workspace_dir, env=session_env(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=STREAM_LINE_LIMIT, start_new_session=True,
        )
    except OSError as exc:
        turn.error = f"failed to spawn {_config.CLAUDE_BIN}: {exc}"
        return turn

    async def drain_stderr() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            turn.stderr.append(raw.decode("utf-8", "replace"))
            del turn.stderr[:-50]

    stderr_task = asyncio.ensure_future(drain_stderr())
    result: Optional[dict] = None
    assert proc.stdout is not None
    try:
        while True:
            bound = _config.SILENCE_S if turn.frames else _config.FIRST_FRAME_S
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=bound)
            except asyncio.TimeoutError:
                _kill(proc)
                turn.error = (f"stream silent for {bound:.0f}s — session killed" if turn.frames
                              else f"no first frame within {bound:.0f}s — session killed")
                break
            if not raw:
                break
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            turn.frames += 1
            sid = frame.get("session_id")
            if isinstance(sid, str) and sid and sid != turn.session_id and frame.get("type") != "result":
                turn.session_id = sid
                if req.on_session:
                    req.on_session(sid)
            if req.on_event:
                event = _event_of(frame)
                if event is not None:
                    req.on_event(event)
            if frame.get("type") == "result":
                result = frame
    finally:
        if proc.returncode is None and result is None and not turn.error:
            _kill(proc)
        await proc.wait()
        _kill(proc)
        await asyncio.gather(stderr_task, return_exceptions=True)

    if result is not None:
        usage = result.get("usage")
        turn.usage = usage if isinstance(usage, dict) else None
        turn.text = str(result.get("result") or "")
        errors = [str(e) for e in result.get("errors") or []]
        if result.get("is_error") or result.get("subtype") != "success":
            turn.error = "; ".join(errors) or turn.text or f"claude ended {result.get('subtype')}"
            turn.resume_missing = bool(resume) and any("No conversation found" in e for e in errors)
        else:
            turn.ok = True
    elif not turn.error:
        tail = "".join(turn.stderr)[-1024:]
        turn.error = f"{_config.CLAUDE_BIN} exited {proc.returncode} without a result frame. stderr tail:\n{tail}"
    return turn


_USAGE_KEYS = (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"),
               ("cache_read_tokens", "cache_read_input_tokens"),
               ("cache_creation_tokens", "cache_creation_input_tokens"))


def _add_usage(total: Optional[dict], usage: Optional[dict]) -> Optional[dict]:
    """Sum the result frames' usage blocks into the store's four columns."""
    if not usage:
        return total
    out = dict(total or {k: 0 for k, _ in _USAGE_KEYS}, source="result")
    for col, key in _USAGE_KEYS:
        v = usage.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[col] += int(v)
    return out


# ---- the engine ------------------------------------------------------------

async def run_claude(req: EngineRequest) -> EngineResult:
    """Run one task: a session, then (code kinds) the verify loop on the same session."""
    try:
        prompt = wrap_goal(req.kind, req.goal, req.workspace_dir)
    except RuntimeError as exc:
        return {"status": "error", "error": f"skills_missing: {exc}"}
    # The pipeline exists BEFORE the session starts: a session that probes
    # `no-mistakes` and finds no gate reads the hand-off as impossible and ends
    # BLOCKED. A pipeline that cannot start spends no session.
    if req.hand_off and req.kind in WRITES_CODE_KINDS and _has_git_dir(req.workspace_dir):
        error = await _init_pipeline(req.workspace_dir)
        if error:
            return {"status": "error", "error": error}
    # a resumed session already holds the skills; it is handed only the new brief
    resume = req.resume_session_id
    if not resume and req.kind in WRITES_CODE_KINDS:
        # a host gap the session would otherwise find at the end of its work
        note = await browser_preflight(req.workspace_dir)
        if note:
            _emit(req, "preflight", "Preflight", {"note": note})
            prompt = f"{note}\n\n{prompt}"
    turn = await run_turn(req, req.goal if resume else prompt, resume=resume)
    if turn.resume_missing:
        sys.stderr.write(f"claude-engine: session {req.resume_session_id} not found — starting fresh\n")
        turn = await run_turn(req, prompt, resume=None)
    usage = _add_usage(None, turn.usage)

    def failed(t: Turn) -> EngineResult:
        return {"status": "error", "error": t.error, "agent_output": t.text, "usage": usage,
                "session_id": t.session_id}

    if not turn.ok:
        return failed(turn)
    verify: Optional[dict] = None
    if req.kind in WRITES_CODE_KINDS:
        asked = False
        feedback: Optional[str]
        for _ in range(max(1, _config.VERIFY_ROUNDS)):
            if parse_blocked_reason(turn.text) is not None:
                break
            cmd = req.verify_cmd or discover_verify(req.workspace_dir)
            if not cmd:
                if asked:
                    break
                asked = True
                feedback = VERIFY_MISSING_PROMPT
            else:
                verify, feedback = await _gate(req, cmd)
                if feedback is None:
                    break
            turn = await run_turn(req, feedback, resume=turn.session_id)
            usage = _add_usage(usage, turn.usage)
            if not turn.ok:
                return failed(turn)
        else:
            if parse_blocked_reason(turn.text) is None:
                cmd = req.verify_cmd or discover_verify(req.workspace_dir)
                verify = (await _gate(req, cmd))[0] if cmd else None
        if verify is None and parse_blocked_reason(turn.text) is None:
            verify = {"ran": True, "cmd": VERIFY_SCRIPT_REL, "passed": False, "exit_code": None,
                      "timed_out": False,
                      "output": "no .devclaw/verify script after the session was asked to create one"}
    reason = parse_blocked_reason(turn.text)
    pr_url: Optional[str] = None
    if reason is None and req.hand_off and verify is not None and verify.get("passed"):
        head = await unshipped_head(req.workspace_dir)
        if head:
            turn, pr_url, why = await _hand_off(req, turn.session_id, head)
            usage = _add_usage(usage, turn.usage)
            if not turn.ok:
                return failed(turn)
            reason = parse_blocked_reason(turn.text)
            if reason is None and pr_url is None:
                return {"status": "error", "error": f"the hand-off did not ship: {why}",
                        "agent_output": turn.text, "usage": usage, "session_id": turn.session_id}
        elif await dirty_tree(req.workspace_dir):
            return {"status": "error", "error": "the hand-off did not ship: the session left uncommitted "
                    "changes and no commit", "agent_output": turn.text, "usage": usage,
                    "session_id": turn.session_id}
    if reason is not None:
        out = blocked_result(reason, workspace_dir=req.workspace_dir, agent_output=turn.text, usage=usage)
        out["session_id"] = turn.session_id
        return out
    result: EngineResult = {"status": "ok", "workspaceDir": req.workspace_dir, "message": "session complete",
                            "agent_output": turn.text, "usage": usage, "session_id": turn.session_id}
    if verify is not None:
        result["verify"] = verify
    if pr_url:
        result["pr_url"] = pr_url
    return result


async def _init_pipeline(workspace_dir: str) -> Optional[str]:
    """``no-mistakes init`` on the host (idempotent: it repairs the gate remote
    and starts the daemon under :func:`host_env`, which keeps the GitHub
    credential). The failure text, or None."""
    rc, out = await _host(workspace_dir, NO_MISTAKES, "init")
    return None if rc == 0 else f"no-mistakes init failed: {out[-500:]}"


async def _hand_off(req: EngineRequest, session_id: str, head: str) -> tuple[Turn, Optional[str], str]:
    """The ship turn: re-init the pipeline (the daemon may have died during the
    session, and only the host env restarts it with the GitHub credential), then
    the SAME session drives the run.
    ``(turn, pr_url, why)`` — ``pr_url`` only when :func:`shipped_run` proves it."""
    error = await _init_pipeline(req.workspace_dir)
    if error:
        return Turn(session_id=session_id, error=error), None, ""
    closes = "".join(f" --closes {n}" for n in req.closes)
    with tempfile.TemporaryDirectory(prefix="devclaw-intent-") as tmp:
        prompt = HANDOFF_PROMPT.format(intent_file=os.path.join(tmp, "intent.md"), closes=closes)
        turn = await run_turn(req, prompt, resume=session_id)
    if not turn.ok or parse_blocked_reason(turn.text) is not None:
        return turn, None, ""
    pr_url, why = await shipped_run(req.workspace_dir, head)
    return turn, pr_url, why


async def _gate(req: EngineRequest, cmd: str) -> tuple[dict, Optional[str]]:
    """The end-of-session verdict plus the feedback to hand back when red: the
    project's checks run green AND the first-run manifest is TRACKED."""
    verify = await run_verify(cmd, req.workspace_dir, _config.VERIFY_TIMEOUT_S,
                              docs_only=await docs_only_diff(req.workspace_dir))
    _emit_verify(req, verify)
    if not verify["passed"]:
        return verify, VERIFY_FAILED_PROMPT.format(cmd=cmd, code=verify["exit_code"],
                                                   tail=(verify["output"] or "")[-3000:])
    untracked = await untracked_manifest(req.workspace_dir)
    if untracked is None:
        return verify, None
    refusal = {"ran": True, "cmd": f"git ls-files --error-unmatch {untracked}", "passed": False,
               "exit_code": None, "timed_out": False,
               "output": f"`{untracked}` is not tracked on this branch — the manifest never reaches the next session"}
    _emit_verify(req, refusal)
    return refusal, UNTRACKED_MANIFEST_PROMPT.format(path=untracked)


def _emit(req: EngineRequest, id: str, type: str, payload: dict) -> None:
    if req.on_event:
        req.on_event(EngineEvent(id=id, type=type, source="devclaw", ts=int(time.time() * 1000),
                                 payload=payload))


def _emit_verify(req: EngineRequest, verify: dict) -> None:
    _emit(req, "verify", "VerifyResult", {k: verify[k] for k in ("cmd", "passed", "exit_code", "timed_out")})
