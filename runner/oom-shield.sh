# OOM-killer shield (devclaw spec 020 US2). Sourced by every non-interactive
# bash in the sandbox via BASH_ENV: the shell marks ITSELF as the preferred
# OOM victim, so memory exhaustion kills the workload command (visible
# "Killed" the agent can adapt to) instead of the agent or runner sharing the
# same cgroup. Raising oom_score_adj is unprivileged; failures are swallowed
# because the shield must never break a command. Contract:
# specs/020-sandbox-oom-legibility/contracts/runner-oom-marker.md
echo 800 > /proc/self/oom_score_adj 2>/dev/null || true
