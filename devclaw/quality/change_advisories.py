"""Mechanical, zero-LLM advisories over the judged span (spec 013 US3).

These three checks used to live in ``runner/hooks/post-run.sh``, where they ran
``git diff <pre_head>`` inside the sandbox — a THIRD independent computation of
"what did the agent change?", with the same blindness as the gates had: a file
the agent never recorded was invisible to them. That is how a run which had just
created ``AGENTS.md`` reported *"AGENTS.md exists but was not updated this run"*
(#630).

They are relocated rather than taught the same trick twice: after materialization
the host holds the one complete artifact, so every advisory reads it. Nothing
here touches git — pure string work over a diff that is already in memory, plus
one ``os.path.isfile`` for "does this repo have a guide at all".

Advisory means advisory. Nothing here can fail a task; the output is warning
lines that ride the task result exactly where the hook's warnings did.
"""

from __future__ import annotations

import os
import re

from ..loom.declared_scope import changed_paths, path_in_scope

#: Browser-test spec files. A run that ADDS one while ``verify_cmd`` cannot run
#: it ships a test nothing executes (the cf-11 failure mode).
_SPEC_GLOBS = (
    "**/*.spec.ts", "**/*.spec.js", "**/*.spec.tsx",
    "e2e/**", "tests/e2e/**",
)

#: Web-UI SOURCE. Changing it without a browser run fails the host browser gate
#: CLOSED at settle, so say so at settle rather than leaving it to be inferred.
_UI_GLOBS = (
    "**/*.component.ts", "**/*.component.html",
    "*/src/app/**", "src/app/**", "angular.json",
)

#: Library surface is exempt from the browser gate (a library slice wires nothing
#: into a running app route), so it must not trigger the nudge either — the
#: cmn-tab-group diff blow-up, 2026-07-18.
_LIB_MARKER = "/src/lib/"

_PLAYWRIGHT_RE = re.compile(r"playwright|pytest-playwright", re.IGNORECASE)

#: ``diff --git a/x b/y`` followed by ``new file mode`` ⇒ the file was created.
_DIFF_HEADER_RE = re.compile(r'^diff --git "?a/(?P<a>.*?)"? "?b/(?P<b>.*?)"?$')

#: The repo guide the worker layer is asked to keep current.
_REPO_GUIDE = "AGENTS.md"


def added_paths(diff: str) -> "tuple[str, ...]":
    """Paths the span CREATED, read from ``new file mode`` markers. Total: an
    unparseable diff yields no additions rather than an exception."""
    out: "list[str]" = []
    current: "str | None" = None
    for line in (diff or "").splitlines():
        m = _DIFF_HEADER_RE.match(line)
        if m:
            current = (m.group("b") or m.group("a")).strip()
            continue
        if current and line.startswith("new file mode"):
            out.append(current)
            current = None
    return tuple(sorted(set(out)))


def _matching(paths: "tuple[str, ...]", globs: "tuple[str, ...]") -> "list[str]":
    return [p for p in paths if path_in_scope(p, globs)]


def change_advisories(
    diff: str, *, workspace_dir: str, verify_cmd: str = ""
) -> "list[str]":
    """Advisory warning lines for one judged span. Never raises."""
    try:
        return _advisories(diff, workspace_dir=workspace_dir, verify_cmd=verify_cmd)
    except Exception as err:  # noqa: BLE001 — advisories never fail a task
        return [f"warn: change advisories could not run: {err.__class__.__name__}: {err}"]


def _advisories(diff: str, *, workspace_dir: str, verify_cmd: str) -> "list[str]":
    touched = changed_paths(diff)
    if not touched:
        return []
    warnings: "list[str]" = []
    runs_browser = bool(verify_cmd) and bool(_PLAYWRIGHT_RE.search(verify_cmd))

    new_specs = _matching(added_paths(diff), _SPEC_GLOBS)
    if new_specs and verify_cmd and not runs_browser:
        warnings.append(
            "warn: new browser tests added but verify_cmd does not run them:\n"
            + "\n".join(f"  - {p}" for p in new_specs)
            + f"\n  verify_cmd: {verify_cmd}"
            "\n  fix: extend verify_cmd to include 'npx playwright test' (or equivalent)."
        )

    changed_ui = [p for p in _matching(touched, _UI_GLOBS) if _LIB_MARKER not in f"/{p}"]
    if changed_ui and verify_cmd and not runs_browser:
        warnings.append(
            "warn: web-UI source changed but verify_cmd runs no browser E2E — "
            "the browser gate will fail this CLOSED:\n"
            + "\n".join(f"  - {p}" for p in changed_ui)
            + f"\n  verify_cmd: {verify_cmd}"
            "\n  fix: add a Playwright spec that exercises this change in the running "
            "app, and extend verify_cmd to run 'npx playwright test --reporter=json' "
            "(see craft/playwright.md)."
        )

    # The repo guide. Reading the MATERIALIZED span is the whole point: a run
    # that created AGENTS.md for the first time is a run that updated it.
    guide_touched = _REPO_GUIDE in touched
    other_touched = any(p != _REPO_GUIDE for p in touched)
    if (
        not guide_touched
        and other_touched
        and os.path.isfile(os.path.join(workspace_dir, _REPO_GUIDE))
    ):
        warnings.append(
            f"warn: {_REPO_GUIDE} exists but was not updated this run; future "
            "agents may re-derive what you learned."
        )
    return warnings
