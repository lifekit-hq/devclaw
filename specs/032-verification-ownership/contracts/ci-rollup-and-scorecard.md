# Contract — the CI rollup fact and the interventions metric

## `check_pr(repo_url, branch) -> RemoteChecksResult` (`devclaw/goal/remote_checks.py`)

Keyed like `merge_on_close.attempt_merge` — the goal's repo and its cumulative
goal branch (the seam's arity is unchanged, so the injected fake is too).
Reads, in order, each under the shared 20 s bound and never raising:

1. `gh pr view <branch> --repo <owner>/<repo> --json url,headRefOid,baseRefName,statusCheckRollup`
   — a "no pull requests found" reply is `no_pr` (a no-change goal: proceeds).
2. `gh api repos/<owner>/<repo>/branches/<base>/protection/required_status_checks/contexts`
   — HTTP 404 ⇒ no protection ⇒ every context in the rollup is required.
3. Only when the rollup is empty: `gh api .../contents/.github/workflows?ref=<base>` to tell
   `no_workflows` (no CI definition) from `pending` (CI defined, nothing reported yet).

Fold (`combine_states`, pure): filter rollup contexts to the required names (or all);
`failing` if any required context has a bad conclusion; else `pending` if any is
queued/in progress; else `passing` if at least one required context completed; the
rollup carries no workflows ⇒ `no_workflows`; only `startup_failure` conclusions ⇒
`infra_broken`; any read error ⇒ `unknown`. Returns `state`, `head_sha`, `failing_names`,
`pending_names`, `detail`.

Injected seam: `RemoteChecker = Callable[[str, str], Awaitable[RemoteChecksResult]]` on
`TickContext.remote_checker` — unchanged arity; `FakeRemoteChecker` in
`tests/test_goal_tick.py` gains a mutable `result`. `RemoteChecksResult.proceeds` is
`state in ("passing", "no_pr")`.

## Where the fact is consulted (all zero-token)

| site | on `passing` | on `failing` | on `pending` / `unknown` | on `no_workflows` | on `infra_broken` |
|---|---|---|---|---|---|
| `_open_done_gate`, before `prepare_ws` and the review dispatch | set `ci_green_head`, proceed | machine steering `[remote-checks] …` (source `auto-ci`), `RESUME_IDLE`, no round counted | `BLOCK mechanical:ci`, `pending_done_proposal=1` | typed Problem `kind=env` (supply the CI definition / cancel; timebox 0 = parks) — the admission-time `ci:definition` capability (US2) is the primary guard, this is the backstop | typed Problem `kind=env` (supply / cancel; parks) |
| `_autoheal_ci` (blocked branch, once per `next_heal_at`) | `_heal_unblock`, proposal stays pending | steering + unblock | stay blocked, zero cost | n/a | n/a |
| `_resolve_done_gate` before `_attempt_merge`, and `_finalize_pending_merge` | merge iff `head_sha == ci_green_head` | re-hold `mechanical:ci` on the current head (the round re-opens) | re-hold | n/a | n/a |

Retired: the post-evaluator block at `tick_donegate.py:433-493`, `CI_GATE_MODE`,
`DEVCLAW_GOAL_CI_GATE`, `check_branch`.

## Admission (spec 030 capability set)

`ci:definition` — implicit for every registered project; green when the default branch
carries at least one `.github/workflows/*.yml`; red ⇒ `mechanical:env` hold with remedy
"onboarding writes the CI workflow"; probed on the sweep cadence, TTL-cached.

## Scorecard block (`compute_scorecard`, `get_scorecard_metrics`, CLI `scorecard`)

```json
"interventions": {
  "window_hours": 168,
  "steers": 3, "resumes": 1, "decisions": 2, "non_worker_commits": 4,
  "achieved_goals": 12,
  "per_achieved_goal": 0.83,
  "items": [{"goal_id": "fs-421-…", "verb": "steer", "ref": "42", "made_at": "…"}, …],
  "note": ""
}
```

`per_achieved_goal = (steers+resumes+decisions+non_worker_commits) / achieved_goals`;
`null` when `achieved_goals == 0`. Source table `goal_interventions` (data-model §5);
degrades to a `note` on `OperationalError`, never raises (the `steering_note` precedent).
