# Contract: runner OOM evidence + shield env

The seam between layer 5 (runner, in-sandbox) and layer 4 (queue settle).

## Runner → queue (terminal result)

When the agent process dies and the cgroup `oom_kill` counter increased since
session start, the runner's `{"status": "error", "error": ...}` error string
BEGINS its OOM annotation with the exact marker `sandbox OOM-killed` followed
by `(cap=<cap>, oom_kill=<n>): <original error>`. The runner performs NO
in-runner session retry for this class (retrying in the same cgroup
reproduces the kill).

Absent evidence (cgroup unreadable, counter unchanged): error string is
unchanged from today — the queue then behaves byte-identically to current
behavior.

## Engine → sandbox (env declaration)

The engine forwards, for every sandbox launch, sourced from the same values
passed to `--memory`/`--cpus`:

```
DEVCLAW_SANDBOX_MEMORY=<effective memory, e.g. 4g>
DEVCLAW_SANDBOX_CPUS=<effective cpus, e.g. 2.0>
```

## Runner → agent (env allowlist additions)

| Var | Purpose |
|---|---|
| `DEVCLAW_SANDBOX_MEMORY` | cage visibility (US3) |
| `DEVCLAW_SANDBOX_CPUS` | cage visibility (US3) |
| `BASH_ENV=/opt/devclaw/oom-shield.sh` | every non-interactive bash the agent spawns self-raises `oom_score_adj` (US2) |

## Shield script (baked into the sandbox image)

`/opt/devclaw/oom-shield.sh`:

```bash
echo 800 > /proc/self/oom_score_adj 2>/dev/null || true
```

Runner-spawned workload (verify_cmd, hooks, mise) gets the same value via a
`preexec_fn` writing `/proc/self/oom_score_adj` before exec. The runner and
agent processes keep the default score (0); unprivileged processes can only
raise, which is sufficient — the killer's badness score treats adj≈800 as
+80% of total memory, dominating any realistic RSS difference.
