# doctor — the read-only post-deploy check

`doctor` (MCP) or `devclaw doctor` (CLI). Zero writes, zero cognition. Checks:

- **credentials** — every registry credential present, well-formed, and (the GitHub-shaped ones) accepted by GitHub right now;
- **tools** — `gh` and `git` on PATH, and (unless `DEVCLAW_ENGINE=stub`) the `claude` and `treehouse` binaries the engine runs;
- **handoff** — (unless `DEVCLAW_ENGINE=stub`) `no-mistakes` on PATH, a `fail` with the fix when it is not, and the state of its home (`NM_HOME`, default `~/.no-mistakes`): a `warn` while it is merely not created yet (the first goal's `no-mistakes init` creates it), a `fail` when `init` cannot create it — a relative `NM_HOME`, or a file or dangling symlink on the path ([runbook](./no-mistakes-in-container.md));
- **pause** — an active quota/auth pause and when it lifts;
- **goals** — every open goal's project checkout and its leased treehouse worktree (none until the first spawn);
- **projects** — every project has a `repo_url` and a `workspace_dir`;
- **database** — the file and its size.

Every non-ok finding names its remedy; doctor executes none. Exit code 1 on a
`fail` or `unknown` finding.
