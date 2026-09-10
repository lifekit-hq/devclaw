# Phase 0 research — provider-side transient pause

> **US3 was CUT on 2026-09-10** — US1/US2 already classify host-side and no
> `server_error` recurred in the 14 days to that date. The US3 design below is
> the record of a rejected belt-and-suspenders, not scheduled work (`specs/README.md`).

## D1 — A new `FailureKind`, not a widening of `TRANSIENT`

**Decision**: add `FailureKind.SERVER_ERROR` and put it in `PAUSING_KINDS`;
leave `TRANSIENT` a retry-now kind.

**Why**: `TRANSIENT` today carries three different cures under one label —
provider overload (wait), network blip / `ECONNRESET` (retry immediately),
and signal death of `claude --print` (a kernel OOM under host memory
pressure — retry, possibly smaller). Only the first is fixed by waiting, and
only the first justifies stopping the whole fleet. Splitting the label is
the same move the AUTH strong/weak split made in 2026-07-21: keep the
account-wide brake's trigger set as narrow as the evidence supports.

**Alternatives rejected**: making `is_pausing` true for all of `TRANSIENT`
(a socket blip would stall the fleet for minutes); a per-goal backoff instead
of an account-wide pause (the outage is account-wide — a per-goal backoff
would let every other goal re-discover it).

## D2 — Strong (pausing) vs. weak (retry-now) provider wording

**Decision**: STRONG = `529`, `overloaded` / `overloaded_error`, an explicit
`server_error` error-kind token, and the harness's own `API Error: 5xx`
framing. Everything else that is transient today (`503 Service Unavailable`,
`bad gateway`, `gateway timeout`, `ECONNRESET`, timeouts, signal death) keeps
its current classification.

**Why**: an account-wide pause has an expensive false positive. Bare 5xx
prose appears in review feedback and test output about the app under
development ("expected 200 got 503"); the existing module comment already
records that lesson for bare `500`/`502`. `529` is Anthropic's overloaded
code and not a plausible assertion number; `overloaded` and `server_error`
are provider vocabulary; `API Error: 5xx` is the harness's own framing of a
provider response, which no app-domain sentence produces.

**Consequence**: `529` moves out of the `_TRANSIENT` pattern into the new
one — the symmetric-ratchet rule means the corresponding `test_limits.py`
case moves with it rather than being duplicated.

## D3 — Where the escalation ladder lives (US2)

**Decision**: the ladder itself is a pure function in `loom/limits.py`
(`step → seconds`, doubling from `SERVER_ERROR_PAUSE_S` to
`SERVER_ERROR_MAX_PAUSE_S`); the episode's step counter is two `meta` keys
written through `StateStore.control`, beside `paused_until` / `pause_reason`
/ `pause_notified`.

**Why**: `limits.py` is documented as pure and deterministic (no I/O, no
hidden clock) and its whole value is being trivially testable against real
error strings — the counter would break that. `control.py` already owns
every other field of a pause episode, so the counter has one writer and one
home, and the reset-on-success hook has an obvious place (the same settle
that refunds the dispatch cap).

**Bound**: 5 min base, ×2 per step, 30 min ceiling. Chosen against the
existing `MAX_PAUSE_REQUEUES = 5`: 5/10/20/30/30 spans ~95 minutes of outage
inside that bound, where a flat 5-minute pause spans 25 — less than the
35-minute outage that produced the spec.

## D4 — The runner tag and the regex fallback (US3)

**Decision**: the runner tags `status="server_error"` (mirroring the existing
`rate_limited` tag); settle treats the tag as sufficient, and keeps
classifying the error text when the tag is absent.

**Why**: this is the shape the rate-limit tag already uses — "belt on top of
suspenders", per the comment in `runner/runner.py`: the structural tag is a
false-negative-tolerant improvement, and the host-side regex remains the
backstop for any runner that predates the tag or any wording the sandbox did
not recognise. It also means US3 can ship independently of US1/US2 without a
version handshake.

## Non-decisions (deliberately unchanged)

- `MAX_PAUSE_REQUEUES` stays 5 — the ladder, not the bound, buys outage
  coverage.
- The pause is still one account-wide `paused_until` shared by queue and
  heartbeat; no second brake is introduced.
- Priority order in `classify_failure` is unchanged: AUTH → AUTH-weak →
  QUOTA → RATE → (new) SERVER_ERROR → TRANSIENT → REAL, so a usage cap that
  happens to mention overload still pauses on the cap's policy.
