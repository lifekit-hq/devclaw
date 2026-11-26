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
  rewrites one of the files named like tests or the runner config that decides
  which tests run (a stub or a manifest the writer needed is not frozen; runner
  config the goal's issue names is in scope). New test files are always
  allowed. A contract change re-runs the pass.
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
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Optional

from ..git_identity import git_identity_env

SIZES = ("S", "M", "L")
TEST_PASS_TRAILER = "Devclaw-Test-Pass"
TEST_FILE_TRAILER = "Devclaw-Test-File"

# ---- size: set once at goal start, from the issue ---------------------------

#: L: money, auth, data migrations, breaking API changes — read in the title and
#: the contract section (the whole body when it has none); "no / not / without /
#: non-breaking" is not breaking, a file permission is not auth
_L_RE = re.compile(
    r"\b(money|payments?|billing|invoices?|refunds?|pricing|prices?|auth|authn|authz|authenticat\w*|"
    r"authori[sz]\w*|login|passwords?|credentials?|oauth|(?:user|role|access|admin)[ -]permissions?|"
    r"(?:data(?:base)?|db|schema)[ -]migrations?|migrat\w*[ \t]+(?:the[ \t]+|a[ \t]+)?(?:data(?:base)?|db|schema)|"
    r"(?<!non-)(?<!\bno )(?<!\bnot )(?<!\bnot a )(?<!\bwithout )breaking)\b", re.IGNORECASE)
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
    """S, M or L from ``(title, body)`` pairs. L wins over everything; S needs
    one issue naming exactly one file and saying "no behaviour change"; else M."""
    issues = list(issues)
    for title, body in issues:
        heads = list(_CONTRACT_HEAD_RE.finditer(body or ""))
        contract = "\n".join(_section(body, m) for m in heads) if heads else (body or "")
        if _L_RE.search(f"{title}\n{contract}"):
            return "L"
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
_ISSUE_NUMBER_RE = re.compile(r"^=+ BEGIN UNTRUSTED ISSUE (\d+)\b", re.M)


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

BASE_REFS = ("origin/HEAD", "origin/main", "origin/master")
CONTRACT_TRAILER = "Devclaw-Contract"
#: what decides which tests run and are collected — frozen with the test pass
_RUNNER_CONFIG_RE = re.compile(r"(?:^|/)(?:conftest\.py|pytest\.ini|(?:jest|vitest)\.config\.[\w.]+)$|^\.devclaw/verify$")
#: a file named like a test — frozen with the test pass, never a mutation target
_TEST_NAME_RE = re.compile(r"(?:^|/)tests?/|(?:^|/)test_[^/]*$|_test\.[^/]*$|\.(?:test|spec)\.[^/]*$")


def frozen_name(path: str) -> bool:
    """What a test pass freezes: a file named like a test, or runner config."""
    return bool(_TEST_NAME_RE.search(path) or _RUNNER_CONFIG_RE.search(path))


def issue_text(brief: str) -> str:
    """The text of the brief's ISSUE fences — what the goal names in scope."""
    return "\n".join(_ISSUE_FENCE_RE.findall(brief or ""))


def _in_scope(path: str, scope: str) -> bool:
    """Runner config the goal's issue names (its path, or a config file's own name)."""
    return bool(_RUNNER_CONFIG_RE.search(path)) and (
        path in scope or (path != ".devclaw/verify" and os.path.basename(path) in scope))


def contract_hash(criteria: list[tuple[str, bool]]) -> str:
    return hashlib.sha256("\n".join(c for c, _ in criteria).encode()).hexdigest()[:16]


async def _git(cwd: str, *argv: str, env: Optional[dict] = None) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *argv, cwd=cwd, env={**os.environ, **git_identity_env(), **(env or {})},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except (OSError, asyncio.TimeoutError) as exc:
        return -1, str(exc)
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


async def _branch(workspace_dir: str) -> str:
    rc, out = await _git(workspace_dir, "rev-parse", "--abbrev-ref", "HEAD")
    return out if rc == 0 and out else "HEAD"


async def merge_base(workspace_dir: str, head: str = "HEAD") -> Optional[str]:
    """The newest commit of the default branch ``head`` holds, or None."""
    for ref in BASE_REFS:
        rc, base = await _git(workspace_dir, "merge-base", head, ref)
        if rc == 0 and base:
            return base
    return None


async def fresh_branch(workspace_dir: str) -> bool:
    """True when the branch carries no commit beyond its merge-base with the
    default branch but the host's ``wip(devclaw):`` snapshots of an
    interrupted session — no code exists yet for a test to fail against."""
    base = await merge_base(workspace_dir)
    if base is None:
        return False
    rc, count = await _git(workspace_dir, "rev-list", "--count", "--invert-grep", "--grep=^wip(devclaw): ",
                           f"{base}..HEAD")
    return rc == 0 and count == "0"


@dataclass(frozen=True)
class Record:
    """A branch's test pass: its commit, the frozen files, the contract hash."""
    commit: str
    files: "tuple[str, ...]"
    contract: str


async def recorded_test_pass(workspace_dir: str, scope: str = "") -> Optional[Record]:
    """The newest test-pass record THIS branch committed, or None. The trailer
    names the branch: a squash-merge copies an earlier goal's trailer onto the
    default branch, and that one is not ours. The frozen files are the listed
    tests plus the runner config present at that commit, less the runner
    config the goal's issue text ``scope`` names."""
    mark = f"{TEST_PASS_TRAILER}: {await _branch(workspace_dir)} "
    rc, sha = await _git(workspace_dir, "log", "-n1", "-F", f"--grep={mark}", "--format=%H", "HEAD")
    if rc != 0 or not sha:
        return None
    _, body = await _git(workspace_dir, "log", "-n1", "--format=%B", sha)
    trailers = [line.split(":", 1) for line in body.splitlines() if ":" in line]
    files = [v.strip() for k, v in trailers if k == TEST_FILE_TRAILER]
    contract = next((v.strip() for k, v in trailers if k == CONTRACT_TRAILER), "")
    _, tree = await _git(workspace_dir, "ls-tree", "-r", "--name-only", sha)
    files += [p for p in tree.splitlines() if _RUNNER_CONFIG_RE.search(p) and p not in files]
    return Record(sha, tuple(f for f in files if not _in_scope(f, scope)), contract)


async def changed_since(workspace_dir: str, base: str) -> Optional[list[str]]:
    """Every path changed since ``base`` — commits, staged, unstaged, untracked —
    except the manifest under ``.devclaw/``. None when git cannot say."""
    rc_a, _ = await _git(workspace_dir, "add", "-A")
    rc, out = await _git(workspace_dir, "diff", "--cached", "--name-only", base)
    if rc_a != 0 or rc != 0:
        return None
    return [p for p in out.splitlines() if p and not p.startswith(".devclaw/")]


async def commit_test_pass(workspace_dir: str, files: list[str], verify: dict, contract: str) -> bool:
    """Commit the test pass with its record; False when git refused."""
    branch = await _branch(workspace_dir)
    trailer = "\n".join([f"{TEST_PASS_TRAILER}: {branch} verify exit {verify.get('exit_code')}",
                         f"{CONTRACT_TRAILER}: {contract}", *(f"{TEST_FILE_TRAILER}: {f}" for f in files)])
    msg = f"test: tests from the contract (devclaw test pass)\n\n{trailer}\n"
    rc, _ = await _git(workspace_dir, "commit", "-q", "--allow-empty", "--no-verify", "-m", msg)
    return rc == 0


async def frozen_violations(workspace_dir: str, commit: str, files: "tuple[str, ...]",
                            head: Optional[str] = None) -> tuple[list[str], list[str]]:
    """``(violations, accepted)``: one line per test-pass file the code pass
    edited or deleted, or the record itself rewritten away — in the working
    tree, or at the commit ``head`` when given. A change the default branch
    made, merged in, is not the code pass's; a file the default branch and the
    test pass changed in conflict is ``accepted`` in whatever resolution the
    branch holds. Fails CLOSED."""
    rc, _ = await _git(workspace_dir, "merge-base", "--is-ancestor", commit, head or "HEAD")
    if rc != 0:
        return [f"the test-pass commit {commit[:10]} is no longer on the branch — its tests were rewritten away"], []
    expected, accepted = commit, list[str]()
    base = await merge_base(workspace_dir, head or "HEAD")
    if files and base is not None and (await _git(workspace_dir, "merge-base", "--is-ancestor", base, commit))[0] != 0:
        rc, out = await _git(workspace_dir, "merge-tree", "--write-tree", "--name-only", "--no-messages", commit, base)
        if rc not in (0, 1) or not out:
            return [f"git could not merge the default branch into the test-pass files: {out[-200:]}"], []
        expected, *conflicted = out.splitlines()
        accepted = [f for f in files if f in conflicted]
    paths = [f for f in files if f not in accepted]
    if not paths:
        return [], accepted
    rc, out = await _git(workspace_dir, "diff", "--name-status", "--no-renames", expected, *([head] if head else []),
                         "--", *paths)
    if rc != 0:
        return [f"git could not diff the test-pass files against {commit[:10]}: {out[-200:]}"], accepted
    verbs = {"D": "deleted"}
    return [f"{verbs.get(s[:1], 'edited')} `{p}`" for s, _, p in (line.partition("\t") for line in out.splitlines())
            if p], accepted


def accepted_note(accepted: list[str]) -> list[str]:
    """The thread note for frozen files a merge of the default branch conflicted in."""
    return [f"the test-pass files {', '.join(f'`{f}`' for f in accepted)} conflicted with the default branch; "
            "the frozen-file check accepted the branch's resolution of them unchecked"] if accepted else []


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
    if path in {os.path.normpath(f) for f in frozen} or _TEST_NAME_RE.search(path):
        return f"`{m.file}` is a test file — mutate the code under test, not its tests"
    if not m.find or "\n" in m.find or "\n" in m.replace or m.find == m.replace:
        return "a mutation changes exactly one line: `find` and `replace` are different single lines"
    try:
        text = open(os.path.join(tree, path), encoding="utf-8").read()
    except OSError:
        return f"`{m.file}` does not exist"
    if text.count(m.find) != 1:
        return f"`find` occurs {text.count(m.find)} times in `{m.file}`, not once"
    return ""


async def _scratch(workspace_dir: str, tmp: str) -> str:
    """A detached worktree of HEAD at ``tmp/tree`` holding the working tree's
    changes (through a private index); the failure text, or ``""``."""
    index = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
    rc, out = await _git(workspace_dir, "read-tree", "HEAD", env=index)
    if rc == 0:
        rc, out = await _git(workspace_dir, "add", "-A", env=index)
    if rc == 0:
        rc, out = await _git(workspace_dir, "write-tree", env=index)
    written = out
    if rc == 0:
        rc, out = await _git(workspace_dir, "worktree", "add", "-q", "--detach", os.path.join(tmp, "tree"), "HEAD")
    if rc == 0:
        rc, out = await _git(os.path.join(tmp, "tree"), "read-tree", "-m", "-u", "HEAD", written)
    return "" if rc == 0 else f"git: {out[-200:]}"


async def mutation_failures(workspace_dir: str, cmd: str, critical: list[str], mutations: list[Mutation],
                            frozen: "tuple[str, ...]", run: Callable[[str, str], Awaitable[dict]]
                            ) -> tuple[list[str], str]:
    """``(failures, could_not_run)``: one failure per critical criterion the
    check refuses (no mutation, an invalid one, or one every test survives);
    ``could_not_run`` says why the check could not run here — the scratch
    worktree failed or verify is red in it unmutated — and counts against
    nothing. Runs in a detached worktree; the real tree is never touched."""
    with tempfile.TemporaryDirectory(prefix="devclaw-mutation-") as tmp:
        tree = os.path.join(tmp, "tree")
        try:
            why = await _scratch(workspace_dir, tmp)
            if why:
                return [], f"the scratch worktree could not be made: {why}"
            base = await run(cmd, tree)
            if not base.get("passed"):
                return [], (f"`{cmd}` is red in the scratch worktree unmutated: "
                            f"{(base.get('output') or '')[-300:]}")
            return await _mutate_each(tree, cmd, critical, mutations, frozen, run), ""
        finally:
            await _git(workspace_dir, "worktree", "remove", "--force", tree)
            await _git(workspace_dir, "worktree", "prune")


async def _mutate_each(tree: str, cmd: str, critical: list[str], mutations: list[Mutation],
                       frozen: "tuple[str, ...]", run: Callable[[str, str], Awaitable[dict]]) -> list[str]:
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
    test_pass: Optional[Record] = None
    #: the branch carries no commit beyond the default branch — no code yet
    fresh: bool = False
    #: the brief rendered every issue the goal closes — a missing one is a
    #: failed read, not a contract change
    complete: bool = True
    #: the issue text: runner config it names is in scope, not frozen
    scope: str = ""
    #: what the newest gate could not check, said on the thread and in the PR
    notes: list[str] = field(default_factory=list)

    @property
    def contract(self) -> str:
        return contract_hash(self.criteria)

    @property
    def critical(self) -> list[str]:
        return [] if self.size == "S" else [c for c, crit in self.criteria if crit]

    @property
    def wants_test_pass(self) -> bool:
        """First on a fresh branch (an in-flight branch with work and no record
        skips it), again whenever the contract changed since the record."""
        if self.size == "S" or not self.criteria:
            return False
        if self.test_pass is None:
            return self.fresh
        return self.complete and self.test_pass.contract != self.contract

    def note(self) -> str:
        lines = [SIZE_NOTES.get(self.size, SIZE_NOTES["M"])]
        if self.test_pass and self.test_pass.files:
            lines.append(FROZEN_NOTE.format(commit=self.test_pass.commit[:10],
                                            files=", ".join(f"`{f}`" for f in self.test_pass.files)))
        if self.critical:
            lines.append(MUTATION_NOTE.format(criteria="\n".join(f"{n}. {c}" for n, c in enumerate(self.critical, 1))))
        return "\n\n".join(lines)


async def plan_for(brief: str, size: str, workspace_dir: str, closes: Iterable[int] = ()) -> Plan:
    rendered = {int(n) for n in _ISSUE_NUMBER_RE.findall(brief or "")}
    scope = issue_text(brief)
    return Plan(size=size if size in SIZES else "M", criteria=contract_criteria(brief),
                test_pass=await recorded_test_pass(workspace_dir, scope), fresh=await fresh_branch(workspace_dir),
                complete=set(closes) <= rendered, scope=scope)


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
    "that pin each criterion of the contract's `## Done when` section. Name each test file like a test "
    "(under a `tests/` or `test/` directory, or `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`): those files "
    "and the runner config are frozen for the code pass. Write no production code beyond the minimum stub "
    "the tests need to fail for the right reason: the tests must fail now, because the change does not "
    "exist yet. If `.devclaw/verify` does not exist, "
    "derive it from the project's CI (it runs what CI runs) and include it. Run it and confirm your new "
    "tests fail for the right reason. Do not commit, push or ship — the host records your files and "
    "proves they fail. End your final message with `TESTS: <what the tests pin>`, or with "
    "`BLOCKED: <question>` when the criteria cannot be tested."
)
TEST_REPASS_NOTE = (
    "The contract changed since the test pass at {commit}. Revise its tests ({files}) and add new ones "
    "so they pin the contract as it reads now; the code already on the branch is not yours to change."
)
TEST_VERIFY_MISSING_PROMPT = (
    "`.devclaw/verify` does not exist, so nothing can prove your tests fail. Create it: an executable "
    "script that runs what this project's CI runs (derive it from `.github/workflows`). Run it, do not "
    "commit, and hand back with your `TESTS:` line."
)
FROZEN_NOTE = (
    "The test pass (commit {commit}) froze these files — the tests it wrote from the contract and the "
    "config that decides which tests run: {files}. Write the code until the tests pass. Do not edit, "
    "delete or skip those files — the gate refuses a diff that touches them. A test you add goes in a "
    "new file."
)
MUTATION_NOTE = (
    "Critical criteria — the host breaks one condition of the code under test for each, in a scratch "
    "copy, and `.devclaw/verify` must go red:\n{criteria}\n\nBefore your exit line, end with a ```json "
    "block: {{\"mutations\": [{{\"criterion\": <n>, \"file\": \"<code file, not a test file>\", \"find\": "
    "\"<one exact line holding the condition>\", \"replace\": \"<that line with the condition broken>\"}}]}} "
    "— one per critical criterion."
)
MUTATION_FAILED_PROMPT = (
    "The one-mutation check refused this branch:\n{failures}\n\nA test must fail when the condition a "
    "critical criterion depends on breaks. Strengthen the tests (in a new file — the test-pass files are "
    "frozen), re-run `.devclaw/verify`, commit, and hand back with a fresh ```json mutations block and "
    "your exit line."
)
PR_NOTES_PROMPT = (
    "Not every gate ran in full on this branch. After the contract in `{intent_file}`, add a "
    "`## Not checked` section holding these lines as written, so the pull request description says so:\n{notes}"
)
HANDOFF_FROZEN_REASON = (
    "the hand-off changed the test-pass files ({commit}) after the gate passed: {violations}. The pull "
    "request {pr_url} is left as pushed — not reverted and not merged."
)
FROZEN_FAILED_PROMPT = (
    "The code pass touched the test-pass files ({commit}): {violations}. Restore them with "
    "`git checkout {commit} -- <file>`, make the code pass the tests as written (a new test goes in a "
    "new file), re-run `.devclaw/verify`, commit, and hand back with your exit line."
)

__all__ = ["SIZES", "BASE_REFS", "Plan", "Record", "Mutation", "size_of", "contract_criteria", "contract_hash",
           "plan_for", "merge_base", "fresh_branch", "recorded_test_pass", "changed_since", "commit_test_pass",
           "frozen_violations", "accepted_note", "frozen_name", "issue_text", "parse_mutations",
           "mutation_failures"]
