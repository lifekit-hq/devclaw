# Testing — how to run and write tests in this repo

The suite is **fully stubbed** — no docker, no `claude` binary, ~1150 tests in
~23s (pytest-xdist, `-n auto` in `addopts`). Anything needing real
docker/claude is an integration concern: `/live-shakedown`, never pytest.

**The suite is a TRIPWIRE NET, not a coverage instrument** (ruled by Denys
2026-08-29, the tests-to-tripwires prune). It exists to pin the invariants
that keep an *unattended autonomous loop* safe — not to pin every behavior.
The tripwire classes are: zero-token idle guards, fail-closed gates, the
CAS/single-writer state machine, OAuth stripping + the sandbox fence, the
pause-and-resume brakes (quota/auth pause, retry caps, watchdogs, admission,
resource release), the one-definition-of-change (materialize span), doctor
seeded-faults, and cheap structural guards (docs map, env-doc sync, tool
re-export, route shadowing, no-docker-in-tests). Ordinary feature behavior is
NOT unit-tested — the live instance, the done-gate, and post-merge review are
its regression surface, and cognition quality is measured by evals
(`tests/cognition/`, `evals/`), never by stubs.

## Running

- Always run with a private tmpdir: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q`.
  `/tmp/pytest-of-<user>` can be root-owned on this host (a past root-run
  pytest), which crashes every `tmp_path` fixture with the default basetemp.
- The suite runs **parallel by default** (`addopts = "-n auto"`). Add `-n0` when
  you need `pdb`, live output, or ordered failures — xdist swallows all three.
  A test that passes at `-n0` and fails at `-n auto` is a test with hidden
  shared state (a fixed path, a global, a port), not an xdist problem.
- **`ruff check .` AND `mypy` before you open a PR** — CI gates both (mypy
  config lives in pyproject `[tool.mypy]`; default strictness, zero-error
  baseline — ratchet up, never loosen). The rule set is narrow
  on purpose (`select = ["F", "E9"]` in `pyproject.toml`): pyflakes and syntax
  errors, not style. F821 is the one that earns its keep — it catches the name
  a refactor left dangling on a path no test executes.
- **In a git worktree, verify the import path FIRST**:
  `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` must print the
  WORKTREE path. The shared venv's editable install is a `.pth` pointing at the
  main checkout; `python -m pytest` from the worktree root wins only because cwd
  precedes site-packages — run from anywhere else and you silently test the
  wrong code.
- Run the full suite before opening any PR. Green baseline lives in the most
  recent PR descriptions; a lower count than baseline means you broke something
  even if your own tests pass.

## Writing

- **A PR ships a test ONLY when it touches a tripwire class** (the list in the
  header) or introduces a new invariant of that kind — then the test is named
  after the invariant, not the function. A PR changing ordinary behavior ships
  NO test; do not re-grow the suite out of diligence. (This replaces the
  pre-2026-08-29 "every behavior-change PR ships a named regression test"
  rule — ruled by Denys, tests-to-tripwires prune.)
- **The ratchet is symmetric** (ruled by Denys 2026-08-27): a PR that REMOVES
  behavior removes that behavior's tests in the same PR.
  (Load-bearing guards — zero-token, fail-closed — pin invariants, not
  instances; they stay until the invariant itself is repealed.)
- **Never mint an instance-test; strengthen the class test.** When a tripwire
  class is already pinned, extend the existing named test's cases (parametrize)
  instead of adding a sibling.
- Fixture map: `tests/goal_fakes.py` has `FakeClaude` (its `.calls` count IS the
  zero-token quota assertion), `FakeEngine`, `RecordingNotifier`, `seed_goal`.
  Goal-tick behavior → `tests/test_goal_tick.py`; transitions/CAS in isolation →
  `test_goal_transitions.py`; queue/gate → `test_review_gate*.py`,
  `test_task_retry.py`.
- Tests that build a "realistic repo" fixture copy the shape in
  `tests/test_review_gate.py` (real `git init` + .NET/Angular marker files) —
  don't invent a new fixture style.
- Zero-token guard tests (`FakeClaude.calls == 0` on idle/blocked paths) are
  load-bearing. If your change makes one fail, the change is wrong — never the
  test.
- Prompt-content tests assert both presence AND absence; when asserting a
  marker is absent from a prompt, first prove it's absent from the raw template
  (a template example like `Program.cs` is a canned prior, not grounding).
