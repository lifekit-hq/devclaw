"""The shared subprocess boundary (devclaw/procutil.py) — the one wrapper the
seven former per-module copies collapsed into. Pins the contract every caller
relies on: combined output, never raises, 127 on spawn failure."""

from devclaw.procutil import run


async def test_run_returns_exit_code_and_combined_output():
    rc, out = await run("sh", "-c", "echo to-stdout; echo to-stderr >&2; exit 3")
    assert rc == 3
    assert "to-stdout" in out and "to-stderr" in out


async def test_run_spawn_failure_returns_127_never_raises():
    rc, out = await run("definitely-not-a-real-binary-xyz")
    assert rc == 127
    assert "not runnable" in out


async def test_run_respects_cwd_and_env_extra(tmp_path):
    rc, out = await run("sh", "-c", "pwd; echo $PROCUTIL_PROBE",
                        cwd=str(tmp_path), env_extra={"PROCUTIL_PROBE": "hi"})
    assert rc == 0
    assert str(tmp_path) in out and "hi" in out
