# TinySpec: the console's phone tab bar pinned to the bottom edge

**Branch**: goal/dc-phone-tab-bar-971
**Date**: 2026-10-26
**Status**: in review
**Complexity**: small (one shell template, one dependency range, one e2e spec)
**Issue**: #971 — the phone tab bar does not sit fully at the bottom of the screen,
where Finance Sentry on the same `cmn-app-layout` shell does

## North-star case

- **Failure moved**: *ran but needed the owner*. The console is the surface the
  owner answers a `decide` from, and the phone is where they answer it. A tab
  bar that is not where the thumb expects it costs a tap per visit and pushes
  the answer to a desktop; the geometry was pinned by nothing, so any shell or
  library bump could move it again without a red check.
- **Number that shows it**: the phone shell's geometry is asserted, not
  eyeballed — the tab bar's bottom edge, its height, its labels' clearance of
  the home indicator and the window's scroll position on every tab route, in
  both the ordinary and the iOS-26 viewport shape. Before: 0 assertions.
- **Cut when**: the console stops being used from a phone, or lifekit-common's
  own suite asserts an app's shell geometry (today it asserts its stories).

## What

Wire the console's phone shell exactly like Finance Sentry's and pin the
geometry with a Playwright spec in that repo's shape:

- `@lifekit-hq/*` moves from `^4.1.0` to `^4.2.0` (the version Finance Sentry
  runs), lockfile included.
- The three notices each carry `cmnAppLayoutNotice` instead of sitting inside
  one wrapper `<div cmnAppLayoutNotice>`, so each is its own child of the
  shell's notices column. This changes nothing observable today — every `lk-*`
  notice sets `:host {display: block}`, so the wrapper collapsed to 0 height
  too — but it is the wiring the shell documents, and the column's children are
  what the shell measures into `--cmn-sticky-top`.
- `host: {class: 'block h-full'}` is removed from `dc-app-shell`, the element
  wrapping `<cmn-app-layout>`. Finance Sentry's `fns-app-shell` carries no host
  class; this was the console's one remaining height rule of its own around the
  shell. It was inert (the frame inside is `fixed inset-0`), so removing it is
  parity, not a fix. `dc-root`'s `block h-full` and `html, body {height: 100%}`
  stay — those are byte-identical to Finance Sentry.
- `frontend/e2e/phone-overlay.spec.ts` asserts the tab bar's geometry on all
  five tab routes at 402x874 with insets 62/34, and again with viewport units
  inflated by the top inset.

With the host class gone, the console adds no height, padding or safe-area rule
of its own around the shell.

## Context

| File | Role |
|---|---|
| `frontend/src/app/shell/app-shell.component.ts` | The console's `cmn-app-layout`, `[phoneOverlay]="true"`, the notice slot, the removed host class |
| `frontend/package.json`, `package-lock.json` | The `@lifekit-hq/*` range and its resolution |
| `frontend/e2e/phone-overlay.spec.ts` | New — the geometry contract, in Finance Sentry's shape |
| `frontend/e2e/phone.spec.ts` | Existing and unchanged — what a PAGE owes the shell at three widths, no insets |

The shell itself is lifekit-common's: the frame is `fixed inset-0` (never
viewport units, deliberately), the tab bar `absolute inset-x-0 bottom-0` with
`calc(49px + env(safe-area-inset-bottom))`.

## Requirements

On `/console/{needs-you,goals,projects,verdicts,more}` at 402x874 with
safe-area insets top 62 / bottom 34, and again with `.h-screen`/`.h-dvh` and
friends inflated by the top inset:

- The tab bar's bottom edge equals the viewport's bottom edge, and it is edge
  to edge horizontally.
- Its height is 49px plus the bottom inset.
- Every tab label and icon sits fully above the bottom inset, and no label is
  squeezed (its box is as tall as its text).
- html and body do not overflow, `window.scrollY` is 0 after a scroll attempt,
  and nothing outside `main` scrolls.
- The notices column's children are exactly the three `lk-*` notices, it has no
  height while they are inactive, and `--cmn-sticky-top` is `0px`. (Verified to
  FAIL on the wrapper-div wiring: the column's children read `['div']`.)
- Nothing from `body` down takes its height from a viewport unit — as a utility
  class, an arbitrary value, a component stylesheet or an inline style. The scan
  covers the console's own wrappers (`dc-root`, `dc-app-shell`) as well as the
  shell. (Verified to FAIL when `dc-app-shell` is given `h-dvh`:
  ``dc-app-shell.block via `.h-dvh` height``.)

## What this change does NOT establish

The gap the owner sees on a real phone is **not reproduced here**, and this
spec does not name its cause. Measured on this branch, in Chromium with the
insets simulated over CDP, the tab bar is flush (bottom 874, height 83) in
all four configurations: `^4.1.0` and `^4.2.0`, wrapper div and per-element
notices. Both differences the issue names are therefore real wiring drift from
Finance Sentry, and neither is the cause of the gap.

Ranked candidates, with what would settle each:

1. **A stale precached app shell on the owner's phone.** The console is an
   installed PWA on `@angular/service-worker`, precache-only, scope
   `/console/`, `registrationStrategy: registerWhenStable:30000`. The phone
   shell itself landed only on 2026-10-13 (`appshell-migration`, #943) and the
   last lifekit bump on 2026-10-26 (#969), so a phone that has not accepted an
   update prompt would render pre-#969 geometry no matter what `main` holds.
   **Settles without a device**: the More tab renders `v{VERSION}` — compare it
   against the deployed release (2.4.0) and `/console/ngsw.json`'s hash. If the
   phone is behind, that is the cause. This is the cheapest check and should be
   run first.
2. **The iOS home-screen keyboard shrinking the layout viewport**, so the
   `fixed inset-0` frame stops short of the bottom edge. lifekit-common
   documents this exact symptom and heals it on a text input's blur
   (`viewport-heal.ts`) — but the heal only fires on blur, so a session that
   shrank and never blurred a field stays short. Ships in both 4.1.0 and
   4.2.0, so it is not explained by the version. **Needs a device**: reproduce
   by focusing then blurring a field in the installed app.
3. **Not installed to the Home Screen at all.** In a Safari tab the bottom
   browser toolbar overlays the viewport and the insets differ. **Settles with
   one screenshot** of whether the status bar is the app's or Safari's.

## Rejected alternatives

- **Add a height, padding or safe-area rule in the console** to push the bar
  down. The shell owns that geometry (`docs/PHONE-CONTRACT.md`); an app rule
  there is the drift the issue is about, and it would mask the real cause.
- **Copy Finance Sentry's whole spec, tablet and desktop cases included.**
  Those are not the contract here, and `console.spec.ts` and `quality.spec.ts`
  already drive the wide shells.
- **Assert the stricter `['in-main']` single-scroller shape** Finance Sentry
  uses. Its alerts page scrolls a `cmn-page-scroll` inside `main`; the
  console's pages scroll `main` itself. The contract is that the WINDOW never
  scrolls and no scroller sits outside `main`, and that is what is asserted.
- **Name a cause from the Chromium run.** It would be a hypothesis dressed as
  a finding; the issue asks for the cause *with its evidence*.

## Tasks

- [x] `@lifekit-hq/*` to `^4.2.0`, lockfile refreshed
- [x] Each notice element carries `cmnAppLayoutNotice`
- [x] `frontend/e2e/phone-overlay.spec.ts`, 5 routes x 2 viewport shapes
- [x] `host: {class: 'block h-full'}` removed from `dc-app-shell`
- [x] Controlled run of the spec against both notice wirings, for the evidence
- [ ] The cause of the real-phone gap, named with its evidence (needs a device)

## Done When

- [x] `.devclaw/verify` green (pytest, ruff, mypy, lint-imports, yamllint,
  frontend lint/test/build/chrome-check)
- [x] `npm run e2e`: 89 passed locally, the new spec included. The 44
  `impeccable` design-scan cases cannot run in this sandbox — the scan launches
  its own Chrome, and this host restricts unprivileged user namespaces
  (`No usable sandbox!`). They are unrelated to this diff and CI runs them.
- [ ] This PR's CI green
- [ ] The cause clause of issue #971 answered
