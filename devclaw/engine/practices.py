"""Three practices on the code path (#1053, spec 048): task size, test-first,
one mutation per critical criterion.

- **Size** S / M / L is set once at goal start from the issue by the rules in
  :func:`size_of` (written out in ``docs/flows/task-execution.md``). S skips the
  optional stages — the test pass, the mutation check and the workflow's own
  optional planning stages; L skips none.
- **Test-first.** On a goal whose contract has ``## Done when`` criteria, a
  fresh test-writer session writes tests from them before any code exists;
  ``.devclaw/verify`` must then be RED. The engine commits that file set with a
  :data:`TEST_PASS_TRAILER` trailer — git is the record, no host state — and
  every later code pass on the branch is refused when it edits, deletes or
  rewrites one of those files. New test files are always allowed.
- **One mutation.** For each criterion marked critical, the session names one
  condition to break; the engine breaks it in a scratch copy of the tree and
  requires ``.devclaw/verify`` to go red. A mutation every test survives fails
  the gate with the criterion named.

The model chooses (what to test, which condition to break); this module only
checks, mechanically. No goal-layer state: the size is the goal row's one
identity column, the contract is read from the brief the goal layer rendered
from the live issue, and the test-pass file set lives in the branch's history.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Optional

from ..git_identity import git_identity_env

SIZES = ("S", "M", "L")
TEST_PASS_TRAILER = "Devclaw-Test-Pass"
TEST_FILE_TRAILER = "Devclaw-Test-File"

# ---- size: set once at goal start, from the issue ---------------------------

#: L: money, auth, data migrations, breaking API changes — read in the title and
#: the contract section (the whole body when it has none)
_L_RE = re.compile(
    r"\b(money|payments?|billing|invoices?|refunds?|pricing|prices?|auth|authn|authz|authenticat\w*|"
    r"authori[sz]\w*|login|passwords?|credentials?|oauth|permissions?|migrations?|migrate|"
    r"breaking)\b", re.IGNORECASE)
_DECLARED_RE = re.compile(r"^[ \t>*_-]*size[*_]*[ \t]*:[ \t*_]*([SML])\b", re.IGNORECASE | re.MULTILINE)
_NO_BEHAVIOUR_RE = re.compile(r"\bno behaviou?r change\b", re.IGNORECASE)
_CODE_SPAN_RE = re.compile(r"`([^`\s]+)`")
_CONTRACT_HEAD_RE = re.compile(r"^(#{2,4})[ \t]*(?:Done when|Acceptance)\b.*$", re.IGNORECASE | re.MULTILINE)


def _section(text: str, m: "re.Match[str]") -> str:
    tail = text[m.end():]
    stop = re.search(rf"^(?:#{{1,{len(m.group(1))}}}\s|=====)", tail, re.MULTILINE)
    return tail[: stop.start()] if stop else tail


def _named_files(text: str) -> set[str]:
    return {s.removeprefix("./") for s in _CODE_SPAN_RE.findall(text)
            if ("/" in s or re.search(r"\.[A-Za-z]\w*$", s)) and "://" not in s}


def size_of(issues: Iterable[tuple[str, str]]) -> str:
    """S, M or L from ``(title, body)`` pairs. L wins over everything; an issue
    may declare ``Size: S|M|L`` (never below L); S otherwise needs one issue
    naming exactly one file and saying "no behaviour change"; else M."""
    issues = list(issues)
    declared: set[str] = set()
    large = False
    for title, body in issues:
        heads = list(_CONTRACT_HEAD_RE.finditer(body or ""))
        contract = "\n".join(_section(body, m) for m in heads) if heads else (body or "")
        large = large or bool(_L_RE.search(f"{title}\n{contract}"))
        declared |= {d.upper() for d in _DECLARED_RE.findall(body or "")}
    if large or "L" in declared:
        return "L"
    if "M" in declared or not issues:
        return "M"
    if "S" in declared:
        return "S"
    if len(issues) == 1:
        title, body = issues[0]
        text = f"{title}\n{body or ''}"
        if _NO_BEHAVIOUR_RE.search(text) and len(_named_files(text)) == 1:
            return "S"
    return "M"


# ---- the contract, as the goal layer rendered it into the brief --------------

_ITEM_RE = re.compile(r"^[ ]?(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?(.+?)\s*$")
_CRITICAL_RE = re.compile(r"\((?:critical)\)|\[critical\]|\*\*critical\*\*|^critical\s*:", re.IGNORECASE)


_ISSUE_FENCE_RE = re.compile(r"^=+ BEGIN UNTRUSTED ISSUE \d+\b.*?$(.*?)^=+ END UNTRUSTED ISSUE \d+", re.M | re.S)


def contract_criteria(brief: str) -> list[tuple[str, bool]]:
    """``(criterion, critical)`` for each top-level item of every ``## Done when``
    / ``## Acceptance`` section of the brief's ISSUE fences (the whole brief
    when it has none — a comment quoting a contract is not the contract).
    Critical = tagged ``(critical)``, ``[critical]``, ``**critical**`` or ``critical:``."""
    out: list[tuple[str, bool]] = []
    texts = _ISSUE_FENCE_RE.findall(brief or "") or [brief or ""]
    for text, m in ((t, m) for t in texts for m in _CONTRACT_HEAD_RE.finditer(t)):
        for line in _section(text, m).splitlines():
            item = _ITEM_RE.match(line)
            if item and (text := item.group(1).strip()) and text not in (c for c, _ in out):
                out.append((text, bool(_CRITICAL_RE.search(text))))
    return out


# ---- git, on the host ---------------------------------------------------------

async def _git(cwd: str, *argv: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *argv, cwd=cwd, env={**os.environ, **git_identity_env()},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except (OSError, asyncio.TimeoutError) as exc:
        return -1, str(exc)
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


async def _branch(workspace_dir: str) -> str:
    rc, out = await _git(workspace_dir, "rev-parse", "--abbrev-ref", "HEAD")
    return out if rc == 0 and out else "HEAD"


async def recorded_test_pass(workspace_dir: str) -> Optional[tuple[str, tuple[str, ...]]]:
    """``(commit, files)`` of the newest test-pass commit THIS branch recorded,
    or None. The trailer names the branch: a squash-merge copies an earlier
    goal's trailer onto the default branch, and that one is not ours."""
    mark = f"{TEST_PASS_TRAILER}: {await _branch(workspace_dir)} "
    rc, sha = await _git(workspace_dir, "log", "-n1", "-F", f"--grep={mark}", "--format=%H", "HEAD")
    if rc != 0 or not sha:
        return None
    rc, body = await _git(workspace_dir, "log", "-n1", "--format=%B", sha)
    files = tuple(line.split(":", 1)[1].strip() for line in body.splitlines()
                  if line.startswith(f"{TEST_FILE_TRAILER}:"))
    return sha, files


async def changed_since(workspace_dir: str, base: str) -> Optional[list[str]]:
    """Every path changed since ``base`` — commits, staged, unstaged, untracked —
    except the manifest under ``.devclaw/``. None when git cannot say."""
    rc_a, _ = await _git(workspace_dir, "add", "-A")
    rc, out = await _git(workspace_dir, "diff", "--cached", "--name-only", base)
    if rc_a != 0 or rc != 0:
        return None
    return [p for p in out.splitlines() if p and not p.startswith(".devclaw/")]


async def commit_test_pass(workspace_dir: str, files: list[str], verify: dict) -> Optional[str]:
    """Commit the test pass with its record; the commit sha, or None."""
    branch = await _branch(workspace_dir)
    trailer = "\n".join([f"{TEST_PASS_TRAILER}: {branch} red before code (verify exit {verify.get('exit_code')})",
                         *(f"{TEST_FILE_TRAILER}: {f}" for f in files)])
    msg = f"test: tests from the contract, red before any code (devclaw test pass)\n\n{trailer}\n"
    rc, _ = await _git(workspace_dir, "commit", "-q", "--allow-empty", "--no-verify", "-m", msg)
    if rc != 0:
        return None
    rc, sha = await _git(workspace_dir, "rev-parse", "HEAD")
    return sha if rc == 0 and sha else None


async def frozen_violations(workspace_dir: str, commit: str, files: "tuple[str, ...]") -> list[str]:
    """How the code pass touched the test-pass files: one line per file it
    edited or deleted, or the record itself rewritten away. Fails CLOSED."""
    rc, _ = await _git(workspace_dir, "merge-base", "--is-ancestor", commit, "HEAD")
    if rc != 0:
        return [f"the test-pass commit {commit[:10]} is no longer on the branch — its tests were rewritten away"]
    if not files:
        return []
    rc, out = await _git(workspace_dir, "diff", "--name-status", "--no-renames", commit, "--", *files)
    if rc != 0:
        return [f"git could not diff the test-pass files against {commit[:10]}: {out[-200:]}"]
    verbs = {"D": "deleted"}
    return [f"{verbs.get(s[:1], 'edited')} `{p}`" for s, _, p in (line.partition("\t") for line in out.splitlines()) if p]


# ---- the one-mutation check ---------------------------------------------------

@dataclass(frozen=True)
class Mutation:
    criterion: int
    file: str
    find: str
    replace: str


def parse_mutations(text: str) -> list[Mutation]:
    """The LAST JSON object in ``text`` carrying a ``mutations`` list."""
    decoder, pos, found = json.JSONDecoder(), 0, None
    while (m := re.compile(r"\{").search(text or "", pos)):
        try:
            value, pos = decoder.raw_decode(text, m.start())
        except ValueError:
            pos = m.start() + 1
            continue
        if isinstance(value, dict) and isinstance(value.get("mutations"), list):
            found = value["mutations"]
    out = []
    for item in found or []:
        if isinstance(item, dict) and isinstance(item.get("criterion"), int):
            out.append(Mutation(item["criterion"], str(item.get("file") or ""), str(item.get("find") or ""),
                                str(item.get("replace") or "")))
    return out


def _invalid(m: Mutation, tree: str, frozen: "tuple[str, ...]") -> str:
    path = os.path.normpath(m.file)
    if not m.file or os.path.isabs(m.file) or path.startswith("..") or path.startswith(".devclaw"):
        return f"`{m.file}` is not a file of the code under test"
    if path in {os.path.normpath(f) for f in frozen}:
        return f"`{m.file}` is a test-pass file — mutate the code under test, not its tests"
    if not m.find or "\n" in m.find or "\n" in m.replace or m.find == m.replace:
        return "a mutation changes exactly one line: `find` and `replace` are different single lines"
    try:
        text = open(os.path.join(tree, path), encoding="utf-8").read()
    except OSError:
        return f"`{m.file}` does not exist"
    if text.count(m.find) != 1:
        return f"`find` occurs {text.count(m.find)} times in `{m.file}`, not once"
    return ""


async def mutation_failures(workspace_dir: str, cmd: str, critical: list[str], mutations: list[Mutation],
                            frozen: "tuple[str, ...]", run: Callable[[str, str], Awaitable[dict]]) -> list[str]:
    """One line per critical criterion the check refuses (no mutation, an invalid
    one, or one every test survives), ``[]`` when each mutation turned verify
    red. Runs in a scratch copy; the real tree is never touched."""
    with tempfile.TemporaryDirectory(prefix="devclaw-mutation-") as tmp:
        tree = os.path.join(tmp, "tree")
        try:
            await asyncio.to_thread(shutil.copytree, workspace_dir, tree, symlinks=True,
                                    ignore=shutil.ignore_patterns(".git"))
        except (OSError, shutil.Error) as exc:
            return [f"the tree could not be copied for the mutation check: {exc}"]
        await _git(tree, "init", "-q")
        base = await run(cmd, tree)
        if not base.get("passed"):
            return [f"`{cmd}` is red on an unmutated scratch copy, so no mutation can be judged: "
                    f"{(base.get('output') or '')[-300:]}"]
        out = []
        for n, criterion in enumerate(critical, 1):
            label = f"critical criterion {n} ({criterion[:120]})"
            m = next((x for x in mutations if x.criterion == n), None)
            why = "no mutation proposed" if m is None else _invalid(m, tree, frozen)
            if m is None or why:
                out.append(f"{label}: {why}")
                continue
            path = os.path.join(tree, os.path.normpath(m.file))
            original = open(path, encoding="utf-8").read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original.replace(m.find, m.replace, 1))
            try:
                result = await run(cmd, tree)
            finally:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(original)
            if result.get("passed"):
                out.append(f"{label}: breaking `{m.find.strip()[:80]}` in `{m.file}` left every test green")
        return out


# ---- the plan one code session runs under -------------------------------------

@dataclass
class Plan:
    size: str = "M"
    criteria: list[tuple[str, bool]] = field(default_factory=list)
    #: ``(commit, files)`` once the branch carries a test pass
    test_pass: Optional[tuple[str, tuple[str, ...]]] = None

    @property
    def critical(self) -> list[str]:
        return [] if self.size == "S" else [c for c, crit in self.criteria if crit]

    @property
    def wants_test_pass(self) -> bool:
        return self.size != "S" and bool(self.criteria) and self.test_pass is None

    def note(self) -> str:
        lines = [SIZE_NOTES.get(self.size, SIZE_NOTES["M"])]
        if self.test_pass and self.test_pass[1]:
            lines.append(FROZEN_NOTE.format(commit=self.test_pass[0][:10],
                                            files=", ".join(f"`{f}`" for f in self.test_pass[1])))
        if self.critical:
            lines.append(MUTATION_NOTE.format(criteria="\n".join(f"{n}. {c}" for n, c in enumerate(self.critical, 1))))
        return "\n\n".join(lines)


async def plan_for(brief: str, size: str, workspace_dir: str) -> Plan:
    return Plan(size=size if size in SIZES else "M", criteria=contract_criteria(brief),
                test_pass=await recorded_test_pass(workspace_dir))


SIZE_NOTES = {
    "S": "Task size: S (one file, no behaviour change). Skip the optional stages `.devclaw/workflow.md` "
         "names (a spec, a plan, a design review): make the change, verify, ship.",
    "M": "Task size: M. Follow `.devclaw/workflow.md`; whether to run its optional stages is your call.",
    "L": "Task size: L (money, auth, a data migration or a breaking API change). Run every stage "
         "`.devclaw/workflow.md` names — skip none.",
}
TEST_PASS_PROMPT = (
    "TEST PASS — you are the test writer, not the engineer. This turn replaces the numbered steps and "
    "the exit lines in the brief below. Write tests, in the repository's own test layout and runner, "
    "that pin each criterion of the contract's `## Done when` section. Write NO production code: the "
    "tests must fail now, because the change does not exist yet. If `.devclaw/verify` does not exist, "
    "derive it from the project's CI (it runs what CI runs) and include it. Run it and confirm your new "
    "tests fail for the right reason. Do not commit, push or ship — the host records your files and "
    "proves they fail. End your final message with `TESTS: <what the tests pin>`, or with "
    "`BLOCKED: <question>` when the criteria cannot be tested."
)
TEST_VERIFY_MISSING_PROMPT = (
    "`.devclaw/verify` does not exist, so nothing can prove your tests fail. Create it: an executable "
    "script that runs what this project's CI runs (derive it from `.github/workflows`). Run it, do not "
    "commit, and hand back with your `TESTS:` line."
)
FROZEN_NOTE = (
    "The test pass (commit {commit}) wrote these tests from the contract, and they fail until the code "
    "exists: {files}. Write the code until they pass. Do not edit, delete or skip those files — the gate "
    "refuses a diff that touches them. A test you add goes in a new file."
)
MUTATION_NOTE = (
    "Critical criteria — the host breaks one condition of the code under test for each, in a scratch "
    "copy, and `.devclaw/verify` must go red:\n{criteria}\n\nBefore your exit line, end with a ```json "
    "block: {{\"mutations\": [{{\"criterion\": <n>, \"file\": \"<code file, not a test>\", \"find\": "
    "\"<one exact line holding the condition>\", \"replace\": \"<that line with the condition broken>\"}}]}} "
    "— one per critical criterion."
)
MUTATION_FAILED_PROMPT = (
    "The one-mutation check refused this branch:\n{failures}\n\nA test must fail when the condition a "
    "critical criterion depends on breaks. Strengthen the tests (in a new file — the test-pass files are "
    "frozen), re-run `.devclaw/verify`, commit, and hand back with a fresh ```json mutations block and "
    "your exit line."
)
FROZEN_FAILED_PROMPT = (
    "The code pass touched the test-pass files ({commit}): {violations}. Restore them with "
    "`git checkout {commit} -- <file>`, make the code pass the tests as written (a new test goes in a "
    "new file), re-run `.devclaw/verify`, commit, and hand back with your exit line."
)

__all__ = ["SIZES", "Plan", "Mutation", "size_of", "contract_criteria", "plan_for", "recorded_test_pass",
           "changed_since", "commit_test_pass", "frozen_violations", "parse_mutations", "mutation_failures"]
