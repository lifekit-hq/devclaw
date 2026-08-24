"""The devclaw.json doorway (spec 016 US2) — parse, load, precedence, seeding.

Error posture under test: absent = None (defaults apply), malformed = LOUD
ManifestError (never a silent fallback), schema-newer-than-instance = the
distinct "instance too old" error.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devclaw.project_manifest import (
    BOILERPLATE_REVISION,
    MANIFEST_NAME,
    SCHEMA_VERSION,
    Manifest,
    ManifestError,
    effective_strictness,
    load_manifest,
    load_manifest_at_base,
    parse_manifest,
    seed_manifest,
)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _repo(tmp_path, name="repo"):
    ws = tmp_path / name
    ws.mkdir()
    _git(tmp_path, "init", "-q", str(ws))
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    return ws


# ---- parse ----------------------------------------------------------------


def test_parse_valid_full_manifest():
    m = parse_manifest(json.dumps({
        "$schema": "https://example/schema.json",
        "schemaVersion": 1,
        "boilerplateRevision": 3,
        "strictnessDefault": "strict",
        "surface": "library",
        "verifyCmd": "dotnet test",
        "stack": ["dotnet", "angular"],
    }))
    assert m == Manifest(1, 3, "strict", "library", "dotnet test", ("dotnet", "angular"))


def test_parse_minimal_manifest_defaults():
    m = parse_manifest('{"schemaVersion": 1}')
    assert m.boilerplate_revision == 0
    assert m.strictness_default is None and m.surface is None and m.verify_cmd is None


def test_unknown_keys_tolerated_forward_compat():
    m = parse_manifest('{"schemaVersion": 1, "futureKnob": {"x": 1}}')
    assert m.schema_version == 1


@pytest.mark.parametrize("text,fragment", [
    ("{not json", "not valid JSON"),
    ("[1,2]", "must be a JSON object"),
    ("{}", "schemaVersion must be a positive integer"),
    ('{"schemaVersion": "1"}', "schemaVersion must be a positive integer"),
    ('{"schemaVersion": 1, "strictnessDefault": "loose"}', "strictnessDefault"),
    ('{"schemaVersion": 1, "surface": "cli"}', "surface"),
    ('{"schemaVersion": 1, "verifyCmd": "  "}', "verifyCmd"),
    ('{"schemaVersion": 1, "stack": "dotnet"}', "stack"),
])
def test_malformed_manifest_is_loud(text, fragment):
    with pytest.raises(ManifestError, match=fragment):
        parse_manifest(text)


def test_schema_newer_than_instance_is_the_distinct_too_old_error():
    with pytest.raises(ManifestError, match="instance too old"):
        parse_manifest(json.dumps({"schemaVersion": SCHEMA_VERSION + 1}))


# ---- load: worktree + ref -------------------------------------------------


def test_absent_manifest_is_none_not_an_error(tmp_path):
    assert load_manifest(str(tmp_path)) is None


def test_worktree_malformed_raises(tmp_path):
    (tmp_path / MANIFEST_NAME).write_text("{oops")
    with pytest.raises(ManifestError):
        load_manifest(str(tmp_path))


def test_load_at_ref_reads_the_committed_version_not_the_worktree(tmp_path):
    ws = _repo(tmp_path)
    (ws / MANIFEST_NAME).write_text('{"schemaVersion": 1, "surface": "app"}')
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "manifest")
    sha = subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    (ws / MANIFEST_NAME).write_text('{"schemaVersion": 1, "surface": "library"}')
    at_ref = load_manifest(str(ws), ref=sha)
    assert at_ref is not None and at_ref.surface == "app"


def test_load_at_unresolvable_ref_is_loud(tmp_path):
    ws = _repo(tmp_path)
    (ws / "f").write_text("x")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "c")
    with pytest.raises(ManifestError, match="does not resolve"):
        load_manifest(str(ws), ref="deadbeef" * 5)


def test_absent_at_ref_is_none(tmp_path):
    ws = _repo(tmp_path)
    (ws / "f").write_text("x")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "c")
    assert load_manifest(str(ws), ref="HEAD") is None


# ---- load at base (the gate-relevant read, FR-009) ------------------------


def test_non_git_workspace_base_read_falls_back_to_worktree(tmp_path):
    (tmp_path / MANIFEST_NAME).write_text('{"schemaVersion": 1, "surface": "library"}')
    m = load_manifest_at_base(str(tmp_path))
    assert m is not None and m.surface == "library"


def test_base_read_uses_merged_remote_truth_not_worker_edits(tmp_path):
    # origin carries surface=app; the workspace clone's worker edits (worktree
    # AND a goal-branch commit) declare library — the base read must see app.
    origin = _repo(tmp_path, "origin")
    (origin / MANIFEST_NAME).write_text('{"schemaVersion": 1, "surface": "app"}')
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "manifest")
    ws = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(ws)], check=True,
                   capture_output=True)
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    _git(ws, "checkout", "-q", "-b", "devclaw/goal-x")
    (ws / MANIFEST_NAME).write_text('{"schemaVersion": 1, "surface": "library"}')
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "worker tamper")
    m = load_manifest_at_base(str(ws))
    assert m is not None and m.surface == "app"


# ---- precedence -----------------------------------------------------------


@pytest.mark.parametrize("explicit,manifest,expected", [
    ("trust", "strict", "trust"),      # explicit goal wins
    ("strict", "trust", "strict"),
    (None, "strict", "strict"),        # manifest default applies
    (None, "trust", "trust"),
    (None, None, "trust"),             # instance default
    ("garbled", None, "strict"),       # unrecognized fails CLOSED
    (None, "garbled", "strict"),
])
def test_effective_strictness_most_specific_wins(explicit, manifest, expected):
    assert effective_strictness(explicit, manifest) == expected


# ---- seeding --------------------------------------------------------------


def test_seed_manifest_writes_current_revisions_once(tmp_path):
    rel = seed_manifest(str(tmp_path))
    assert rel == MANIFEST_NAME
    m = load_manifest(str(tmp_path))
    assert m is not None
    assert m.schema_version == SCHEMA_VERSION
    assert m.boilerplate_revision == BOILERPLATE_REVISION


def test_seed_manifest_never_touches_an_existing_human_owned_file(tmp_path):
    (tmp_path / MANIFEST_NAME).write_text('{"schemaVersion": 1, "surface": "app"}')
    assert seed_manifest(str(tmp_path)) is None
    assert load_manifest(str(tmp_path)).surface == "app"  # byte-untouched semantics
