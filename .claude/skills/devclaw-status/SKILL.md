---
name: devclaw-status
description: Produce a morning status digest of the live devclaw instance — every durable goal's phase/lifecycle, anything blocked or awaiting Denys, recent deliveries, the deduplicated problems catalog, and the nightly cycle reports (clean-cycle / mechanism-wedge history). Reads live state through the devclaw MCP (pointed at the VPS). Use whenever Denys asks "what's the status of goals", "any problems", "what's blocked", "what needs me", "how are the goals doing", "did last night run clean", "give me the devclaw digest", "/devclaw-status", or any morning/standup check on the running instance — even if he doesn't say "digest". This is the read-only "what's going on right now" surface; it never steers, resumes, cancels, or edits a goal.
---

# devclaw-status — the morning digest of the live instance

Denys asks this almost every day: *what's the status, what's on fire, what needs
me?* This skill answers it in one scannable pass over the live instance, ordered
so the thing that needs **him** is at the top and the healthy-and-running goals
don't bury it.

**Read-only.** This skill inspects; it never mutates. Steering, resuming,
answering, and cancelling are deliberate acts Denys does himself (or asks for by
name) — never fold them into a status read.

## Where the data comes from — and the one hard rule

Everything is read through the **devclaw MCP, which points at the VPS instance**.
The MCP tools you use:

| Tool | Gives you |
|---|---|
| `list_goals` | the spine — every goal's id, objective, phase, lifecycle, `blocked_on`, direction verdict, actions_dispatched, progress (last_at + stalled) |
| `get_goal(id)` | one goal in depth — done_when, backlog, `next`, in_flight, recent_log, firmed_draft (incl. unanswered `unknowns`) |
| `tail_goal(id)` | the deliveries tail (what each action shipped: summary + gate verdict + PR) and the live event stream — use only to explain a specific goal, not for every goal |
| `list_projects` / `project_status(id)` | the project rollup, if Denys wants it grouped by project |
| `list_problems` | the deduplicated problems catalog (message ×count) — **only if the tool exists** (see Problems below) |

There is **one exception** to "everything is an MCP tool": the **cycle reports**.
They are NOT exposed as an MCP tool — the only read surface is the HTTP route
**`GET /evals/cycles.json`** on the *same server the MCP tunnels to*. Derive its
base URL from the configured devclaw MCP url (in `~/.claude.json`,
`mcpServers.devclaw.url`, e.g. `http://127.0.0.1:28791/mcp`) by stripping the
trailing `/mcp`, then:
`curl -s --max-time 15 "<base>/evals/cycles.json?limit=14"`.
Each row is `{cycle_key, clean (0/1), wedges_json, pauses_json, summary, …}`;
the human-readable `summary` already carries the settled/done/failed line and
the needs-operator list — parse it, don't recompute. If the curl fails or
returns nothing, say the cycle-report endpoint wasn't reachable (same fail-loud
rule as a missing MCP) — don't invent a cycle history.

**Hard rule — never fabricate status.** If the devclaw MCP tools are **not
available in this session** (no `list_goals` in your toolset), the MCP isn't
wired to the VPS right now. Say exactly that and stop:

> The devclaw MCP (→ VPS) isn't connected to this session, so I can't read live
> goal state. Wire up the devclaw MCP pointing at the VPS and ask again.

Do **not** fall back to reading the local `./devclaw.db` — it's a stale dev
artifact and reporting it as live status is the exact "silent degradation" this
repo forbids. A digest that looks current but isn't is worse than no digest.

## Procedure

1. **Pull the spine.** Call `list_goals`. If it errors or the toolset lacks it,
   apply the hard rule above and stop.

2. **Triage each goal into one of three buckets** from its `list_goals` row —
   this ordering is the whole point of the digest:
   - **Needs Denys** — `blocked_on` is set, *or* `progress.stalled` is true, *or*
     the direction verdict is a stop-state (`stalled` / `needs_human`).
   - **Running** — an active `phase`/`lifecycle`, progressing, not blocked.
   - **Quiet** — idle / done / cancelled / nothing in flight and nothing pending.

3. **Deepen only the "Needs Denys" goals.** For each, call `get_goal(id)` to turn
   a terse `blocked_on` into an actionable line: what it's waiting on, and *how
   Denys clears it* (see the mapping below). If he asks "what's it actually
   doing" about a specific goal, `tail_goal(id)` for the deliveries/event tail —
   but don't tail every goal by reflex; it's heavy.

4. **Problems catalog.** If `list_problems` is in your toolset, call it and list
   the top entries as `×<count> <message>`, most-frequent first. If it is **not**
   exposed as an MCP tool, note that plainly:
   > Problems catalog: captured server-side (#260) and the `list_problems` read
   > tool is built (#262) but not yet live on this instance — the VPS hasn't
   > redeployed the new code, so there's no read surface here yet.
   Don't guess at problems from goal logs to fill the gap — say what's readable.

5. **Cycle reports.** `curl` the `/evals/cycles.json` endpoint (base-URL
   derivation above). Report the last ~14 nightly cycles as a clean/wedged
   history — **a cycle is `clean` iff zero mechanism-wedges fired in its
   22:00–05:00 Europe/London window** (ADR 0006). A *wedge* is the loop's own
   plumbing breaking — an engine error, a review-gate crash, a worker with no
   result line, a wall-clock timeout, a broken delivery, a `mechanical:*` block.
   A genuine `needs_answer` (human-gated by design) and a self-healed quota/auth
   pause are **clean**, not wedges — surface them under "needs operator" /
   "pauses" but never count them against the cycle. Lead with the **most recent
   cycle** (did last night run clean?) and the **rolling clean rate** over the
   window; then name the recurring wedge *classes* (e.g. `review_crash ×3`,
   `engine_error ×3`) so a systemic wedge is visible, not just last night's. If
   only a handful of cycles exist, say so — the mechanism is young and the rate
   is noisy.

6. **Optional project grouping.** Only if Denys asked for it, or there are enough
   goals that grouping helps, fold in `list_projects` / `project_status`.

7. **Render the digest** (next section). Keep it tight — this is a glance, not a
   report.

### How Denys clears a blocked goal — put the right verb on each blocked line

The point of the "Needs you" section is to hand him the next move, grounded in
what `get_goal` shows:

- Blocked in **FIRMING** with unanswered `unknowns` in `firmed_draft` →
  **`answer_unknowns`** (those answers can only come that way; steering won't do).
- Blocked awaiting a **direction/decision**, or the goal's on the wrong track →
  **`steer_goal`** (the direction-change verb).
- Blocked but the **blocker was cleared out-of-band** (a dependency landed, a
  mechanical block that didn't self-heal) → **`resume_goal`** (re-attempts the
  same contract, records no steering).

Name the verb; don't run it. Denys decides.

## The digest format

Lead with what needs him. Collapse the quiet goals. A calm instance should read
calm — don't manufacture urgency.

```
# devclaw status

## ⚠ Needs you (<n>)
- <objective, trimmed> — BLOCKED: <blocked_on, in plain words>
  → clear with `<answer_unknowns | steer_goal | resume_goal>` (<one-line why>)
- <objective> — STALLED: no progress since <when> · last verdict <verdict>

## Running (<n>)
- <objective> — <phase>/<lifecycle> · dir:<verdict> · in-flight:<tool or —> · dispatched <n> · last progress <ago>

## Problems
- ×<count> <message>
  …or the "not exposed over MCP" note.

## Cycle reports
- Last night (<cycle_key>): ✅ CLEAN — settled <n> (<d> done, <f> failed) · needs-op <n>
  …or ⚠️ <n> wedge(s): <class ×n, class ×n>
- Clean rate: <clean>/<total> cycles · recurring wedge: <class> (<n> nights)

## Quiet (<n>)
- <objective> — <lifecycle> (done / idle / cancelled)
```

Rules that keep it honest and scannable:
- **If "Needs you" is empty, say so first and plainly** — "Nothing needs you;
  everything's running or quiet." That's the most valuable possible answer and it
  should be unmissable.
- Trim objectives to one line; the id is available if he wants to drill in, but
  don't lead every line with a raw goal id.
- Prefer relative time ("2h ago", "since yesterday") over raw timestamps.
- Report exactly what the tools return. An empty instance = "No goals on the
  instance." Not an error, not an apology.

## Why it's shaped this way

- **Attention-first ordering** matches the actual question. Denys isn't asking for
  a database dump; he's asking "is anything waiting on me, and is anything on
  fire." Everything else is reassurance, so it goes below and stays terse.
- **Verb-per-blocker** turns the digest from a report into a control panel: each
  blocked goal already tells him the one move that unblocks it, grounded in the
  goal's real state rather than a guess.
- **Fail-loud on a missing MCP** mirrors the repo's core philosophy (loud failure
  over silent degradation). The stale local db is a trap; naming the disconnect
  is the correct, honest output.
- **Cycle reports answer the operator's real done-criterion** — "kick off a goal
  for the cycle and it runs without me." Goal state answers "what needs me right
  now"; the clean-cycle history answers "is the *loop itself* healthy, or is its
  own plumbing wedging night after night." A run of ⚠️ cycles with the same wedge
  class is the single most important reliability signal on the instance, so it
  earns its own section rather than being buried in the per-message problems
  catalog (which mixes plumbing wedges with by-design gate verdicts and human
  gates). The clean/wedge boundary is deliberately narrow: only mechanism
  breakage counts, so the rate doesn't get diluted by needs_answer gates and
  self-healed pauses that are the system *working*.
