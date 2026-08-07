# Ledger — a personal expense tracker

> **This file is the worker-facing brief for the "Ledger" compounding experiment.**
> It states *what to build*, in prose. It is the canonical copy; at experiment
> start it is placed in the **target repo** (and mirrored into the durable goal's
> `done_when`). The worker builds toward it.
>
> How each feature is *graded* is deliberately **not** in this file — the
> executable acceptance checks live scorecard-side in the devclaw repo
> (`evals/ledger_checklist/`) and never enter the target repo. Build the feature
> for real; a green is earned by the feature working, not by satisfying a check
> you can read.

## What Ledger is

A small but real personal-finance app: a **.NET minimal-API** backend with a
**SQLite** store and an **Angular** single-page frontend. A user records
expenses, categorises them, filters and summarises them, behind a login. It is
intentionally layered — later features build on earlier ones — so the app cannot
be finished in a single pass.

## Stack (fixed)

- **Backend:** .NET (latest LTS) minimal API, C#. EF Core + SQLite. `backend/`.
- **Frontend:** Angular (latest), TypeScript. `frontend/`.
- **Tests:** backend integration tests (`dotnet test`); a frontend Playwright
  smoke (`frontend/e2e/`). Both must pass for the app to be "done".
- One-command run for each side, documented in the repo `README.md`
  (`dotnet run --project backend`; `ng serve`).

## Features (build in roughly this order — each builds on the last)

1. **Backend scaffold.** The backend project builds and runs. `GET /health`
   returns `200` with a small JSON body. `README.md` documents how to run it.
2. **Persistence.** EF Core + SQLite wired. An `Expense` entity
   (`id, amount, description, date, categoryId?`). A migration creates the schema
   and applies cleanly from empty.
3. **Expense CRUD.** `POST/GET/GET{id}/PUT/DELETE /expenses`, with input
   validation (amount > 0, description non-empty, date required). Covered by
   backend integration tests.
4. **Categories.** A `Category` entity (`id, name`) and `GET/POST /categories`.
   An expense may reference a category; the CRUD endpoints accept and return it.
5. **Filtering.** `GET /expenses` accepts `category`, `from`, and `to` query
   params and returns the filtered set. Covered by tests.
6. **Summary.** `GET /expenses/summary` returns totals grouped by category and by
   month. Covered by tests.
7. **Auth.** JWT-based login (`POST /auth/login`, a seeded or registerable user).
   `/expenses` and `/categories` require a valid token and are scoped to the
   authenticated user; unauthenticated requests get `401`.
8. **Frontend scaffold.** The Angular app builds (`ng build`) and, running
   against the live API, lists the user's expenses.
9. **Frontend CRUD.** Add, edit, and delete an expense from the UI, including a
   category dropdown and the filter controls (category / date range).
10. **Summary view.** A view showing totals by category and month (a table and/or
    chart). `ng build` is green and a Playwright smoke drives the core flow
    (log in → add an expense → see it in the list and the summary).

## Definition of done

All ten features work, the backend integration tests pass, and the frontend
builds and its Playwright smoke passes. Keep the app runnable at every step — a
half-migrated or non-building state is not progress.

## Working style

- Small, reviewable PRs — one feature (or a coherent slice of one) per PR.
- Keep `README.md` honest and current: how to run each side, how to run the tests.
- Do not weaken or delete tests to make a build pass. If a feature is genuinely
  incomplete, leave it visibly incomplete rather than faking it green.
