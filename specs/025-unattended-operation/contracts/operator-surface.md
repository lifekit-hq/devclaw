# Operator Surface Contracts: Unattended-Week Operation

## MCP verb: `set_quiet_mode` (in `devclaw/server/tools/control.py`)

```
set_quiet_mode(on: bool, until: str | None = None, reason: str = "") -> {
    "quiet": bool,
    "until": str | None,     # ISO timestamp or null (indefinite until disarm)
    "suppressed_so_far": int # rows in suppressed_pings since armed
}
```

- `on=true` arms; `until` (ISO date/datetime) sets self-disarm expiry —
  RECOMMENDED for a holiday window so a forgotten toggle can't mute forever.
- `on=false` disarms (deletes the meta key; the suppressed backlog persists).
- Idempotent; never errors on already-armed/already-off.

## MCP read: suppressed-ping backlog

Exposed through the existing observability surface (one addition):

```
get_status() gains: "quiet_mode": {"armed": bool, "until": str|null,
                                   "suppressed": int}
```

plus a bounded read returning the backlog rows in `ts_ms` order for the
catch-up digest (FR-014) — surfaced via `get_events`-style pagination, not a
new tool, if a natural fit exists; else one `list_suppressed_pings(limit)`.

## Notifier contract (internal, `devclaw/goal/notify.py`)

```
class Notifier(Protocol):
    async def send(self, text: str) -> bool: ...

class QuietNotifier:                # wraps any Notifier
    async def send(self, text) -> bool          # suppress+record while armed
    async def send_critical(self, text) -> bool # always delegates
```

Call-site contract: ONLY the instance-dead class calls `send_critical` —
today that is (a) the auth-pause owner ping when pause-and-resume cannot
heal (`tick.py` auth episode) and (b) the self-deploy rollback-failure path.
Adding a `send_critical` call site is a spec-level decision, not a
convenience.

## Deploy trigger contract (devclaw ↔ Actions)

- devclaw triggers: `gh workflow run deploy.yml -f tag=<sha> -f auto=true`
  once quiescent (`count_running() == 0`), never while a task runs.
- The workflow's auto lane runs `deploy/deploy-devclaw-auto.sh <sha>`:
  exit 0 = deployed + probe green; exit non-zero = rolled back (or rollback
  failed, in which case the script has already fired the relay ping —
  the instance-dead class — before exiting).
- Exactly one rollback attempt per deploy; no retry loops on the runner.

## Blocked-kind contract addition

`mechanical:merge_failed` joins the structured `blocked_kind` set:
human-gated (never auto-healed), `resume_goal` ⇒ retry the merge only,
`steer_goal` ⇒ normal re-direction (clears `pending_merge_pr` only if the
steering changes delivery expectations — default keeps it).
