# no-mistakes inside the devclaw container

How the pipeline a goal's branch ships through (`no-mistakes`, #953) is wired
into the `devclaw-mcp` container, how to prove it from a fresh container, and
how to undo it. Failure it closes: *stopped when it shouldn't* — on 2026-11-05
the first goal in the container ended `BLOCKED` ("no-mistakes is not
initialized on this host (empty repo registry, daemon stopped)") because
nothing in the container held a gate, and what little it held was lost on
every redeploy.

## What is wired

| Piece | Where | Why |
|---|---|---|
| the binary, pinned and checksum-verified | `deploy/Dockerfile` (`NO_MISTAKES_VERSION`) | unchanged — already in the image |
| the home: gates (`repos/`), run history (`state.sqlite`), `config.yaml`, daemon files | `NM_HOME=/var/lib/devclaw/no-mistakes`, set in `deploy/Dockerfile` (`ENV`) and `deploy/docker-compose.devclaw.yml` | `/var/lib/devclaw` is the external `devclaw-state` volume, so a recreate keeps it. The default `~/.no-mistakes` is the container's writable layer: gone on every redeploy. |
| a gate per project, created before the session starts | `devclaw/engine/claude.py` (`_init_pipeline`, first thing in `run_claude` for a code task that hands off) | a session that finds no gate reads the hand-off as impossible and ends `BLOCKED`. `no-mistakes init` is idempotent; run in a leased treehouse worktree it registers the project checkout, so one gate serves every goal of the repo. A pipeline that cannot start fails the row `INTERRUPTED` before a session is spent. |
| the daemon, started by that `init` under the host env | `devclaw/engine/claude.py` (`host_env`) | the daemon pushes and opens the PR, so it needs `GH_TOKEN`; a session's env is stripped of it (`credentials.session_env`). `_hand_off` re-runs `init` before the ship turn in case the daemon died during the session. |
| stale-daemon cleanup on every container start | `deploy/entrypoint.sh` | the daemon dies with its container but leaves `daemon.pid` / `daemon.lock` / `socket` on the volume. no-mistakes trusts a live pid; pids restart from 1 in a new container, so the recorded one is soon some unrelated process and every `no-mistakes` call answers "connection refused". The entrypoint removes those three files and nothing else. |

Credentials: nothing new. `GH_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN` arrive from
the on-box secrets file (`env_file` in the compose file) and `git` resolves
through the existing `GIT_CONFIG_*` `gh auth git-credential` helper; the daemon
inherits both from the server process. No secret is in the image, the volume
layout above or a log.

Side effect of `init` to know about: it installs the `/no-mistakes` skill into
`~/.claude/skills/` and `~/.agents/skills/` (the host's Claude home is
bind-mounted, so `/home/lifekit/.claude/skills/no-mistakes` appears on the box)
and a user systemd unit file in the container's ephemeral home. Harmless.

## Prove it from a fresh container (no secrets, no goal)

Uses the deployed image and a scratch directory standing in for the volume.
Nothing here touches the live container or volume.

```bash
IMG=ghcr.io/lifekit-hq/devclaw-mcp:<sha>      # the image to check
VOL=$(mktemp -d); docker run --rm --user root -v $VOL:/v $IMG sh -c 'chown 1000:1000 /v'
RUN="docker run --rm -v $VOL:/var/lib/devclaw --entrypoint sh $IMG -c"

# 1. container A: a project with an origin, a leased worktree, a goal branch, then init
$RUN '
  export NM_HOME=/var/lib/devclaw/no-mistakes TREEHOUSE_ROOT=/var/lib/devclaw/workspaces/.treehouse
  git config --global user.email a@b.c; git config --global user.name t
  git init -q --bare -b trunk /var/lib/devclaw/o.git
  git clone -q /var/lib/devclaw/o.git /var/lib/devclaw/workspaces/proj; cd /var/lib/devclaw/workspaces/proj
  git checkout -q -b trunk; echo hi > f; git add f; git commit -qm init; git push -q origin trunk
  WT=$(treehouse get --lease --json --lease-holder devclaw:g1 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin)[\"path\"])")
  cd "$WT"; git checkout -q -b goal/g1; echo two >> f; git commit -qam two
  no-mistakes init | grep -E "Gate|gate"; no-mistakes daemon status | tail -1; no-mistakes axi status | head -3'

# 2. container B (the redeploy): same volume. The gate and the remote survive; the daemon does not.
#    Stale files from A's daemon are cleared by the entrypoint, so `init` brings it back.
docker run --rm -v $VOL:/var/lib/devclaw -e NM_HOME=/var/lib/devclaw/no-mistakes $IMG sh -c '
  ls $NM_HOME/repos; no-mistakes daemon status | tail -1
  cd $(ls -d /var/lib/devclaw/workspaces/.treehouse/.treehouse/proj-*/1/proj)
  git remote -v | grep no-mistakes | head -1; no-mistakes init | grep -E "Gate"; no-mistakes daemon status | tail -1'
```

Expected: A prints `Gate initialized`, `daemon running`, and `axi status`
naming `current_branch: goal/g1`; B lists the same gate directory, reports the
daemon not running before `init` and running after, and still shows the
`no-mistakes` remote. (Run B without `--entrypoint sh`, as above, so the
entrypoint executes; with a stale `daemon.pid` pointing at a live process the
same `init` answers `connection refused` — that is the failure the entrypoint
removes.)

The stubbed tripwires: `tests/test_handoff_fails_closed.py` (the pipeline is
initialised before the first turn; a dead pipeline spends no session) and
`tests/test_no_mistakes_state_survives_redeploy.py` (compose names one
`NM_HOME` on the state volume; the entrypoint clears only the
daemon files).

## Check the live instance (read-only)

`devclaw doctor` reports the hand-off too — `no-mistakes` on PATH (a `fail`
naming this runbook when it is missing, like a missing `claude` or
`treehouse`) and whether `$NM_HOME` exists (a `warn` until the first `init`
creates it) — so a host that cannot ship a branch is a finding instead of a
goal that stops at the hand-off.

```bash
docker exec devclaw-devclaw-mcp-1 sh -c 'echo $NM_HOME; ls $NM_HOME/repos; no-mistakes daemon status | tail -1'
```

Right after a deploy `$NM_HOME` exists only once a goal has run (the first
`init` creates it) and the daemon is "not running" until then; that is normal.
After a goal's session: one `repos/<hash>.git` per project it worked on, the
daemon running, and `docker exec … no-mistakes runs` (from the project
checkout) listing the goal's run.

## Undo

- **Stop using the volume home**: remove the `NM_HOME` line from
  `deploy/docker-compose.devclaw.yml` and the `ENV` line from
  `deploy/Dockerfile`, redeploy. The home reverts to `~/.no-mistakes` in the
  container's layer (state lost on every redeploy again, the original defect).
- **Drop the state** (the gates are rebuilt by the next goal's `init`; only run
  history is lost): `docker exec devclaw-devclaw-mcp-1 sh -c 'no-mistakes daemon stop; rm -rf "$NM_HOME"'`.
  Each project's checkout keeps a `no-mistakes` git remote pointing at the
  removed gate until the next `init` repairs it; `no-mistakes eject` in the
  checkout removes it.
- **Roll back the image**: `bash deploy/deploy-devclaw.sh <prior-sha>`
  (see [`devclaw-self-deploy.md`](./devclaw-self-deploy.md) §5). A prior image
  without the entrypoint simply leaves stale daemon files in place.
