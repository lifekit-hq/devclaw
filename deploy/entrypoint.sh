#!/bin/sh
# devclaw-mcp container entrypoint.
#
# The no-mistakes home (NM_HOME, on the `devclaw-state` volume) outlives the
# container; its daemon does not. A container that stops with the daemon alive
# leaves daemon.pid / daemon.lock / socket behind, and no-mistakes trusts a live
# pid: when that pid is reused by any process in the NEW container (pids restart
# from 1), every `no-mistakes` call answers "connection refused" and no hand-off
# can ship. A process starting here has no daemon of its own yet, so whatever
# the previous container left is stale by construction. Remove ONLY those three
# runtime files; the gates (repos/), run history (state.sqlite) and config.yaml
# are the state the volume exists to keep.
set -eu

if [ -n "${NM_HOME:-}" ]; then
  rm -f -- "$NM_HOME/daemon.pid" "$NM_HOME/daemon.lock" "$NM_HOME/socket"
fi

exec "$@"
