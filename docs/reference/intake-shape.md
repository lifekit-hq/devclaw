# The intake shape — `file_intake` (single intake doorway, stage 1)

The canonical narrative for how an ask enters devclaw. Enforcement lives in
code (`devclaw/intake.py`, surfaced as the `file_intake` MCP tool in
`devclaw/server/tools.py`); this page is the human-readable reference. The
direction it implements is locked in
[`../proposals/single-intake-doorway.md`](../proposals/single-intake-doorway.md).
There are deliberately **no per-repo issue templates** — the tool renders the
issue body from one place, so the shape cannot drift per repo.

## The rule

**Every ask from every source enters devclaw through `file_intake`.** Human or
agent, chat or Telegram or A2A — the ask becomes a labeled GitHub issue on the
target registered project's repo, and the returned issue URL is the asker's
durable receipt. `file_intake` can only create issues; turning an issue into
execution (stage 2) is a separate act by the authorized dispatcher (today:
Denys, via any interface), whose dispatch references the intake issue so the
delivery PR closes it.

Non-human askers never call dispatch tools. The mediating agent (e.g. the
OpenClaw devclaw agent receiving an A2A ask from Ledger) MAY call `file_intake`
without human confirmation — that is what the tool's bounded blast radius is
for — and returns the URL to the asker.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `project_id` | yes | A **registered project** (`list_projects`). The issue is filed on the registry row's `repoUrl` repo; an unknown project or a row without a GitHub `repoUrl` is rejected synchronously. |
| `what` | yes | The ask, one paragraph. Its first line becomes the issue title (`[intake] …`). |
| `done_when` | yes | Verifiable completion criteria, ≥ 20 chars (the goal-admission bar, applied at the doorway). |
| `asker` | yes | Who is asking (`denys`, `ledger`, …). **Recorded, not authenticated** — a claim devclaw stamps, not verifies. |
| `channel` | yes | `chat` \| `telegram` \| `a2a` \| `other`. |
| `context` | no | Evidence: where seen, repro steps, links. Rendered as `—` when omitted. |

Stamped server-side, never caller-supplied: the filing timestamp (UTC ISO) and
the `devclaw-intake` label (created idempotently on first use per repo).

## The receipt and its lifecycle

- **Filed** → the tool returns `{issue_url, project_id, repo}`. The URL is the
  receipt; a filing failure raises with an actionable message instead — there
  is no receipt unless the issue really exists.
- **Open** → the ask is pending. The asker follows up on its own cadence
  (pull; there is no push notification in P1).
- **Closed by a merged PR** → shipped. The dispatcher references the intake
  issue at dispatch so the delivery PR carries the closing reference.

## What this is not

Not a dispatch surface (stage 2 is human-gated; auto-pickup of
`devclaw-intake` issues is a named, scorecard-gated future upgrade). Not a
state store (issues are intent; execution state stays in SQLite — see the
single-writer invariant in [`../architecture.md`](../architecture.md)). Not an
LLM call — filing is mechanical (`gh` subprocess with a `GITHUB_TOKEN`
credential, never `ANTHROPIC_*`).
