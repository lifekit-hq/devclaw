# Feature Specification: Auth Session Refresh

**Feature Branch**: `004-auth-session-refresh`

**Created**: 2026-08-14

**Status**: Draft

**Input**: User description: "devclaw should refresh its own claude OAuth access token instead of pausing the whole instance and paging the operator to SSH in — plus a way to re-login over MCP for the rare dead-session case. OAuth-only."

## Problem Statement

devclaw's cognition is `claude` over Pro/Max OAuth. The credential
(`~/.claude/.credentials.json`) is bind-mounted **read-only** into every sandbox.
OAuth **access** tokens expire every few hours; the **refresh** token is
long-lived.

Today, when a worker or cognition call hits `401 OAuth access token has expired`,
`devclaw/loom/limits.py` classifies it as an **AUTH pause**: account-wide pause,
an actionable "re-login needed" owner ping, a fixed 2-hour re-probe
(`AUTH_PAUSE_S = 7200`), and auto-resume **only after a human re-logs in**. There
is **no auto-refresh attempt**.

Live diagnosis (2026-08-14): when this fired on the running instance,
`claude auth status` inside the container reported `loggedIn: true` (valid Max
session) — the login was **not** dead; only the short-lived access token had
lapsed, and the read-only sandbox mount can't persist a refresh. A single
host-side `claude --print "ok"` (creds are writable on the host) refreshed the
access token and cleared the pause. So today's AUTH pause is **mostly a false
alarm**: it pages the operator to SSH into the VPS and re-login when a cheap
host-side refresh would self-heal it. This was the single failure that silently
paused a whole night's run this session — the #1 recurring usability tax on the
live instance.

This feature makes devclaw **refresh its own OAuth session** in the common case,
and adds a way to **re-login over MCP** (no SSH) for the rare case the refresh
token itself is dead — all strictly OAuth-only.

## Clarifications

*(To be completed in `/speckit-clarify`. The specify author flagged the open
questions inline as `[NEEDS CLARIFICATION]`.)*

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Expired access token self-heals, no human, no pause (Priority: P1)

As the operator, when devclaw's `claude` **access** token expires mid-run (the
common case, the refresh token still valid), devclaw **refreshes the token
host-side and retries once**, so the work continues and the instance never
pauses or pings me. My night's run is not silently lost to a token that could
have refreshed itself.

**Why this priority**: This is the whole feature — it removes the recurring
false-alarm pause that is the top operational tax on the live instance.

**Independent Test**: Simulate a cognition/worker call that returns an
expired-access-token 401 once, then succeeds after a refresh; assert devclaw
performed a refresh, retried once, the call succeeded, and **no AUTH pause was
recorded and no owner ping fired**.

**Acceptance Scenarios**:

1. **Given** a cognition/worker call that fails with an expired-access-token 401
   and a **valid** refresh token, **When** devclaw handles it, **Then** it
   attempts a host-side OAuth refresh, retries the call **once**, the retry
   succeeds, and **no AUTH pause / no owner ping** occurs.
2. **Given** the refresh succeeds, **When** the next task dispatches, **Then** it
   mounts the freshly-refreshed token (per-task `docker run --rm` remount) and
   runs normally — the read-only sandbox mount is not modified.
3. **Given** the retry **still** returns an expired/`401`, **When** devclaw
   handles it, **Then** it does not loop — one refresh + one retry, then fall
   through to the AUTH pause path (US2).

---

### User Story 2 - Genuinely dead session still pauses loudly (Priority: P1)

As the operator, when the **refresh token itself** is dead/revoked (a real
re-login is required), devclaw **pauses and pings me exactly as today** — the
loud-failure posture is preserved; the new refresh attempt only precedes it.

**Why this priority**: The change must not weaken the fail-loud guarantee. A
dead session is a real human-action condition and must stay legible.

**Independent Test**: Simulate a 401 where the host-side refresh **also** fails
(dead refresh token); assert devclaw records the AUTH pause and fires the
actionable "re-login needed" owner ping, byte-for-byte with today's behavior.

**Acceptance Scenarios**:

1. **Given** an expired-access-token 401 **and** a refresh that fails, **When**
   devclaw handles it, **Then** it records the account-wide AUTH pause + the
   actionable owner ping, with the same wording/cadence as today.
2. **Given** any **non**-auth pausing failure (quota / rate-limit), **When**
   devclaw handles it, **Then** the refresh path is **not** engaged — those
   classify and pause exactly as today (no spurious refresh attempts).

---

### User Story 3 - Re-login over MCP, no SSH (Priority: P2)

As the operator, when a full re-login **is** required, I can complete it from
chat (the OpenClaw waiter / an MCP client) instead of SSHing into the VPS: a
`refresh_auth` call returns the OAuth login URL, I authorize in my browser, and a
`complete_auth` call with the pasted code finishes the login and **clears the
AUTH pause immediately**.

**Why this priority**: It removes the SSH step for the rare dead-session case,
but the P1 auto-refresh already makes the *common* case hands-free, so this is
the second slice.

**Independent Test**: With the instance in an AUTH pause, call `refresh_auth`
(returns a URL), then `complete_auth(<code>)`; assert the pause clears and
dispatch resumes — without any SSH.

**Acceptance Scenarios**:

1. **Given** an AUTH-paused instance, **When** `refresh_auth()` is called,
   **Then** it returns the OAuth login URL and does not itself clear the pause.
2. **Given** a valid pasted code, **When** `complete_auth(code)` is called,
   **Then** the login completes, the host creds are written, the AUTH pause
   clears, and dispatch resumes.
3. **Given** an invalid/expired code, **When** `complete_auth` is called,
   **Then** it fails legibly (the pause stays; the operator can retry) — no
   partial/corrupt credential state.

---

### Edge Cases

- **Refresh succeeds but the retry hits a *different* failure** (quota, network):
  that failure is classified on its own merits — the refresh doesn't mask it.
- **Concurrent 401s** (many in-flight calls expire at once): the refresh must be
  **single-flighted** — one refresh, not N concurrent refreshes racing to
  rewrite the credential. [NEEDS CLARIFICATION: is a simple host-side lock /
  single-flight guard sufficient, or must the refresh be serialized through one
  owner (e.g. the heartbeat) to avoid credential-file write races?]
- **Refresh mechanism**: the host-side refresh is an OAuth token-endpoint /
  `claude`-driven refresh that writes the host `~/.claude/.credentials.json`.
  [NEEDS CLARIFICATION: perform the refresh by invoking the host `claude` (e.g. a
  minimal call that triggers its built-in refresh) vs calling the OAuth
  token-refresh endpoint directly with the stored refresh token — which is the
  supported, stable seam?]
- **Distinguishing expired-access-token from dead-session**: the classifier must
  separate "access token expired, refreshable" from "session dead, re-login
  required" so US1 self-heals and US2 pauses. `claude auth status` (`loggedIn`)
  is the ground-truth signal for the latter.
- **`complete_auth` interactivity**: a full re-login is inherently a two-step,
  browser-involved flow (URL → authorize → paste code); the MCP tool cannot
  complete a browser OAuth by itself (recorded as an accepted limitation).
- **Idle/blocked ticks**: the refresh only fires in the failure path of a call
  that was already being made — never as new work on an idle/blocked tick.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: On an **expired-access-token 401** from a cognition or worker call,
  devclaw MUST attempt a **host-side OAuth refresh** (using the stored refresh
  token) and **retry the failing call once** before classifying it as an AUTH
  pause.
- **FR-002**: If the refresh **and** single retry succeed, devclaw MUST continue
  normally with **no AUTH pause recorded and no owner ping fired**. Subsequent
  tasks pick up the refreshed token via their normal per-task credential remount;
  the read-only sandbox mount is **not** modified.
- **FR-003**: If the host-side refresh **fails** (dead/revoked refresh token),
  devclaw MUST fall through to **today's AUTH pause + actionable owner ping**,
  unchanged in wording and re-probe cadence. The loud-failure posture is
  preserved.
- **FR-004**: The refresh path MUST engage **only** for the expired-access-token
  class — **not** for quota, rate-limit, or any non-auth pausing failure, which
  classify and pause exactly as today.
- **FR-005**: The refresh MUST be **single-flighted** — a burst of concurrent
  401s triggers **one** refresh, never N racing writes to the credential file.
- **FR-006**: The refresh MUST be **OAuth-only**. `ANTHROPIC_API_KEY` /
  `ANTHROPIC_AUTH_TOKEN` remain actively stripped at every spawn site; the
  refresh and MCP tools refresh the OAuth **session** only and MUST NOT accept,
  read, or introduce an API key or bearer token.
- **FR-007**: The zero-token idle guard MUST hold — the refresh fires only inside
  the failure path of an in-progress call, adding **no** LLM/subprocess work on
  an idle or blocked tick. The refresh itself is a token-endpoint/`claude`
  refresh, not a metered model call. All `FakeClaude.calls == 0`
  idle/blocked-path tests stay green.
- **FR-008** *(P2)*: devclaw MUST expose an MCP tool pair — `refresh_auth()`
  returns the OAuth login URL (without clearing the pause), and
  `complete_auth(code)` completes the login, writes the host credentials, and
  clears the AUTH pause — so a full re-login needs no SSH.
- **FR-009**: A failed or invalid `complete_auth` MUST leave the credential state
  intact (no partial/corrupt write) and the AUTH pause in place, so the operator
  can retry.

### Key Entities

- **OAuth credential** (`~/.claude/.credentials.json`, host): the access token +
  long-lived refresh token. Writable on the host, mounted read-only into
  sandboxes; refreshing rewrites it host-side.
- **Failure classification** (`loom/limits.py`): today AUTH / QUOTA / RATE /
  TRANSIENT / REAL. This feature adds an **expired-access-token → refresh-and-
  retry** decision **before** AUTH pause, distinct from a dead-session AUTH pause.
- **AUTH pause**: the existing account-wide pause + owner ping + re-probe —
  reached only when a refresh cannot save the session (unchanged).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An expired-access-token 401 with a valid refresh token results in
  **zero AUTH pauses and zero owner pings** — the run continues. Verified by a
  named regression test and, on the live instance, by the absence of
  refresh-triggered auth pauses in the cycle reports going forward.
- **SC-002**: A genuinely dead session (failed refresh) still produces the
  **same** loud AUTH pause + actionable ping as today — no regression to the
  fail-loud posture (named test asserts parity).
- **SC-003**: Operator effort for the common expiry drops from "SSH to the VPS +
  `docker exec … claude auth login`" to **nothing** (self-healed); for the rare
  dead-session case, from SSH to a **two-step MCP call** from chat.
- **SC-004**: No API-key path is introduced — the OAuth-only invariant holds
  (a test asserts `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` remain stripped and
  no refresh path consumes one).
- **SC-005**: Idle/blocked ticks still cost **zero** `claude` calls — the
  existing zero-token guard tests stay green.

## Assumptions

- The host `~/.claude/.credentials.json` is writable by the devclaw process
  (confirmed live: a host-side `claude --print` refreshed it) — host-owned
  refresh is the natural, safe seam.
- Each sandbox is `docker run --rm` with a fresh per-task credential mount, so a
  host-refreshed token propagates to the next task without touching the
  read-only mount.
- `claude auth status` (`loggedIn`) is a reliable ground-truth signal for
  "session dead vs access token merely expired".
- Companion/operator model is unchanged; this reduces operator toil, it does not
  change who owns the account.

## Constitution Impact

Believed **additive — no amendment required**. Principle I (OAuth-only) is
**reinforced**, not changed: the refresh operates on the OAuth session, never an
API key, and the strip-at-every-spawn-site guard is untouched. Principle VI
(loud failure over silent degradation) is preserved — a genuinely dead session
still pauses loudly; the feature only prevents the *false* alarm. Principle III
(zero-token idle) holds (FR-007). The spec asserts this; `/speckit-clarify`
confirms it, and if any invariant text should name the auto-refresh behavior,
that edit ships in the same arc.

## Direction Memory — Rejected / Deferred Alternatives

*(Per `.claude/rules/speckit-workflow.md`: the spec is the direction memory.)*

- **Rejected: make the sandbox credential mount writable so the worker refreshes
  in place.** The sandbox is untrusted; mounting a writable credential into it
  widens the blast radius. Host-owned refresh + per-task remount is safer and is
  already the natural seam.
- **Rejected: keep the pure pause + SSH-re-login model.** It's the false-alarm
  tax this whole spec exists to remove — a valid session shouldn't page a human.
- **Deferred (P3): adopt a long-lived token (`claude setup-token` +
  `CLAUDE_CODE_OAUTH_TOKEN`).** A complementary *reduce-frequency* measure (fewer
  expiries), but it doesn't remove the need for auto-refresh (tokens still
  rotate) and it's a lifekit-stack/compose env change, not a devclaw-code change.
- **Deferred (P2): the `refresh_auth`/`complete_auth` MCP tool pair.** The P1
  auto-refresh makes the common case hands-free; the over-MCP re-login only
  matters for the rare dead-session case and is a second slice.

## Slicing

- **P1 (firm — N PRs, sized in plan)**: the auto-refresh-before-pause layer
  (detect expired-access-token 401 → single-flighted host-side refresh → retry
  once → else today's AUTH pause), with named regression tests (self-heal
  without pause; dead-refresh still pauses+pings as today; non-auth pausing
  failures untouched; zero-token idle guard green; OAuth-only — no API-key path).
- **P2 (named, unsized)**: the `refresh_auth` / `complete_auth` MCP tool pair
  (re-login over the wire, no SSH).
- **P3 (named, unsized)**: adopt the long-lived `setup-token` env on the
  lifekit-stack side to cut expiry frequency.
