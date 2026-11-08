# TinySpec: the diagnosing-bugs skill as an opt-in session plugin

**Branch**: fm/pk-r3-skill-trial
**Date**: 2026-11-08
**Status**: in review
**Complexity**: small (one env var, one argv flag, one prompt sentence, one image COPY, one skill file moved)
**Issue**: #1007 — Step 0 and Step 1; Step 2 (the `shakedown-skill-trigger` goal) runs after this ships and the image is deployed

## North-star case

- **Failure moved**: *ran and produced garbage*, if the skill fires. Today's
  evidence names no stop caused by missing diagnosis, so this ships as an
  opt-in trial, not a default.
- **Number that shows it**: in the shakedown trial, `Skill` tool uses of
  `devclaw-skills:diagnosing-bugs` per session, and `VerifyResult` rounds to
  green, arm B against arm A. Pass bar in #1007: B fires in at least 4 goals
  that went red, rounds-to-green not above A, tokens per goal within +10%,
  zero extra stops.
- **Cut when**: the trial shows the skill does not fire or does not help —
  the plugin, the env var and the sentence are removed together.

## What

- `deploy/plugin/` is a one-skill Claude Code plugin (`devclaw-skills`,
  skill `diagnosing-bugs`, MIT, attribution line kept). The Dockerfile COPYs it
  to `/opt/devclaw/plugin/`. Nothing is written into the shared
  `CLAUDE_CONFIG_DIR`.
- `DEVCLAW_PLUGIN_DIR` (unset by default) adds `--plugin-dir <path>` to
  `claude -p` and one sentence to step 1 of `session.md`. Unset, the argv and
  the prompt are what they were: one image serves both arms.
- `devclaw/skills/fix_bug/10-diagnosis-loop.md` (moved into the plugin as the
  skill body) and `fix_bug/00-scope.md` are retired: `_skill_paths` loads
  `fix_bug/*.md` only for kind `fix_bug`, which the goal layer never spawns
  (last task 2026-08-27). Their two tests go with them.

## Context

**Step 0 (prerequisite) — PASSED.** In the running container
(`devclaw-devclaw-mcp-1`, `claude` 2.1.318, user `node`), one
`claude -p "reply with the single word ok" --output-format stream-json
--verbose --plugin-dir <tmp one-skill plugin> --max-turns 1` call exited 0
with result `ok`. The `system/init` frame listed `devclaw-skills:diagnosing-bugs`
among 53 skills (52 without the plugin) and the plugin as
`devclaw-skills@inline`. The probe plugin lived in a `mktemp` dir inside the
container and was deleted afterwards. The skill is namespaced, so trial queries
match the suffix `:diagnosing-bugs`.

The goal layer stays at its 1,500-line ceiling: the sentence lives in
`config.diagnosis_hint()`, not in `devclaw/goal/`.

## Done-when

- Step 0 result is in the PR description. ✔
- Image carries the plugin at `/opt/devclaw/plugin/`; `DEVCLAW_PLUGIN_DIR` unset ⇒ argv and prompt unchanged. ✔
- The two dead-wired `fix_bug` skill files are gone. ✔
- `tests/test_engine_skills.py::test_plugin_dir_env_alone_separates_the_arms` pins the arm separation.
