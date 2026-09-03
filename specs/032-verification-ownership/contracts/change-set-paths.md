# Contract — change classification (the one definition of change, extended)

`devclaw/task_change.py` answers "what did the agent change?" once (spec 013). This spec
extends the answer with "and what kind of thing is each changed path".

## `ChangeSet.paths`

```python
@dataclass(frozen=True)
class ChangedPath:
    path: str          # repo-relative, rename target for R
    status: str        # A | M | D | R | T (git name-status)
    binary: bool       # numstat reported "-\t-"
    cls: str           # "product" | "gate_input" | "env_decl"
    in_scope: bool     # gate_input reclassified to product by the issue text

class ChangeSet:
    ...
    paths: tuple[ChangedPath, ...] = ()
    @property gate_input_paths -> tuple[str, ...]   # cls == gate_input and not in_scope
    @property binary_paths -> tuple[str, ...]
    @property env_decl_paths -> tuple[str, ...]
```

Produced only in `_capture_change` (`devclaw/queue/settle.py`) from
`git diff --numstat -M base..head` + `git diff --name-status -M base..head`, alongside the
existing diff text. `classify_path(path, *, hunk: str, in_scope: tuple[str, ...]) -> str` is
pure and lives in `task_change.py`.

## Classification tables (the only place they exist)

`GATE_INPUT_GLOBS`: `AGENTS.md`, `.github/workflows/**`, `.github/actions/**`,
`**/playwright.config.*`, `angular.json`, `**/jest.config.*`, `**/vitest.config.*`,
`**/karma.conf.*`, `pytest.ini`, `tox.ini`, `.pre-commit-config.yaml`, `.husky/**`,
`**/.npmrc`, `**/global.json`, `.tool-versions`, `.mise.toml`.
Content rule: a `package.json` hunk that adds a `preinstall`, `postinstall` or `prepare`
script key ⇒ `gate_input`.

`ENV_DECL_GLOBS`: `devclaw.json`, `.devcontainer/**`.

Everything else ⇒ `product`. Matching uses `devclaw/loom/diff_paths.path_in_scope`.

## In-scope declaration (from the issue, no new template)

Backticked tokens in the dispatch brief's issue section that match a `GATE_INPUT_GLOBS`
entry (a literal path such as `` `.github/workflows/backend.yml` `` or a glob such as
`` `.github/workflows/*` ``) mark matching changed paths `in_scope=True`. The extraction
reuses the brief text already handed to delivery (`deliver_change(goal=…)`).

## The gate

`_ChangeClassGate` (`gate_id = "change_class"`, member of `ALWAYS_HARD`), placed right
after `_MaterializeGate` in both gate chains (`settle.py` main and salvage). Verdict:

| condition | outcome |
|---|---|
| any `gate_input_paths` | fail: `change_class: gate-input edit(s) <paths> — a worker never edits a project's gate inputs; report BLOCKED: env — <item>, or raise the conflict as a contract block; a ticket about CI declares the path in scope` |
| any `binary_paths` | fail: `change_class: committed binary <paths> — binaries never ship from a sandbox` |
| `env_decl_paths` only | pass; goal-log line `env_declaration_changed: <paths>` |
| none | pass |

The failure text carries `_CHANGE_CLASS_MARKER` so the retry loop treats it like
`_PROMPT_TOO_LONG_MARKER`: fail closed, no retry, `mark_failed` names the paths and the
two legitimate moves.

## Consumers

- gate chain (above), `tests/test_materialize_gate.py` shape for its class test;
- delivery: `judged_head` unchanged; PR body gains nothing (a failing task never delivers);
- done-gate brief (`_done_gate_review_brief`) and `devclaw/prompts/goal-evaluator.md`: one
  rule each — AGENTS.md, CI configuration, test-runner configuration, install scripts and
  binaries are never evidence for a clause.
