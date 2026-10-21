# TinySpec: a fixture-backed console demo on GitHub Pages

**Branch**: fm/gh-dc-957
**Date**: 2026-10-21
**Status**: done
**Complexity**: small
**Issue**: #957 (part of #343, finish plan T10)

## North-star case

- **Failure moved**: *ran but needed the owner*. Someone who wants to see what
  devclaw does today needs a host, a `GH_TOKEN`, a GitHub Packages token for
  the console bundle, and the owner to explain the pages. The hosted demo
  shows every console page on invented data with no install and no token.
- **Number that shows it**: the time from the README to a working console.
  Today it is a host install plus a token. With the demo it is one link.
- **Cut when**: the console stops being the owner's surface, or the fixtures
  stop matching what the host serves. `generate.py` writes them from the host's
  own routes, so a drift shows in the e2e suite first.

## What

`npm run build:demo` builds the console's production bundle under
`/devclaw/` with the `demo` build configuration. That configuration swaps
`src/app/demo/demo.ts` for `demo.pages.ts` (Angular `fileReplacements`). The
swapped-in interceptor answers every feed from the e2e fixtures, which the
build copies to `demo/`, through the same `feeds.json` map that
`serve-dist.mjs` and the specs read. It keeps the account's appearance for the
visit and refuses every verb with a read-only notice. The build writes
`404.html`, GitHub Pages' SPA fallback, and points the manifest at
`/devclaw/`. `pages.yml` publishes it from main, and the frontend CI job builds
it on every PR. The README's *Try it* section links the demo and documents
the stub-engine host (`DEVCLAW_ENGINE=stub`).

## Context

| File | Role |
|---|---|
| `frontend/src/app/demo/demo.ts`, `demo.pages.ts` | The swap point, and the demo's fixture interceptor |
| `frontend/angular.json` | The `demo` configuration: base href, output, fixtures as assets, the file replacement, no worker |
| `frontend/package.json` | `build:demo`: the build, `404.html`, the manifest's paths |
| `frontend/e2e/fixtures/generate.py` | Also writes every seeded session, so no demo link dead-ends |
| `.github/workflows/pages.yml`, `ci.yml` | Publish from main; build on every PR |

## Requirements

- The real console's build is unchanged. `demo.ts` adds no interceptor, and
  the worker stays on outside the demo.
- The fixtures hold only invented data: `example/widgets` and
  `owner@example.com`. They hold no token, hostname or personal data.
- The publish runs only from CI on main.

## Rejected alternatives

- **A runtime switch (a meta tag, a query flag)**: it ships the demo path in
  the real console. `fileReplacements` is Angular's standard build-time swap.
- **Copying the fixtures to their feed paths**: that needs a second map beside
  `feeds.json`, which #962 made the single map.

## Tasks

- [x] The `demo` configuration, the swap point and the interceptor
- [x] `generate.py` writes every session a page links to; fixtures regenerated
- [x] `pages.yml`, and the demo build in the frontend CI job
- [x] README *Try it*: the demo link and the stub-engine quickstart

## Done When

- [x] `npm run lint`, `npm test`, `npm run build`, `npm run e2e` (122) and
  `npm run build:demo` are green. Served the way Pages serves it, every
  linked route of the demo renders, and no page dead-ends.
- [ ] The repository's Pages source is set to GitHub Actions, and the first
  `pages.yml` run on main publishes https://lifekit-hq.github.io/devclaw/
