"""Per-project doctor checks — registry/goal link integrity + workspace state.

Same posture as the instance checks: mechanical, read-only, zero cognition.
US2/US3 extend this module with manifest / revision / marker / scaffold
checks (spec 016 FR-003).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pathlib import Path

from .. import project_manifest as _manifest
from ..engine.workspace import workspace_is_dispatchable
from .model import Finding, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from ..project_registry import Project
    from .context import InstanceContext


def check_workspace_preflight(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    cid = "project.workspace.preflight"
    reason = workspace_is_dispatchable(project.workspace_dir)
    if reason:
        return [Finding(cid, Verdict.FAIL, reason,
                        remedy="update_project (or restore the workspace checkout)",
                        project_id=project.id)]
    return [Finding(cid, Verdict.OK,
                    f"workspace {project.workspace_dir} dispatchable",
                    project_id=project.id)]


def check_dangling_links(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Advisory ``goal_ids`` entries pointing at goals that no longer exist.

    Today this drift is INVISIBLE: ``project_rollup`` joins on ``project_id``
    only and simply emits nothing for a dangling link (nothing ever sets the
    vestigial ``missing`` marker). Doctor is the producer of that finding —
    as a report line, never a row mutation.
    """
    cid = "project.links.dangling"
    dangling = sorted(gid for gid in (project.goal_ids or []) if not ctx.goal_store.exists(gid))
    if dangling:
        return [Finding(
            cid, Verdict.WARN,
            f"goal_ids entr{'ies' if len(dangling) > 1 else 'y'} resolving to no goal: "
            f"{', '.join(dangling)} (cancel+refile drift)",
            remedy="link_goal (relink or unlink the stale id)",
            project_id=project.id,
        )]
    return [Finding(cid, Verdict.OK, "all advisory goal links resolve", project_id=project.id)]


def check_unstamped_goals(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Goals whose workspace maps onto this project but carry no project_id.

    The one-shot #524 P3 backfill ran once and was deleted (2026-08-29 prune),
    so a goal created in a gap stays unstamped forever and silently drops out
    of every project rollup.
    """
    cid = "project.links.unstamped_goals"
    unstamped = sorted(
        g.id for g in ctx.goals
        if g.project_id is None and g.workspace_dir and g.workspace_dir == project.workspace_dir
    )
    if unstamped:
        return [Finding(
            cid, Verdict.WARN,
            f"goal(s) on this workspace with no project_id stamp: {', '.join(unstamped)} — "
            "invisible to project rollups",
            remedy="link_goal",
            project_id=project.id,
        )]
    return [Finding(cid, Verdict.OK, "no unstamped goals on this workspace", project_id=project.id)]


# ---- manifest / boilerplate drift (spec 016 US3) --------------------------


def check_manifest(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Presence + validity + revision currency of the repo's devclaw.json.
    Worktree read on purpose — doctor reports the repo's CURRENT state; the
    gate reads stay pinned to the merged base (FR-009)."""
    pid = project.id
    ws = project.workspace_dir or ""
    if not ws or not Path(ws).exists():
        return [Finding("project.manifest.presence", Verdict.UNKNOWN,
                        "workspace not on disk — manifest state unknowable",
                        project_id=pid)]
    try:
        manifest = _manifest.load_manifest(ws)
    except _manifest.ManifestError as exc:
        return [Finding("project.manifest.valid", Verdict.FAIL, str(exc),
                        remedy="fix devclaw.json by PR (it is human-owned)",
                        project_id=pid)]
    if manifest is None:
        return [Finding("project.manifest.presence", Verdict.WARN,
                        "no devclaw.json — instance defaults apply; per-project "
                        "declarations (strictness, surface, verify_cmd) unavailable",
                        remedy="onboard (the install PR seeds one)",
                        project_id=pid)]
    findings = [Finding("project.manifest.presence", Verdict.OK,
                        f"devclaw.json present (schema {manifest.schema_version})",
                        project_id=pid)]
    if manifest.boilerplate_revision < _manifest.BOILERPLATE_REVISION:
        findings.append(Finding(
            "project.manifest.revision", Verdict.WARN,
            f"boilerplate revision {manifest.boilerplate_revision} behind the "
            f"instance's {_manifest.BOILERPLATE_REVISION}",
            remedy="onboard (re-onboard migrates via a reviewable PR)",
            project_id=pid))
    else:
        findings.append(Finding("project.manifest.revision", Verdict.OK,
                                f"boilerplate revision current "
                                f"({manifest.boilerplate_revision})",
                                project_id=pid))
    return findings


def check_marker_integrity(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    cid = "project.markers.integrity"
    pid = project.id
    agents = Path(project.workspace_dir or "") / "AGENTS.md"
    if not agents.exists():
        return [Finding(cid, Verdict.OK, "no AGENTS.md (nothing to bound)",
                        project_id=pid)]
    problem = _manifest.managed_marker_problem(agents.read_text(encoding="utf-8"))
    if problem:
        return [Finding(cid, Verdict.FAIL, f"AGENTS.md: {problem} — a re-onboard "
                        "cannot tell devclaw-owned from human-owned content",
                        remedy="fix the marker pair by PR, then onboard",
                        project_id=pid)]
    return [Finding(cid, Verdict.OK, "devclaw:managed markers well-formed",
                    project_id=pid)]


def check_scaffold_drift(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Diff the repo's committed-in-worktree ``.specify/`` scaffold against the
    packaged canonical source (the file-copied half of the boilerplate — the
    #610 silent-fork class, detectable per repo). Extra repo-local files are
    fine; a canonical file that is missing or differs is drift."""
    cid = "project.scaffold.drift"
    pid = project.id
    ws = Path(project.workspace_dir or "")
    dest = ws / ".specify"
    if not dest.is_dir():
        return [Finding(cid, Verdict.OK, "no .specify/ scaffold (not onboarded yet)",
                        project_id=pid)]
    from ..speckit_setup import _SCAFFOLD_DIRS, _SCAFFOLD_FILES, _speckit_source
    src = _speckit_source()
    if not src.is_dir():
        return [Finding(cid, Verdict.UNKNOWN,
                        f"vendored speckit source missing at {src}", project_id=pid)]
    drifted: list[str] = []
    for dirname in _SCAFFOLD_DIRS:
        s = src / dirname
        if not s.is_dir():
            continue
        for f in sorted(p for p in s.rglob("*") if p.is_file()):
            rel = f.relative_to(src)
            target = dest / rel
            if not target.exists() or target.read_bytes() != f.read_bytes():
                drifted.append(str(rel))
    for name in _SCAFFOLD_FILES:
        s = src / name
        if s.is_file():
            target = dest / name
            if not target.exists() or target.read_bytes() != s.read_bytes():
                drifted.append(name)
    if drifted:
        shown = ", ".join(drifted[:5]) + (f" (+{len(drifted) - 5} more)" if len(drifted) > 5 else "")
        return [Finding(cid, Verdict.WARN,
                        f".specify/ drifted from the packaged scaffold: {shown}",
                        remedy="onboard (re-onboard refreshes the scaffold via PR)",
                        project_id=pid)]
    return [Finding(cid, Verdict.OK, ".specify/ matches the packaged scaffold",
                    project_id=pid)]


def check_issue_refs_shape(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Referenced-goal records parse (spec 019 US1): every goal on this
    project whose goal.yaml carries ``issue_refs`` must load, and the refs
    must be positive integers — a hand-edited or corrupted record would
    otherwise surface only as a mid-night dispatch crash."""
    cid = "project.goals.issue_refs"
    bad: list[str] = []
    for gid in ctx.goal_store.list_goal_ids():
        try:
            g = ctx.goal_store.load_goal(gid)
        except Exception as exc:  # noqa: BLE001 — the unparseable record IS the finding
            bad.append(f"{gid} (goal.yaml unreadable: {exc})")
            continue
        if g.project_id != project.id:
            continue
        if any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in g.issue_refs):
            bad.append(f"{gid} (issue_refs {g.issue_refs!r} not positive ints)")
    if bad:
        return [Finding(
            cid, Verdict.FAIL,
            "referenced-goal record(s) malformed: " + "; ".join(sorted(bad)[:5]),
            remedy="cancel + recreate the goal (goals are durable — no field patches)",
            project_id=project.id,
        )]
    return [Finding(cid, Verdict.OK, "all referenced-goal records parse", project_id=project.id)]


#: Module-global gh boundary so tests patch it HERE (the collector
#: convention). Returns the open ready-labeled issues as parsed dicts, or
#: None when the listing could not run at all (no gh, network failure,
#: non-JSON) — the check reports UNKNOWN then, never OK. PRs are excluded:
#: the issues listing endpoint returns them too.
def _list_ready_issues(repo_url: str, label: str) -> "list[dict] | None":
    import json
    import subprocess

    from ..goal.remote_checks import parse_owner_repo

    owner_repo = parse_owner_repo(repo_url)
    if not owner_repo:
        return None
    try:
        proc = subprocess.run(
            ["gh", "api",
             f"repos/{owner_repo}/issues?labels={label}&state=open&per_page=100"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return [i for i in data if isinstance(i, dict) and "pull_request" not in i]


def check_backlog_ready_contract(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Every open ready-labeled issue carries an acceptance section the
    contract reader actually parses.

    The label and the contract have separate lifecycles: grading stamps the
    label once, while :func:`devclaw.goal.issue_ref.extract_acceptance` reads
    the section live at every dispatch — so a requirement change (spec 019
    made the section load-bearing) or a later body edit leaves labeled issues
    the pointer lane can only discover goal-by-goal, as ``MissingAcceptance``
    blocks. Same drift class as #641: the stubbed suite structurally cannot
    see it. Advisory, never a hold — the dispatch boundary already fails
    loud; doctor's job is surfacing the whole labeled population at once.
    """
    from ..intake import READY_LABEL  # one home for the label (issue_doorway discipline)

    from ..goal.issue_ref import extract_acceptance

    cid = "project.backlog.ready_contract"
    if not project.repo_url:
        return [Finding(cid, Verdict.OK,
                        "no repo_url — no labeled backlog to verify",
                        project_id=project.id)]
    issues = _list_ready_issues(project.repo_url, READY_LABEL)
    if issues is None:
        return [Finding(cid, Verdict.UNKNOWN,
                        f"could not list open {READY_LABEL} issues for "
                        f"{project.repo_url} — contract coverage unproven",
                        remedy="verify gh auth/network on the instance, re-run doctor",
                        project_id=project.id)]
    bad = sorted(
        int(i["number"]) for i in issues
        if isinstance(i.get("number"), int)
        and not extract_acceptance(str(i.get("body") or ""))
    )
    if bad:
        shown = ", ".join(f"#{n}" for n in bad[:10]) + (
            f" (+{len(bad) - 10} more)" if len(bad) > 10 else "")
        return [Finding(cid, Verdict.WARN,
                        f"open {READY_LABEL} issue(s) with no parseable acceptance "
                        f"section: {shown} — a goal referencing one blocks at "
                        "dispatch (MissingAcceptance)",
                        remedy="regrade_intake (groom a '## Done when' / "
                               "'## Acceptance' section into the issue)",
                        project_id=project.id)]
    evidence = (
        f"{len(issues)} open {READY_LABEL} issue(s) all carry a parseable "
        "acceptance section" if issues else f"no open {READY_LABEL} issues"
    )
    return [Finding(cid, Verdict.OK, evidence, project_id=project.id)]


#: Files whose content betrays a private-registry dependency, and how much of
#: each is worth reading (a lockfile can be tens of MB; the registry line lives
#: in the config, and in a lockfile it recurs on every resolved URL, so a
#: bounded head read is enough to see it).
_REGISTRY_EVIDENCE_FILES: tuple[tuple[str, int], ...] = (
    (".npmrc", 64 * 1024),
    ("package-lock.json", 512 * 1024),
)

#: Private npm registry hosts the advisory recognises. Deliberately narrow —
#: this check exists to catch the write-and-forget case, not to be a registry
#: taxonomy.
_PRIVATE_REGISTRY_HOSTS: tuple[str, ...] = ("npm.pkg.github.com",)


def check_capability_declaration(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """ADVISORY (spec 030 FR-005a): the repo visibly depends on a private npm
    registry but declares no ``registry:*`` capability in ``devclaw.json``.

    Capability declaration is explicit-only (FR-005), which buys a contract the
    instance can trust and costs write-and-forget — a repo that grows a private
    dependency never gets the admission brake. This check is that cost's
    backstop and NOTHING more: it is a report line, never a hold. Mechanical
    and bounded — a couple of head reads, no network, no cognition.
    """
    cid = "project.capabilities.undeclared"
    ws = Path(project.workspace_dir or "")
    try:
        manifest = _manifest.load_manifest(str(ws))
    except _manifest.ManifestError as exc:
        # The manifest checks above already report this loudly; say why this
        # one could not judge rather than guessing (FR-005: never silent).
        return [Finding(cid, Verdict.UNKNOWN, f"manifest unreadable: {exc}",
                        project_id=project.id)]
    declared = manifest.capabilities if manifest else ()
    if any(c.startswith("registry:") for c in declared):
        return [Finding(cid, Verdict.OK, "registry capability declared",
                        project_id=project.id)]
    for name, budget in _REGISTRY_EVIDENCE_FILES:
        try:
            with (ws / name).open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(budget)
        except OSError:
            continue  # absent or unreadable — no evidence, not a finding
        for host in _PRIVATE_REGISTRY_HOSTS:
            if host in head:
                return [Finding(
                    cid, Verdict.WARN,
                    f"{name} resolves against {host} but devclaw.json declares no "
                    "registry:* capability — a broken registry credential will "
                    "burn worker sessions instead of holding dispatch",
                    remedy='add "capabilities": ["registry:npm-github"] to devclaw.json',
                    project_id=project.id,
                )]
    return [Finding(cid, Verdict.OK, "no undeclared registry dependency visible",
                    project_id=project.id)]


PROJECT_CHECKS: tuple = (
    check_workspace_preflight,
    check_dangling_links,
    check_unstamped_goals,
    check_manifest,
    check_marker_integrity,
    check_scaffold_drift,
    check_issue_refs_shape,
    check_backlog_ready_contract,
    check_capability_declaration,
)
