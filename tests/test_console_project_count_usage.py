"""Regression: Overview's project-count tile must source from fetchProjects(),
not from distinct projectId values over the goal rows.

A project with zero goals is absent from the goal rows, so the old set-dedup
approach silently undercounts projects.  This test pins the correct source so
the two counts can never diverge again.
"""

from pathlib import Path

_OVERVIEW_SRC = (
    Path(__file__).resolve().parents[1]
    / "devclaw" / "server" / "console" / "src" / "pages" / "Overview.tsx"
)


def test_overview_project_count_matches_projects_source():
    """Overview.tsx must derive its Projects tile from fetchProjects(), not from
    a Set over goal rows — a project with zero goals would otherwise be dropped."""
    src = _OVERVIEW_SRC.read_text()

    # Must import and call fetchProjects()
    assert "fetchProjects" in src, (
        "Overview.tsx must import fetchProjects from ../api"
    )

    # Must NOT use the old set-dedup pattern over goal rows
    assert "new Set(" not in src, (
        "Overview.tsx must not derive project count via new Set(...) over goal rows; "
        "use fetchProjects() so zero-goal projects are counted"
    )
    assert ".map(g => g.projectId)" not in src and ".map((g) => g.projectId)" not in src, (
        "Overview.tsx must not map goal rows to projectId for the count; "
        "use fetchProjects() instead"
    )
