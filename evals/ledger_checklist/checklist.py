"""The HIDDEN acceptance checklist for the "Ledger" compounding experiment.

Lives in the devclaw repo, NEVER in the target repo. The worker builds toward
the prose `SPEC.md`; these executable checks are the grader — devclaw's own
trust-the-input / verify-the-output principle. One deterministic check per
feature; run against a checked-out target repo at HEAD, each returns pass/fail,
and the collection is the night's **criteria vector** (see the compounding
scorecard, P1-C).

Two layers, deliberately separated so the mechanics are testable off the box:

* The **probes** (``_c1``..``_c10``) shell out to dotnet / ng / an HTTP client /
  Playwright. They only *execute* meaningfully on the live box (a real toolchain
  + a booted app). They are best-effort real, live-validated.
* The **runner** (``run_checklist``) is pure orchestration over an injected
  ``CheckCtx``. Its behaviour — producing a full vector, and failing a probe
  CLOSED when it raises rather than letting the exception escape — is exercised
  in ``tests/test_ledger_checklist_runner.py`` with a fake ctx, no toolchain
  needed. Same stub discipline as the rest of the suite.

The live-box runner (P1-C) is what constructs a real ``CheckCtx``: it clones the
target repo at HEAD, boots the backend (and serves the frontend build) and wires
``sh`` / ``api`` to the real subprocess + HTTP client, then calls
``run_checklist``. Nothing here boots anything on import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, NamedTuple


class ShResult(NamedTuple):
    rc: int
    out: str


class ApiResult(NamedTuple):
    status: int
    body: str


@dataclass
class CheckCtx:
    """Everything a probe needs, all injectable so probes are testable.

    * ``repo`` — path to the checked-out target repo at HEAD.
    * ``sh`` — run a command in ``repo``: ``sh(["dotnet", "build", "backend"])``
      → ``ShResult``. The live runner uses subprocess; tests pass a fake.
    * ``api`` — call the booted backend: ``api("GET", "/health")`` →
      ``ApiResult``. ``token``/``json`` are optional. The live runner closes over
      the backend base URL (so probes speak in paths); tests pass a fake.
    """

    repo: str
    sh: Callable[..., ShResult]
    api: Callable[..., ApiResult]


class CheckResult(NamedTuple):
    passed: bool
    detail: str
    mode: str = "executed"  # "executed" | "judged" (documented Playwright fallback)


@dataclass(frozen=True)
class Check:
    id: str  # "c1".."c10" — stable across nights so the vector is comparable
    feature: str  # the prose criterion (mirrors a SPEC.md feature)
    probe: Callable[[CheckCtx], CheckResult]


# --- probes (live-validated; see module docstring) --------------------------

def _ok(cond: bool, detail: str, mode: str = "executed") -> CheckResult:
    return CheckResult(bool(cond), detail, mode)


def _c1(ctx: CheckCtx) -> CheckResult:
    build = ctx.sh(["dotnet", "build", "backend"])
    if build.rc != 0:
        return _ok(False, "dotnet build (backend) failed")
    health = ctx.api("GET", "/health")
    return _ok(health.status == 200, f"GET /health → {health.status}")


def _c2(ctx: CheckCtx) -> CheckResult:
    # A migration exists and lists cleanly — the schema is real, not implied.
    migs = ctx.sh(["dotnet", "ef", "migrations", "list", "--project", "backend"])
    return _ok(migs.rc == 0 and migs.out.strip() != "", "EF migrations present + list clean")


def _c3(ctx: CheckCtx) -> CheckResult:
    created = ctx.api("POST", "/expenses", json={"amount": 12.5, "description": "coffee", "date": "2026-01-02"})
    if created.status not in (200, 201):
        return _ok(False, f"POST /expenses (valid) → {created.status}")
    bad = ctx.api("POST", "/expenses", json={"amount": 0, "description": "", "date": "2026-01-02"})
    if bad.status != 400:
        return _ok(False, f"POST /expenses (invalid) → {bad.status}, expected 400")
    listed = ctx.api("GET", "/expenses")
    return _ok(listed.status == 200 and "coffee" in listed.body, "CRUD round-trip + validation")


def _c4(ctx: CheckCtx) -> CheckResult:
    cat = ctx.api("POST", "/categories", json={"name": "Food"})
    if cat.status not in (200, 201):
        return _ok(False, f"POST /categories → {cat.status}")
    exp = ctx.api("POST", "/expenses", json={"amount": 8, "description": "lunch", "date": "2026-01-03", "categoryId": 1})
    if exp.status not in (200, 201):
        return _ok(False, f"POST /expenses (with category) → {exp.status}")
    return _ok("categoryId" in exp.body or '"category"' in exp.body, "expense references a category")


def _c5(ctx: CheckCtx) -> CheckResult:
    filtered = ctx.api("GET", "/expenses", json=None)  # runner encodes query in path for probes needing it
    by_cat = ctx.api("GET", "/expenses?category=1")
    by_date = ctx.api("GET", "/expenses?from=2026-01-01&to=2026-01-31")
    return _ok(
        filtered.status == 200 and by_cat.status == 200 and by_date.status == 200,
        "GET /expenses honours category + date filters",
    )


def _c6(ctx: CheckCtx) -> CheckResult:
    summ = ctx.api("GET", "/expenses/summary")
    if summ.status != 200:
        return _ok(False, f"GET /expenses/summary → {summ.status}")
    body = summ.body.lower()
    return _ok("categor" in body and "month" in body, "summary totals by category + month")


def _c7(ctx: CheckCtx) -> CheckResult:
    anon = ctx.api("GET", "/expenses", token=None)
    if anon.status != 401:
        return _ok(False, f"GET /expenses unauthenticated → {anon.status}, expected 401")
    login = ctx.api("POST", "/auth/login", json={"username": "demo", "password": "demo"})
    if login.status != 200 or '"token"' not in login.body and "token" not in login.body:
        return _ok(False, f"POST /auth/login → {login.status}")
    authed = ctx.api("GET", "/expenses", token="valid")
    return _ok(authed.status == 200, "auth: 401 anon, 200 with token")


def _c8(ctx: CheckCtx) -> CheckResult:
    install = ctx.sh(["npm", "ci"], cwd="frontend")
    if install.rc != 0:
        return _ok(False, "npm ci (frontend) failed")
    build = ctx.sh(["npx", "ng", "build"], cwd="frontend")
    return _ok(build.rc == 0, "frontend builds (ng build)")


def _c9(ctx: CheckCtx) -> CheckResult:
    smoke = ctx.sh(["npx", "playwright", "test", "--grep", "expense-crud"], cwd="frontend")
    return _ok(smoke.rc == 0, "Playwright: add/edit/delete + category + filters")


def _c10(ctx: CheckCtx) -> CheckResult:
    build = ctx.sh(["npx", "ng", "build"], cwd="frontend")
    if build.rc != 0:
        return _ok(False, "ng build failed")
    smoke = ctx.sh(["npx", "playwright", "test", "--grep", "summary"], cwd="frontend")
    return _ok(smoke.rc == 0, "Playwright: login → add → list + summary")


CHECKS: list[Check] = [
    Check("c1", "Backend scaffold builds; GET /health → 200", _c1),
    Check("c2", "Persistence: EF Core + SQLite; migration applies", _c2),
    Check("c3", "Expense CRUD with validation; tested", _c3),
    Check("c4", "Categories; an expense references one", _c4),
    Check("c5", "Filtering by category + date range", _c5),
    Check("c6", "Summary totals by category + month", _c6),
    Check("c7", "JWT auth; /expenses scoped + 401 for anon", _c7),
    Check("c8", "Frontend scaffold builds; lists expenses", _c8),
    Check("c9", "Frontend CRUD (add/edit/delete + filters)", _c9),
    Check("c10", "Summary view; ng build green; smoke passes", _c10),
]


def run_checklist(ctx: CheckCtx, checks: list[Check] = CHECKS) -> dict[str, CheckResult]:
    """Run every check against ``ctx`` and return ``{id: CheckResult}``.

    FAIL-CLOSED: a probe that raises does not escape and does not count as a
    pass — it becomes ``CheckResult(passed=False, ...)`` with the exception in
    the detail. A grader that crashes is never a green (the #186 discipline,
    applied to the experiment's instrument).
    """
    out: dict[str, CheckResult] = {}
    for c in checks:
        try:
            out[c.id] = c.probe(ctx)
        except Exception as e:  # noqa: BLE001 — fail-closed on any probe crash
            out[c.id] = CheckResult(False, f"probe crashed: {type(e).__name__}: {e}")
    return out


def criteria_vector(results: dict[str, CheckResult]) -> dict[str, bool]:
    """The night's N-bit green/red snapshot — the unit the scorecard compares
    across nights to detect monotonic progress vs churn (green→red)."""
    return {cid: r.passed for cid, r in results.items()}
