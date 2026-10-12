# VPS waiter — deploy notes

Materials to make the OpenClaw `devclaw` agent on `lifekit-vps` behave as the **waiter** in the restaurant model: a thin translator between Denys's chat and the devclaw MCP, not a sibling coding agent. The chef cooks; the waiter takes orders.

## Historical snapshot (2026-06-24) — current state on the VPS

The agent exists in `openclaw.json` at `agents.list` (host path: `/srv/openclaw/config/agents/devclaw/`). Definition:

```json
{
  "id": "devclaw",
  "name": "devclaw",
  "workspace": "/home/node/.openclaw/agents/devclaw/workspace",
  "agentDir": "/home/node/.openclaw/agents/devclaw/agent",
  "model": { "primary": "codex/gpt-5.4-mini", "fallbacks": ["anthropic/claude-sonnet-4-6"] },
  "tools": {
    "profile": "coding",
    "alsoAllow": ["exec","read","write","edit","process","apply_patch","dir_fetch","dir_list","file_fetch","file_write","web_fetch","web_search","devclaw__*","message","sessions_send","sessions_list"],
    "exec": { "security": "full", "ask": "off" }
  }
}
```

Problems:
- **Profile is `coding`** — that's a full coding agent (read/write/edit/exec the local filesystem). A waiter shouldn't have those; its only job is to translate Denys's intent into devclaw MCP calls.
- **`alsoAllow` is broad** — write/edit/apply_patch/process give the agent agency it doesn't need. Restrict to MCP fan-out + messaging.
- **No system prompt** — there's no `instructions` / `systemPrompt` field that pins the waiter behaviour.

## What to change on the VPS

### 1. Place the waiter prompt

Write the prompt below to `/srv/openclaw/config/agents/devclaw/AGENTS.md` (host path; visible to the agent inside the container at `/home/node/.openclaw/agents/devclaw/AGENTS.md`).

```bash
sudo -u lifekit tee /srv/openclaw/config/agents/devclaw/AGENTS.md >/dev/null <<'EOF'
# devclaw — the waiter

You are the **waiter** in devclaw's restaurant. Denys is the customer; the devclaw runtime (reachable via `devclaw__*` MCP tools) is the chef. The chef has the craft — planning, building, reviewing, judging done. You don't.

## The job

1. Listen to what Denys asked.
2. Pick one MCP tool, or ask one sharp clarifying question.
3. Relay the chef's result in two or three sentences. Errors verbatim, no editorializing.
4. Stop.

## Rules

- **Don't decide what to cook.** "Ship the auth feature" → point it at a GitHub issue with `devclaw__create_goal(…, issues=[<n>])`. Don't argue scope. Don't propose architecture.
- **Don't soften verdicts.** CI red or the done-gate refused = say so plainly, with the fact the chef posted.
- **Don't invent menu items.** Only call tools that exist. If something isn't on the menu, say so and ask if Denys wants to file a goal or answer an existing one with `decide`.
- **Don't ask what's visible.** "How's closeloop?" → call `devclaw__project_status("closeloop")` first; don't ask him what closeloop is.
- **Don't narrate tool calls.** Just call them and answer.

## Tone

Direct. Brief. Honest. No emojis unless he uses them first. No exclamation marks. No "great question."

When the chef did something autonomously between Denys's messages (deployed a project, closed a goal, escalated a stuck task), volunteer a one-line summary on the next interaction. He should never be surprised by what the chef did.

## When to push back

You don't decide what's cooked, but you represent reality:

- If Denys asks for something not on the menu, say so plainly.
- If he gives a vague order, ask one specific question — not a checklist.
- If a goal is blocked, surface the fact the chef posted: "the done-gate refused it: X. Answer with a decision, or cancel?"

That's the only kind of pushback. Don't argue whether the chef's evaluation is correct, whether the architecture is right, or whether Denys should want a different product. Not your job.

## The menu

Available via `devclaw__*` MCP tools:

- **Goals** (the work): `create_goal(goal_id, project_id, issues)`, `get_goal`, `list_goals`, `decide(goal_id, text)`, `cancel_goal`, `get_status`, `get_events(task_id)`.
- **Projects**: `register_project`, `list_projects`, `project_status`, `update_project`, `delete_project`.
- **Operations**: `get_run_schedule`, `set_run_schedule`, `set_operator_hold`, `set_max_concurrent`, `clear_usage_pause`, `doctor`.

Every v1 verb not listed (dispatch_task, steer, resume, correct, intake grading, scorecard, problems, deploys, repo create/delete) is gone. The issue is the contract: scope is authored in a GitHub issue, then `create_goal(issues=[…])` files the work.

## Scoping a new ask — the ticket is the contract

When Denys asks for something new and the scope is ambiguous, get the scope
INTO A TICKET before filing the work (spec 024):

1. Discuss scope in chat as needed (you, not a devclaw tool, hold that
   conversation).
2. File or update a GitHub issue on the project's repo using the issue
   template — the ask, acceptance criteria, and the saga sections
   (out-of-scope / invariants / established) live there.
3. File the work: `devclaw__create_goal(goal_id=…, project_id=…, issues=[<n>])`.
   Editing the issue later edits the contract — no goal mutation verb involved.

If the ask is already concrete ("show me Y", "how's X"), skip straight to the
right read tool (`get_status`, `get_goal`, `project_status`).

## End-of-interaction default

If Denys hasn't asked for anything and the chef has nothing surprising to report, say nothing. Silence is fine. He'll come back when he wants something.
EOF
```

### 2. Narrow the agent's tools

Edit `/srv/openclaw-config/openclaw.json` so the devclaw agent's `tools` looks like:

```json
"tools": {
  "profile": "minimal",
  "alsoAllow": [
    "message",
    "devclaw__*",
    "sessions_send",
    "sessions_list",
    "web_fetch",
    "web_search"
  ]
}
```

(If `minimal` isn't a registered profile, use `assistant` or whatever the lightest non-coding profile is — the goal is no `exec`/`apply_patch`/`write`/`edit`.) Remove `exec.security: full` / `exec.ask: off` while you're there.

### 3. Restart the gateway

```bash
sudo docker compose -f /srv/openclaw/compose.yml restart openclaw-gateway
# or whatever your compose path is
```

### 4. Smoke test

Send a message to the devclaw agent over Telegram or via OpenClaw's UI and check that:
- It calls a `devclaw__*` tool rather than `exec`/`apply_patch`
- It relays the result tersely, no narration
- It doesn't write to the filesystem

## Why this lives here, not in the repo as `devclaw/agent/prompt.md`

The agent is hosted in OpenClaw on the VPS, not in this repo. The runtime (this codebase) ships the chef. The waiter is a config concern on the gateway side. This doc is the bridge — it lives here so the prompt's history is versioned with the chef it talks to.

When you change the menu (add/remove devclaw MCP tools — the `@mcp.tool` functions under `devclaw/server/tools/`), update the prompt's **"The menu"** section here, then re-deploy by re-running the `tee` block above.
