"""Regression test: test_main_branch_guard.py must skip cleanly when .claude/ is
absent, both under full-suite collection (conftest.py collect_ignore) AND under
direct invocation (pytest tests/test_main_branch_guard.py).

This file is always collected — the guard test file itself cannot test its own
absence path. The regression runs pytest on a copy of the guard test in a temp
directory without .claude/, asserting exit 5 (1 skipped, 0 errors) rather than
the old exit 2 (1 collection error / FileNotFoundError).
"""

import shutil
import subprocess
import sys
from pathlib import Path


def test_guard_test_skips_cleanly_when_claude_absent(tmp_path):
    """pytest tests/test_main_branch_guard.py in a checkout without .claude/
    must skip cleanly (exit 5, "1 skipped"), not raise FileNotFoundError
    (exit 2, "1 error") — criterion 1 / clause 7 (spec 017)."""
    # Copy the guard test into a temp directory with no .claude/. The test file
    # resolves _HOOK as parents[1] / ".claude" / "hooks" / "main-branch-guard.py";
    # with __file__ at <tmp>/tests/test_main_branch_guard.py, parents[1] is <tmp>,
    # which has no .claude/ — so the module-level skip guard fires.
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    src = Path(__file__).parent / "test_main_branch_guard.py"
    shutil.copy(src, test_dir / "test_main_branch_guard.py")

    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         str(test_dir / "test_main_branch_guard.py"),
         "-v", "--no-header", "--tb=short"],
        capture_output=True, text=True, timeout=60,
        cwd=str(tmp_path),  # no pyproject.toml here → no -n auto from repo
    )
    output = result.stdout + result.stderr

    # Exit 5 = "no tests collected" (module skipped). Exit 2 = collection error.
    assert result.returncode == 5, (
        f"Expected exit 5 (1 skipped), got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "skip" in output.lower(), (
        f"Expected 'skip' in pytest output:\n{output}"
    )
    assert "FileNotFoundError" not in output, (
        f"FileNotFoundError must not appear — the skip guard must fire before _load():\n{output}"
    )
