# TinySpec: the console on the lifekit app-shell standard, appearance per user

**Branch**: fm/dc-appshell-migrate
**Date**: 2026-10-13
**Status**: in review
**Complexity**: medium (one additive table and two routes; the rest is the console)
**Issue**: the captain's lifekit app-shell standard (plan section 6, devclaw row, as amended by
section 8), devclaw on `@lifekit-hq/*` 3.3.x since #943

## North-star case

- **Failure moved**: *ran but needed the owner*. The console has no
  phone-reachable Settings (it sat in the tab bar's overflow), appearance is a
  per-device toggle that every new phone or browser asks for again, and the
  controls that decide whether the loop runs (hold, run window) are one
  hidden tap away from a phone. The owner reaches for another device or the
  chat waiter to hold dispatch.
- **Number that shows it**: owner interventions made from a phone: a hold or
  a run-window change is two taps from any screen (More, then Settings), and
  a new device shows the owner's appearance at first load without a visit to
  Settings. Read as the count of `PATCH /api/me/preferences` rows against
  `updated_at`, and the `/control/pause` calls with a phone user agent.
- **Cut when**: devclaw has more than one console user and a shared auth
  client in lifekit-common owns per-user preferences (the captain's Q6, a
  later track), or the console stops being used from a phone.

## What

The console moves to the standard app shell. Phone: four tabs and a More
tab (the account, Settings, Sign out); the avatar leaves the top bar there.
Wide: the sidebar plus the avatar menu (identity header, Settings, Sign out).
There is no top-bar theme toggle: the command palette offers Appearance
System, Light, Dark. Settings gains Account (read-only gate identity) and
Appearance (mode and seed colour, applied at once), and the existing dispatch
and run-window controls become a "Workspace: Instance" group with unchanged
behaviour. Each page declares its title and parent in route data; the
hand-rolled headings go.

Appearance follows the account: `GET`/`PATCH /api/me/preferences` in the
shared shape (`{appearance: {theme, seed?, intensity?}}`), one row per gate
identity in a new `user_preferences` table, with the device cache first.

## Context

| File | Role |
|---|---|
| `devclaw/server/routes/me.py` | The two routes; identity is the `X-Auth-Request-Email` the sso forwardAuth sets |
| `devclaw/state_store/preferences.py`, `schema.py` | `user_preferences(subject PK, theme, seed, intensity, updated_at)`, additive |
| `frontend/src/app/shell/`, `settings/`, `account/` | The shell, More page, Settings, account model, preferences store |
| `frontend/src/app/app.routes.ts` | Route `data` chrome (title, parent) |
| `tests/test_me_preferences.py` | The tripwire: no identity writes nothing, identities isolate, invalid writes nothing |

## Requirements

- No identity, no row: a request without the forwarded header is `401` and
  touches nothing. The console then keeps its device cache.
- Rows are keyed by the lowercased email; one user's write is never another's.
- A `PATCH` is validated whole (theme in system/light/dark, seed `#rrggbb`,
  intensity 0..1) before any write; an invalid body writes nothing.
- The schema change is additive (`CREATE TABLE IF NOT EXISTS`); an existing
  database opens unchanged.
- The sign-in and sign-out flow, the bearer-token handling and the deploy
  config do not change.
- Appearance is applied from the device cache before first paint
  (`lifekit-theme-sync` block in `index.html`), then the account's wins.

## Rejected alternatives

- **Key by the bearer token**: there is one token for the whole instance, so
  every user would share one row, which is the per-device state again.
- **Require the edge-proof header or change Traefik**: the devclaw router
  does not carry it, and a deploy-config change is out of scope for this
  migration. The forwarded email is already trusted by the sso middleware that
  fronts the console; the machine router bypasses sso and carries no
  identity, so a `?token=` console gets `401` and keeps its device cache.
- **Store appearance in `meta`**: `meta` holds the control flags (pillar 5).
  A per-user record is a different noun and belongs in its own table.
- **A page-level Save**: the standard applies toggles and pickers at once; only
  the run window's text fields keep a per-section Save.
- **A shared auth client now**: the captain's Q6 marks it a later track.

## Tasks

- [x] `user_preferences` table, `PreferencesMixin`, `/api/me/preferences`
- [x] `tests/test_me_preferences.py`
- [x] Shell: More tab, account menu, palette appearance and Sign out, no toggle
- [x] Settings: Account, Appearance, Workspace: Instance, About, Session
- [x] Route data chrome; hand-rolled page headings removed
- [x] `dc.css` seed, `lifekit-theme-sync`, root font size left alone
- [x] Unit and e2e specs; live drive at 390px and desktop, light and dark
- [x] Docs: architecture code map and stored state, INDEX tag, AGENTS.md map

## Done When

- [x] Suite, ruff, mypy, lint-imports green; `npm run lint`, `npm test`,
  `npm run build`, `lifekit-chrome-check`, `npm run e2e` green
- [x] Driven live at 390px and desktop in light and dark: More, Settings,
  appearance saved and read back with the device cache cleared
- [ ] This PR's CI green
