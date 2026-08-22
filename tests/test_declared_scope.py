"""Regression tests for the declared-file-scope substrate (spec 010 FR-101/FR-103).

A `[P]` task earns concurrent execution by declaring the paths it will touch.
:mod:`devclaw.loom.declared_scope` is the pure mechanism that holds it to that:
diff in, verdict out — no git, no workspace, no LLM.

The load-bearing properties pinned here:

  1. every touched path is seen (adds, edits, deletes, renames, spaces in names);
  2. a claim is a `[P]` row WITH a scope that THIS increment checked off — nothing
     weaker counts, and nothing checked earlier counts twice;
  3. `*` narrows (it does not cross ``/``) — otherwise a declaration is theatre;
  4. no claim ⇒ NOT CONSULTED, never "allowed": the failure direction of every
     parser here is silence about the contract, not permission.
"""

from __future__ import annotations

from devclaw.loom.declared_scope import (
    ScopeCheck,
    changed_paths,
    claimed_scopes,
    parse_scopes,
    path_in_scope,
    scope_check,
    violation_summary,
)


def _tasks_diff(before: str, after: str, path: str = "specs/010-feat/tasks.md") -> str:
    """A unified-diff block that replaces one tasks.md line with another."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,1 @@\n"
        f"-{before}\n"
        f"+{after}\n"
    )


def _file_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,0 +1,1 @@\n"
        "+touched\n"
    )


# ---- changed_paths ---------------------------------------------------------


def test_changed_paths_reads_every_touched_path_from_the_diff():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/src/new.py b/src/new.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+z\n"
        "diff --git a/src/gone.py b/src/gone.py\n"
        "deleted file mode 100644\n--- a/src/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-q\n"
        "diff --git a/old/name.py b/new/name.py\n"
        "similarity index 100%\nrename from old/name.py\nrename to new/name.py\n"
    )
    assert changed_paths(diff) == (
        "new/name.py",
        "old/name.py",
        "src/a.py",
        "src/gone.py",
        "src/new.py",
    )


def test_changed_paths_ignores_diff_content_that_looks_like_a_diff_header():
    """A repo containing patch files must not have its own content read as file
    boundaries — every false path would read as an out-of-scope edit."""
    diff = (
        "diff --git a/fixtures/sample.patch b/fixtures/sample.patch\n"
        "--- a/fixtures/sample.patch\n+++ b/fixtures/sample.patch\n"
        "@@ -1,0 +1,3 @@\n"
        "+diff --git a/secret/prod.py b/secret/prod.py\n"
        "+--- a/secret/prod.py\n"
        "++++ b/secret/prod.py\n"
    )
    assert changed_paths(diff) == ("fixtures/sample.patch",)


def test_changed_paths_handles_a_path_containing_spaces():
    diff = (
        'diff --git a/docs/my notes.md b/docs/my notes.md\n'
        "--- a/docs/my notes.md\n+++ b/docs/my notes.md\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert changed_paths(diff) == ("docs/my notes.md",)


# ---- claim detection -------------------------------------------------------


def test_claimed_scopes_names_only_the_parallel_rows_this_increment_checked():
    diff = _tasks_diff(
        "- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)",
        "- [x] T012 [P] [US1] Renderer (scope: src/widget/**)",
    )
    assert claimed_scopes(diff) == {"T012": ("src/widget/**",)}


def test_a_row_already_checked_before_this_increment_is_not_a_claim():
    diff = _tasks_diff(
        "- [x] T012 [P] [US1] Renderer (scope: src/widget/**)",
        "- [x] T012 [P] [US1] Renderer, reworded (scope: src/widget/**)",
    )
    assert claimed_scopes(diff) == {}


def test_a_reworded_task_row_checked_in_the_same_increment_is_still_claimed():
    """Claims key off the task id, not the label — the rule slice_guard learned."""
    diff = _tasks_diff(
        "- [ ] T012 [P] [US1] Old wording (scope: src/widget/**)",
        "- [x] T012 [P] [US1] New wording entirely (scope: src/widget/**)",
    )
    assert claimed_scopes(diff) == {"T012": ("src/widget/**",)}


def test_a_parallel_row_without_a_declared_scope_is_not_a_claim():
    """FR-101 is `[P]` **with** a declared scope; `[P]` alone declares nothing."""
    diff = _tasks_diff(
        "- [ ] T012 [P] [US1] Renderer",
        "- [x] T012 [P] [US1] Renderer",
    )
    assert claimed_scopes(diff) == {}


def test_a_scoped_row_that_is_not_marked_parallel_is_not_a_claim():
    """The hermetic contract is fan-out's, not every task's."""
    diff = _tasks_diff(
        "- [ ] T012 [US1] Renderer (scope: src/widget/**)",
        "- [x] T012 [US1] Renderer (scope: src/widget/**)",
    )
    assert claimed_scopes(diff) == {}


def test_a_scope_declaration_outside_the_task_graph_is_not_a_claim():
    """Only ``specs/*/tasks.md`` speaks for the plan."""
    diff = (
        "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "-- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
        "+- [x] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
    )
    assert claimed_scopes(diff) == {}


def test_parse_scopes_reads_a_comma_separated_declaration():
    assert parse_scopes("T1 [P] do it (scope: a/**, b.py ,  c/d/*.ts)") == (
        "a/**",
        "b.py",
        "c/d/*.ts",
    )
    assert parse_scopes("T1 [P] do it") == ()


# ---- glob semantics --------------------------------------------------------


def test_star_does_not_cross_a_directory_separator_but_doublestar_does():
    assert path_in_scope("src/a.py", ["src/*"])
    assert not path_in_scope("src/deep/a.py", ["src/*"])
    assert path_in_scope("src/deep/a.py", ["src/**"])
    assert path_in_scope("src/a.py", ["src/**/a.py"])


def test_a_declared_directory_covers_its_subtree():
    assert path_in_scope("src/widget/deep/a.py", ["src/widget/"])
    assert path_in_scope("src/widget/deep/a.py", ["src/widget"])
    assert not path_in_scope("src/widgetry/a.py", ["src/widget"])


def test_an_empty_declaration_covers_nothing():
    assert not path_in_scope("src/a.py", [])
    assert not path_in_scope("src/a.py", ["", "   "])


# ---- the verdict -----------------------------------------------------------


def test_scope_violations_is_empty_when_every_touched_path_is_declared():
    diff = _tasks_diff(
        "- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)",
        "- [x] T012 [P] [US1] Renderer (scope: src/widget/**)",
    ) + _file_diff("src/widget/render.py")
    check = scope_check(diff)
    assert check.consulted
    assert check.violations == ()


def test_scope_violations_names_every_out_of_scope_path():
    diff = _tasks_diff(
        "- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)",
        "- [x] T012 [P] [US1] Renderer (scope: src/widget/**)",
    ) + _file_diff("src/widget/render.py") + _file_diff("src/core/db.py") + _file_diff("infra/deploy.sh")
    check = scope_check(diff)
    assert check.violations == ("infra/deploy.sh", "src/core/db.py")
    summary = violation_summary(check)
    assert "src/core/db.py" in summary and "infra/deploy.sh" in summary
    assert "src/widget/**" in summary and "T012" in summary


def test_the_tasks_file_itself_is_always_in_scope_for_a_claimed_increment():
    diff = _tasks_diff(
        "- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)",
        "- [x] T012 [P] [US1] Renderer (scope: src/widget/**)",
    ) + _file_diff("src/widget/render.py")
    assert scope_check(diff).violations == ()


def test_an_increment_that_claims_nothing_is_not_consulted():
    diff = _file_diff("anything/at/all.py")
    check = scope_check(diff)
    assert not check.consulted
    assert check.violations == ()


def test_a_garbled_diff_yields_no_claim_rather_than_a_silent_allow():
    for junk in ("", "   ", "not a diff at all", "@@ @@ +++ ---\n"):
        check = scope_check(junk)
        assert not check.consulted, junk
        assert check.violations == (), junk


def test_work_smuggled_under_an_unscoped_task_still_violates_the_claim():
    """Checking an extra unscoped row must not widen the allowed set — that would
    be a one-line route-around of the whole contract (#358)."""
    diff = (
        "diff --git a/specs/010-feat/tasks.md b/specs/010-feat/tasks.md\n"
        "--- a/specs/010-feat/tasks.md\n+++ b/specs/010-feat/tasks.md\n"
        "@@ -1,2 +1,2 @@\n"
        "-- [ ] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
        "-- [ ] T013 [US1] Anything else\n"
        "+- [x] T012 [P] [US1] Renderer (scope: src/widget/**)\n"
        "+- [x] T013 [US1] Anything else\n"
    ) + _file_diff("src/widget/render.py") + _file_diff("src/core/db.py")
    assert scope_check(diff).violations == ("src/core/db.py",)


def test_a_dispatched_scope_binds_even_when_the_worker_never_checks_its_row():
    """The fan-out lane case: the host pinned the contract, so skipping the plan
    bookkeeping cannot be a way out of it."""
    diff = _file_diff("src/widget/render.py") + _file_diff("src/core/db.py")
    check = scope_check(diff, ("src/widget/**",))
    assert check.consulted
    assert check.violations == ("src/core/db.py",)


def test_an_empty_dispatched_scope_leaves_the_increment_unconsulted():
    diff = _file_diff("src/core/db.py")
    assert not scope_check(diff, ()).consulted
    assert not scope_check(diff, None).consulted


def test_scope_check_never_raises_on_hostile_input():
    for junk in (None, 12345, object()):
        assert isinstance(scope_check(junk), ScopeCheck)  # type: ignore[arg-type]
