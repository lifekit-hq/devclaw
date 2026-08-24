"""The single doorway for the per-project manifest — ``devclaw.json``.

Spec 016 US2. The manifest is a REPO-OWNED, human-authored, PR-reviewed
declaration at the repo root: schema version, boilerplate revision, and the
per-project defaults devclaw used to infer (gate-strictness default, surface
kind for the browser gate, verify command, stack markers). Devclaw reads it
ONLY through this module — one parse, one validation, one precedence rule —
mirroring ``config.py``'s doctrine on the per-repo axis. Devclaw writes it
only via :func:`seed_manifest` on the reviewable onboard/install-PR path,
never at runtime (the #617 no-second-writer rule).

Trust boundary (FR-009, strengthened during implementation): every
GATE-RELEVANT read (strictness, surface, verify_cmd) comes from the repo's
**remote default-branch tip** — the human-merged truth — via
:func:`load_manifest_at_base`, never from the worktree or the goal branch,
both of which the sandboxed worker can write to (#358; the removed #233
planner override is the same class). A worktree read
(:func:`load_manifest`, ``ref=None``) exists for validation/preflight and
doctor presence checks only. Where no remote exists (dev/stub workspaces)
the worktree is the only truth and is used as the base.

Error posture: an ABSENT manifest is ``None`` (instance defaults apply); a
PRESENT-but-malformed manifest raises :class:`ManifestError` — loud, never a
silent fallback (FR-010). A ``schemaVersion`` newer than this instance
understands raises the distinct "instance too old" error, never a partial
parse.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

#: structurally identical to goal.models.Strictness (kept literal here so this
#: repo-level doorway does not import the goal layer).
_Strictness = Literal["trust", "strict"]

MANIFEST_NAME = "devclaw.json"

#: the manifest schema version this instance understands.
SCHEMA_VERSION = 1

#: the current vintage of the boilerplate ``onboard`` installs. Bump when the
#: installed boilerplate content changes; doctor compares it against each
#: repo's manifest (spec 016 US3) and re-onboard migrates.
BOILERPLATE_REVISION = 1

#: published schema URL, referenced from seeded manifests for editor validation.
SCHEMA_URL = (
    "https://raw.githubusercontent.com/lifekit-hq/devclaw/main/"
    "docs/reference/devclaw-manifest.schema.json"
)

_STRICTNESS_VALUES = ("trust", "strict")
_SURFACE_VALUES = ("app", "library")


class ManifestError(ValueError):
    """A present-but-unusable manifest. Always loud — callers must never
    swallow this into a silent default (FR-010)."""


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    boilerplate_revision: int = 0
    strictness_default: Optional[str] = None
    surface: Optional[str] = None
    verify_cmd: Optional[str] = None
    stack: tuple[str, ...] = field(default_factory=tuple)


def parse_manifest(text: str, *, source: str = MANIFEST_NAME) -> Manifest:
    """Parse + validate manifest JSON. Fail-loud on any malformation; unknown
    keys are tolerated (forward-compat within a schema version)."""
    try:
        raw = json.loads(text)
    except Exception as exc:
        raise ManifestError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{source} must be a JSON object, got {type(raw).__name__}")
    sv = raw.get("schemaVersion")
    if not isinstance(sv, int) or isinstance(sv, bool) or sv < 1:
        raise ManifestError(f"{source}: schemaVersion must be a positive integer")
    if sv > SCHEMA_VERSION:
        raise ManifestError(
            f"{source}: schemaVersion {sv} is newer than this devclaw instance "
            f"understands (max {SCHEMA_VERSION}) — instance too old for this repo; "
            "upgrade devclaw before operating it"
        )
    rev = raw.get("boilerplateRevision", 0)
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise ManifestError(f"{source}: boilerplateRevision must be a non-negative integer")
    strictness = raw.get("strictnessDefault")
    if strictness is not None and strictness not in _STRICTNESS_VALUES:
        raise ManifestError(
            f"{source}: strictnessDefault must be one of {_STRICTNESS_VALUES}, "
            f"got {strictness!r}"
        )
    surface = raw.get("surface")
    if surface is not None and surface not in _SURFACE_VALUES:
        raise ManifestError(
            f"{source}: surface must be one of {_SURFACE_VALUES}, got {surface!r}"
        )
    verify_cmd = raw.get("verifyCmd")
    if verify_cmd is not None and (not isinstance(verify_cmd, str) or not verify_cmd.strip()):
        raise ManifestError(f"{source}: verifyCmd must be a non-empty string")
    stack = raw.get("stack", [])
    if not isinstance(stack, list) or any(not isinstance(s, str) for s in stack):
        raise ManifestError(f"{source}: stack must be a list of strings")
    return Manifest(
        schema_version=sv,
        boilerplate_revision=rev,
        strictness_default=strictness,
        surface=surface,
        verify_cmd=verify_cmd.strip() if isinstance(verify_cmd, str) else None,
        stack=tuple(stack),
    )


def _git(workspace_dir: str, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        ["git", "-C", workspace_dir, *args],
        capture_output=True, text=True, timeout=60,
    )


def load_manifest(workspace_dir: str, ref: Optional[str] = None) -> Optional[Manifest]:
    """Load the manifest from the worktree (``ref=None``) or from a git ref.

    ``None`` when the file is absent at that location; :class:`ManifestError`
    when present but malformed. A git failure on a ref read raises (the
    ``task_change`` not-best-effort posture — a gate input that cannot be
    determined is loud, never a silent None)."""
    if ref is None:
        path = Path(workspace_dir) / MANIFEST_NAME
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"{path} unreadable: {exc}") from exc
        return parse_manifest(text, source=str(path))
    proc = _git(workspace_dir, "cat-file", "-e", f"{ref}:{MANIFEST_NAME}")
    if proc.returncode != 0:
        # distinguish "file absent at ref" from "ref/repo broken"
        head = _git(workspace_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if head.returncode != 0:
            raise ManifestError(
                f"cannot read {MANIFEST_NAME} at {ref!r} in {workspace_dir}: "
                f"ref does not resolve ({(head.stderr or proc.stderr).strip()})"
            )
        return None
    show = _git(workspace_dir, "show", f"{ref}:{MANIFEST_NAME}")
    if show.returncode != 0:
        raise ManifestError(
            f"git show {ref}:{MANIFEST_NAME} failed in {workspace_dir}: "
            f"{show.stderr.strip()}"
        )
    return parse_manifest(show.stdout, source=f"{MANIFEST_NAME}@{ref}")


def _default_branch_ref(workspace_dir: str) -> Optional[str]:
    """The remote default-branch ref (``origin/<default>``), or None when the
    workspace has no usable remote tracking ref (dev/stub repos)."""
    head = _git(workspace_dir, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip()
    for cand in ("origin/main", "origin/master"):
        probe = _git(workspace_dir, "rev-parse", "--verify", "--quiet", cand)
        if probe.returncode == 0:
            return cand
    return None


def load_manifest_at_base(workspace_dir: str) -> Optional[Manifest]:
    """The GATE-RELEVANT read: the manifest at the remote default-branch tip —
    human-merged truth the worker cannot write to. Falls back to the worktree
    only when the workspace has no remote at all (then the worktree IS the
    only truth — dev/stub repos)."""
    # Fast path: not a git checkout (stub/dev workspaces) — no subprocess,
    # the worktree file (or its absence) is the only truth.
    if not (Path(workspace_dir) / ".git").exists():
        return load_manifest(workspace_dir)
    ref = _default_branch_ref(workspace_dir)
    if ref is None:
        return load_manifest(workspace_dir)
    return load_manifest(workspace_dir, ref=ref)


# ---- precedence (spec 016 FR-008: most-specific-wins, resolved live) ------


def effective_strictness(
    explicit: Optional[str], manifest_default: Optional[str]
) -> _Strictness:
    """Explicit per-goal setting > manifest default > instance default
    ('trust'). Anything unrecognized resolves FAIL-CLOSED to 'strict' — the
    ``gate_policy`` posture, never a silent 'trust'."""
    for candidate in (explicit, manifest_default):
        if candidate is None:
            continue
        if candidate == "trust":
            return "trust"
        return "strict"  # 'strict' itself, or anything unrecognized (fail-closed)
    return "trust"


def resolve_goal_strictness(goal) -> _Strictness:
    """Live most-specific-wins resolution for goal-level reads (dispatch
    snapshot, done-gate structural axis, slice guardrail). Reads the manifest
    at the merged base — a worker-side edit on the goal branch or worktree
    never changes a gate regime (FR-009). Raises ManifestError on a malformed
    base manifest (loud, fail-closed)."""
    explicit = getattr(goal, "strictness_explicit", None)
    if explicit == "trust":
        return "trust"
    if explicit == "strict":
        return "strict"
    manifest = load_manifest_at_base(goal.workspace_dir) if goal.workspace_dir else None
    return effective_strictness(None, manifest.strictness_default if manifest else None)


def resolve_verify_cmd(action_verify_cmd: Optional[str], goal_verify_cmd: Optional[str],
                       workspace_dir: Optional[str]) -> Optional[str]:
    """verify_cmd precedence: planner action > goal > manifest (merged base).
    All three tiers are host-validated inputs; the manifest tier is
    human-authored and PR-reviewed (unlike the removed #233 planner override,
    which was model output honored blindly)."""
    if action_verify_cmd:
        return action_verify_cmd
    if goal_verify_cmd:
        return goal_verify_cmd
    if not workspace_dir:
        return None
    manifest = load_manifest_at_base(workspace_dir)
    return manifest.verify_cmd if manifest else None


def resolve_surface(workspace_dir: str) -> Optional[str]:
    """The declared browser-gate surface kind at the merged base, or None when
    undeclared (the path-glob heuristics stay in charge)."""
    manifest = load_manifest_at_base(workspace_dir)
    return manifest.surface if manifest else None


# ---- devclaw:managed markers (one home — spec 016 US3) --------------------

#: the marker pair bounding devclaw-owned blocks in operated-repo docs
#: (AGENTS.md). Everything outside them is human-owned; a re-onboard replaces
#: only within. Previously these literals lived only in the onboard skill's
#: prose + two tests — this is now the ONE code home (doctor's marker-integrity
#: check reads them from here).
MANAGED_START = "<!-- devclaw:managed:start -->"
MANAGED_END = "<!-- devclaw:managed:end -->"


def managed_marker_problem(text: str) -> Optional[str]:
    """A human-readable defect in the managed-marker pairing of ``text``, or
    None when markers are absent-or-well-formed (absent is fine — not every
    doc carries a managed block)."""
    starts = text.count(MANAGED_START)
    ends = text.count(MANAGED_END)
    if starts == 0 and ends == 0:
        return None
    if starts != ends:
        return f"unpaired devclaw:managed markers ({starts} start / {ends} end)"
    if starts > 1:
        return f"{starts} devclaw:managed blocks (expected at most one)"
    if text.index(MANAGED_START) > text.index(MANAGED_END):
        return "devclaw:managed end marker precedes the start marker"
    return None


# ---- seeding (the ONE devclaw-write path — reviewable PR only) ------------


def seed_manifest(workspace_dir: str) -> Optional[str]:
    """Write a seed ``devclaw.json`` when the repo has none. Called ONLY from
    the onboard/install reviewable-PR path — never a silent runtime write.
    Returns the relative path written, or None when a manifest already exists
    (an existing manifest is human-owned; devclaw does not touch it here)."""
    path = Path(workspace_dir) / MANIFEST_NAME
    if path.exists():
        return None
    seed = {
        "$schema": SCHEMA_URL,
        "schemaVersion": SCHEMA_VERSION,
        "boilerplateRevision": BOILERPLATE_REVISION,
    }
    path.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
    return MANIFEST_NAME


def migrate_manifest(workspace_dir: str) -> bool:
    """Bring an existing manifest's MECHANICAL fields current — schemaVersion
    and boilerplateRevision only; every human-set field (and any unknown key)
    is preserved verbatim. Returns True when the file changed. Like
    :func:`seed_manifest`, called ONLY from a reviewable-PR path (spec 016
    US3: doctor detects, re-onboard migrates, the human merges). Raises
    ManifestError on a malformed file — migration never repairs by guessing."""
    path = Path(workspace_dir) / MANIFEST_NAME
    if not path.exists():
        return False
    parse_manifest(path.read_text(encoding="utf-8"), source=str(path))  # validate loud
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (raw.get("schemaVersion") == SCHEMA_VERSION
            and raw.get("boilerplateRevision", 0) >= BOILERPLATE_REVISION):
        return False
    raw["schemaVersion"] = SCHEMA_VERSION
    raw["boilerplateRevision"] = BOILERPLATE_REVISION
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return True
