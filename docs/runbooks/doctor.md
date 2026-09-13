# doctor — the read-only post-deploy check

`doctor` (MCP) or `devclaw doctor` (CLI). Zero writes, zero cognition. Checks:

- **credentials** — every registry credential present, well-formed, and (the GitHub-shaped ones) accepted by GitHub right now;
- **tools** — `gh`, `git`, and `docker` on PATH;
- **pause** — an active quota/auth pause and when it lifts;
- **goals** — every open goal's project checkout and goal checkout;
- **projects** — every project has a `repo_url` and a `workspace_dir`;
- **database** — the file and its size.

Every non-ok finding names its remedy; doctor executes none. Exit code 1 on a
`fail` or `unknown` finding.
