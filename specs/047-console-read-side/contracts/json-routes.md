# Contract: JSON feeds (P1–P4)

All GET, token-gated like every route but `/health` and `/metrics`. Registration order: every
`*.json` route registers before `routes/console.py` (`tests/test_route_shadowing.py`).

## Changed feeds

- `GET /goals.json` → each row gains `attention` (data-model §2, or `null`) and `usage`
  (data-model §3 aggregate).
- `GET /goals/{id}.json` → gains `attention`, `usage`, `verdicts[]` (data-model §4, newest first);
  each `sessions[]` entry gains `usage` (block or `null`).
- `GET /projects.json`, `GET /projects/{id}.json` → rollup gains `usage` (aggregate over the
  project's goals' sessions) and each goal entry gains `attention.kind` (or `null`).
- `GET /tasks/{id}.json` → gains `usage` (block or `null`) and, for a blocked row,
  `block: { question, options, default, recommended, kind, item }`.

## New feed

- `GET /verdicts.json?limit=N` → `{ "verdicts": [data-model §4 …], "count": n, "truncated": bool }`,
  newest first, default limit 100, max 1000 (`json_limit`).

## Verbs (unchanged)

- `POST /goals/{id}/decide {text}` — an option click posts the option's full text.
- `POST /goals/{id}/cancel`.
