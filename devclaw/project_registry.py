"""The project registry — one source of truth for "all the things devclaw owns".

DevClaw already has three lower-level primitives: ephemeral **tasks** and
**programs** (SQLite, in ``state_store``) and durable **goals** (on disk, in
``goal_store``). What it lacked is a single first-class entity that says *"these
are the repos I'm working on, and here's the current status of each"* — the view
a control plane (chat / API / CLI) needs to answer "what are you doing?".

A :class:`Project` is exactly that thin unifying record: a repo + its workspace +
an optional live preview + a status. It does NOT own the goals or duplicate
their state — the rollup (:func:`project_rollup`) joins live goal status on
read via **workspace_dir match**, so the registry never rots.

Association model: a goal belongs to a project iff their ``workspace_dir``
values match (normalized). This deliberately replaces the earlier stored
``goal_ids`` list, which drifted on the cancel-and-refile pattern (v1
missions cancelled but the v2 mission wasn't relinked → Projects Home read
0 active goals for a project that had a live one). Workspace-dir is already
the identity axis for verify / sandbox / PRs, so making it the project↔goal
join key is coherent with the rest of the architecture. ``Project.goal_ids``
is retained as advisory only (CLI ``link_goal`` still writes it) but is NOT
consulted by the rollup.

Deliberately small and decoupled: its own ``projects`` table on the shared SQLite
file (registry writes are rare + human-driven), no dependency on the goal layer —
the rollup takes a pre-fetched ``all_goals`` list (from
``goal_service.list_goals``) so both the MCP tools and the CLI reuse one shape.
Distinct from ``project_store.Project``, which is the *build-from-scratch
interview* artifact.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from .state_store import SQLITE_BUSY_TIMEOUT_MS

ProjectStatus = Literal["active", "paused", "archived"]
#: a read-only getter that returns a goal's live status dict (or raises KeyError);
#: goal_service.get_goal and a GoalStore-backed getter both satisfy it.
GoalGet = Callable[[str], dict]

#: sentinel distinguishing "field not supplied" (leave unchanged) from an
#: explicit ``None`` (clear the override, fall back to the global default).
#: Every per-project OVERRIDE field (``autodeploy``, ``review_gate``,
#: ``verify_done``, ``browser_gate_mode``, ``sandbox_image``) uses this three-way
#: partial-update semantics.
_UNSET: Any = object()

#: the per-project override fields, in one place so create/read/save/migrate
#: stay in lockstep. Each is nullable = "inherit the devclaw-wide default"; a
#: non-null value pins this project's behaviour regardless of the env default.
#: ``bool`` fields persist as INTEGER (0/1), ``str`` fields as TEXT.
_OVERRIDE_BOOL_FIELDS = ("autodeploy", "review_gate", "verify_done")
_OVERRIDE_STR_FIELDS = ("browser_gate_mode", "sandbox_image",
                        "sandbox_memory", "sandbox_cpus")
_OVERRIDE_FIELDS = _OVERRIDE_BOOL_FIELDS + _OVERRIDE_STR_FIELDS

#: docker image-ref grammar for the ``sandbox_image`` override, enforced at
#: THIS single write choke point (create/update) so a stored pin can never be
#: flag-shaped ("--env-file=…" would be parsed by docker as a FLAG, injecting
#: host env — incl. a stray metered API key — into an autonomous sandbox),
#: whitespace-ridden, or empty ("" would silently degrade to the default
#: instead of erroring). The console edge re-checks the same grammar for a
#: friendly 400; this raise is the backstop the MCP path hits.
_IMAGE_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:@-]*$")


def _validate_sandbox_image(value: Optional[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not _IMAGE_REF_RE.fullmatch(value):
        raise ValueError(
            f"sandbox_image must be a docker image ref "
            f"([a-zA-Z0-9][a-zA-Z0-9._/:@-]*), got: {value!r}"
        )


#: docker memory-string grammar for the ``sandbox_memory`` override — digits
#: plus an optional b/k/m/g suffix, the exact shape ``--memory`` accepts and
#: ``host_resources._parse_mem`` parses. Enforced at the same single write
#: choke point as ``_validate_sandbox_image`` (spec 020 US4).
_MEM_STR_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[bkmg]?$", re.IGNORECASE)


def _validate_sandbox_memory(value: Optional[str]) -> None:
    """Grammar AND write-time admittability (spec 020, clarified with Denys:
    reject loudly, never store a value dispatch can only defer on forever).
    A value is unadmittable when it plus the cognition reserve exceeds host
    MemTotal — the stable budget, not the fluctuating MemAvailable. On a host
    where MemTotal is unreadable the admittability half is skipped (grammar
    still enforced) — fail-open mirrors the admission brake itself."""
    if value is None:
        return
    if not isinstance(value, str) or not _MEM_STR_RE.fullmatch(value.strip()):
        raise ValueError(
            f"sandbox_memory must be a docker memory string "
            f"(digits + optional b/k/m/g, e.g. '4g'), got: {value!r}"
        )
    # local imports: host_resources imports this module at import time — the
    # lazy import here is what keeps the pair acyclic.
    from .host_resources import _parse_mem, host_mem_total_bytes
    from . import config as _config

    want = _parse_mem(value)
    reserve = _parse_mem(_config.COGNITION_MEM_RESERVE)
    total = host_mem_total_bytes()
    if total is not None and want + reserve > total:
        raise ValueError(
            f"sandbox_memory {value!r} can never be admitted on this host: "
            f"{want} bytes + cognition reserve {reserve} bytes exceeds host "
            f"MemTotal {total} bytes. Lower the override or grow the host."
        )


def _validate_sandbox_cpus(value: Optional[str]) -> None:
    if value is None:
        return
    try:
        ok = isinstance(value, str) and float(value.strip()) > 0
    except (ValueError, TypeError):
        ok = False
    if not ok:
        raise ValueError(
            f"sandbox_cpus must be a positive number string (e.g. '2.0'), "
            f"got: {value!r}"
        )


def _validate_workspace_path(value: Optional[str]) -> None:
    """A stored ``workspace_dir`` must be a non-empty, absolute, container-side
    path. The 2026-08-12 dispatch failures came from a *host*-perspective path
    (``/srv/…`` from before the container migration) written for a
    *container*-perspective consumer, plus a fourth path the waiter invented
    outright — accepted silently because the write path validated nothing.
    Validating shape at THIS single write choke point (mirroring
    :func:`_validate_sandbox_image`) stops a relative/empty/junk path from being
    stored to rot. Existence is deliberately NOT checked here — a project may be
    registered before its clone exists; asserting the workspace is really there
    is the dispatch-time preflight's job (spec 003 US2/US3)."""
    if value is None:
        return
    p = str(value).strip()
    if not p:
        raise ValueError("workspace_dir must be a non-empty path")
    if p.startswith("~"):
        p = str(Path(p).expanduser())
    if not p.startswith("/"):
        raise ValueError(
            f"workspace_dir must be an absolute container-side path, got: {value!r}"
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bool_db(value: Optional[bool]) -> Optional[int]:
    """Persist a three-way override bool: None stays NULL (inherit), else 0/1."""
    return None if value is None else int(value)


@dataclass
class Project:
    id: str  # stable slug, e.g. "todo-fullstack-demo"
    name: str
    repo_url: Optional[str] = None
    workspace_dir: Optional[str] = None
    preview_url: Optional[str] = None
    status: ProjectStatus = "active"
    #: durable goals driving this project — linked by id, never copied
    goal_ids: list[str] = field(default_factory=list)
    notes: str = ""
    #: per-project overrides for delivery/quality knobs that are otherwise
    #: devclaw-wide env defaults. ``None`` = inherit the default; a set value
    #: pins this repo. A decision about a REPO, not a goal's objective.
    #: Resolved via :meth:`resolve_override`.
    autodeploy: Optional[bool] = None     # None = conditional default (app surface ⇒ on, library ⇒ off)
    review_gate: Optional[bool] = None    # devclaw default: task_queue.REVIEW_GATE_ENABLED
    verify_done: Optional[bool] = None    # DEVCLAW_GOAL_VERIFY_DONE
    browser_gate_mode: Optional[str] = None  # fleet default: task_queue.BROWSER_GATE_MODE (flexible|strict)
    #: per-project sandbox image (ADR 0005) — the exotic-needs escape hatch and
    #: the migration bridge (.NET projects pin devclaw-sandbox-dotnet:local
    #: until the mise path passes its live gate). None = the engine's
    #: DEVCLAW_SANDBOX_IMAGE default.
    sandbox_image: Optional[str] = None
    #: per-project sandbox sizing (spec 020 US4, ADR 0005's sibling): a heavy
    #: frontend repo declares e.g. "6g" while python repos inherit the
    #: instance default. Validated (grammar + write-time admittability) at
    #: the create/update choke point; resolved at launch beside
    #: sandbox_image; accounted by launch admission.
    sandbox_memory: Optional[str] = None
    sandbox_cpus: Optional[str] = None
    #: evidence/shakedown project (spec 018 US2): excluded from every
    #: ratchet-facing scorecard rate, reported separately. Plain bool —
    #: a project either is bench or isn't; there is no global default to
    #: inherit, so no tri-state.
    bench: bool = False
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "repoUrl": self.repo_url,
            "workspaceDir": self.workspace_dir,
            "previewUrl": self.preview_url,
            "status": self.status,
            "goalIds": list(self.goal_ids),
            "notes": self.notes,
            "autodeploy": self.autodeploy,
            "reviewGate": self.review_gate,
            "verifyDone": self.verify_done,
            "browserGateMode": self.browser_gate_mode,
            "sandboxImage": self.sandbox_image,
            "sandboxMemory": self.sandbox_memory,
            "sandboxCpus": self.sandbox_cpus,
            "bench": self.bench,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


def _row_to_project(r: sqlite3.Row) -> Project:
    goal_ids: list[str] = []
    if r["goal_ids"]:
        try:
            parsed = json.loads(r["goal_ids"])
            if isinstance(parsed, list):
                goal_ids = [x for x in parsed if isinstance(x, str)]
        except json.JSONDecodeError:
            pass  # tolerate a corrupt cell — treat as no links
    keys = r.keys()

    def _bool_col(name: str) -> Optional[bool]:
        # Migration-safe: a row read before the column existed has no key.
        raw = r[name] if name in keys else None
        return None if raw is None else bool(raw)

    def _str_col(name: str) -> Optional[str]:
        raw = r[name] if name in keys else None
        return None if raw is None else str(raw)

    return Project(
        id=r["id"],
        name=r["name"],
        repo_url=r["repo_url"],
        workspace_dir=r["workspace_dir"],
        preview_url=r["preview_url"],
        status=r["status"],
        goal_ids=goal_ids,
        notes=r["notes"] or "",
        autodeploy=_bool_col("autodeploy"),
        review_gate=_bool_col("review_gate"),
        verify_done=_bool_col("verify_done"),
        browser_gate_mode=_str_col("browser_gate_mode"),
        sandbox_image=_str_col("sandbox_image"),
        sandbox_memory=_str_col("sandbox_memory"),
        sandbox_cpus=_str_col("sandbox_cpus"),
        bench=bool(_bool_col("bench")),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


class ProjectExists(Exception):
    """Raised on create() when the id is already taken."""


class UnknownProject(KeyError):
    """Raised by resolve_dispatch() when a project_id is not registered. A
    KeyError subclass so existing ``except KeyError`` guards keep working; the
    tool layer maps it to a synchronous ToolError (zero task/engine work)."""


@dataclass
class ResolvedDispatch:
    """The concrete dispatch target resolved from a ``project_id`` reference key
    (spec 003 / #520). Transient — consumed at the tool seam, never persisted.
    Carries ``project_id`` so the dispatch seam can stamp it onto the task/goal
    row (#524 P3): the per-project override knobs then resolve by id off that
    stored ``project_id``, not by a fragile normalized-workspace-path scan. The
    knob VALUES aren't resolved here — only the key needed to resolve them later."""
    workspace_dir: str
    repo_url: Optional[str] = None
    project_id: Optional[str] = None


class ProjectRegistry:
    """SQLite-backed CRUD for the project registry. Owns its own ``projects``
    table on the given db file (shared with the state store; registry writes are
    infrequent so a second WAL connection is fine). A re-entrant lock serializes
    access since FastMCP may touch it from the loop and background tasks."""

    def __init__(self, db_path: str) -> None:
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        # Wait for the lock instead of failing fast — the CLI and the server each
        # open a connection to this shared file, so a CLI write while the server
        # holds the lock must queue, not raise `database is locked`. See
        # state_store.SQLITE_BUSY_TIMEOUT_MS (same env knob, same db file).
        self._db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        self._lock = threading.RLock()
        self._bootstrap()

    def _bootstrap(self) -> None:
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                  id            TEXT PRIMARY KEY,
                  name          TEXT NOT NULL,
                  repo_url      TEXT,
                  workspace_dir TEXT,
                  preview_url   TEXT,
                  status        TEXT NOT NULL DEFAULT 'active',
                  goal_ids      TEXT,
                  notes         TEXT,
                  autodeploy    INTEGER,
                  review_gate   INTEGER,
                  verify_done   INTEGER,
                  browser_gate_mode TEXT,
                  created_at    INTEGER NOT NULL,
                  updated_at    INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
                CREATE TABLE IF NOT EXISTS managed_repos (
                  slug       TEXT PRIMARY KEY,  -- lowercased owner/name
                  created_at INTEGER NOT NULL
                );
                """
            )
            # Migration for DBs created before a given override column existed —
            # CREATE TABLE IF NOT EXISTS above is a no-op on an already-existing
            # table, so each per-project override column needs adding explicitly
            # on an older `projects` table. NULL by default = "inherit the global
            # env default", same as a freshly created row. SQLite type is INTEGER
            # for bool fields, TEXT for the string field.
            #
            # Swallow the duplicate-column error rather than introspecting first:
            # this db file is SHARED (the CLI and the server each open their own
            # connection, see the class docstring), so a PRAGMA-then-ALTER read
            # is a TOCTOU race — the loser of two concurrent bootstraps crashes
            # on a column the winner just added. Mirrors StateStore._bootstrap
            # and GoalStore's migrator, which have always done it this way; this
            # was the one migrator that didn't.
            for name in _OVERRIDE_BOOL_FIELDS:
                self._add_column(name, "INTEGER")
            for name in _OVERRIDE_STR_FIELDS:
                self._add_column(name, "TEXT")
            # bench (spec 018 US2): plain bool, NULL-on-old-rows reads False.
            self._add_column("bench", "INTEGER")
            self._db.commit()

    def _add_column(self, name: str, sql_type: str) -> None:
        """Idempotent ``ALTER TABLE projects ADD COLUMN``. SQLite has no
        ``IF NOT EXISTS`` for columns, so "already there" arrives as an
        OperationalError and is the success case, not a failure."""
        try:
            self._db.execute(f"ALTER TABLE projects ADD COLUMN {name} {sql_type}")
        except sqlite3.OperationalError:
            pass  # column already exists (or another writer just added it)

    # ---- CRUD --------------------------------------------------------------

    def create(
        self,
        *,
        id: str,
        name: str,
        repo_url: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        preview_url: Optional[str] = None,
        notes: str = "",
        goal_ids: Optional[list[str]] = None,
        autodeploy: Optional[bool] = None,
        review_gate: Optional[bool] = None,
        verify_done: Optional[bool] = None,
        browser_gate_mode: Optional[str] = None,
        sandbox_image: Optional[str] = None,
        sandbox_memory: Optional[str] = None,
        sandbox_cpus: Optional[str] = None,
        bench: bool = False,
    ) -> Project:
        _validate_sandbox_image(sandbox_image)
        _validate_sandbox_memory(sandbox_memory)
        _validate_sandbox_cpus(sandbox_cpus)
        _validate_workspace_path(workspace_dir)
        p = Project(
            id=id, name=name, repo_url=repo_url, workspace_dir=workspace_dir,
            preview_url=preview_url, notes=notes, goal_ids=list(goal_ids or []),
            autodeploy=autodeploy,
            review_gate=review_gate, verify_done=verify_done,
            browser_gate_mode=browser_gate_mode, sandbox_image=sandbox_image,
            sandbox_memory=sandbox_memory, sandbox_cpus=sandbox_cpus,
            bench=bool(bench),
        )
        with self._lock:
            try:
                self._db.execute(
                    """INSERT INTO projects
                         (id, name, repo_url, workspace_dir, preview_url, status,
                          goal_ids, notes, autodeploy,
                          review_gate, verify_done, browser_gate_mode, sandbox_image,
                          sandbox_memory, sandbox_cpus,
                          bench, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        p.id, p.name, p.repo_url, p.workspace_dir, p.preview_url,
                        p.status, json.dumps(p.goal_ids), p.notes,
                        _bool_db(p.autodeploy),
                        _bool_db(p.review_gate), _bool_db(p.verify_done),
                        p.browser_gate_mode, p.sandbox_image,
                        p.sandbox_memory, p.sandbox_cpus,
                        1 if p.bench else 0, p.created_at, p.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # Roll back the failed INSERT's implicit transaction — otherwise it
                # stays open on this long-lived connection and holds the write lock
                # until the next commit, starving every other connection's writes
                # (the root cause of the 75s `database is locked` stall, found
                # dogfooding 2026-06-21). pysqlite does NOT auto-rollback here.
                self._db.rollback()
                raise ProjectExists(id) from exc
            self._db.commit()
        return p

    def get(self, project_id: str) -> Optional[Project]:
        with self._lock:
            r = self._db.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return _row_to_project(r) if r else None

    def list(self, *, status: Optional[ProjectStatus] = None) -> list[Project]:
        with self._lock:
            if status:
                rows = self._db.execute(
                    "SELECT * FROM projects WHERE status = ? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM projects ORDER BY updated_at DESC"
                ).fetchall()
        return [_row_to_project(r) for r in rows]

    def update(
        self,
        project_id: str,
        *,
        name: Optional[str] = None,
        repo_url: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        preview_url: Optional[str] = None,
        status: Optional[ProjectStatus] = None,
        notes: Optional[str] = None,
        autodeploy: Optional[bool] = _UNSET,
        review_gate: Optional[bool] = _UNSET,
        verify_done: Optional[bool] = _UNSET,
        browser_gate_mode: Optional[str] = _UNSET,
        sandbox_image: Optional[str] = _UNSET,
        sandbox_memory: Optional[str] = _UNSET,
        sandbox_cpus: Optional[str] = _UNSET,
        bench: Optional[bool] = None,
    ) -> Project:
        """Partial update — only the supplied fields change. Returns the updated
        project. Raises KeyError if unknown. ``updated_at`` always bumps.

        The per-project override fields (``autodeploy``,
        ``autodeploy``, ``review_gate``, ``verify_done``) use three-way
        semantics (unlike the plain fields): omit one entirely to leave the
        current override untouched; pass a concrete value to pin it; pass
        ``None`` explicitly to CLEAR the override back to "inherit the global
        default". The ``_UNSET`` sentinel default is how "don't touch" is
        distinguished from an explicit clear."""
        p = self.get(project_id)
        if p is None:
            raise KeyError(project_id)
        if name is not None:
            p.name = name
        if repo_url is not None:
            p.repo_url = repo_url
        if workspace_dir is not None:
            _validate_workspace_path(workspace_dir)
            p.workspace_dir = workspace_dir
        if preview_url is not None:
            p.preview_url = preview_url
        if status is not None:
            p.status = status
        if notes is not None:
            p.notes = notes
        if autodeploy is not _UNSET:
            p.autodeploy = autodeploy
        if review_gate is not _UNSET:
            p.review_gate = review_gate
        if verify_done is not _UNSET:
            p.verify_done = verify_done
        if browser_gate_mode is not _UNSET:
            p.browser_gate_mode = browser_gate_mode
        if sandbox_image is not _UNSET:
            _validate_sandbox_image(sandbox_image)
            p.sandbox_image = sandbox_image
        if sandbox_memory is not _UNSET:
            _validate_sandbox_memory(sandbox_memory)
            p.sandbox_memory = sandbox_memory
        if sandbox_cpus is not _UNSET:
            _validate_sandbox_cpus(sandbox_cpus)
            p.sandbox_cpus = sandbox_cpus
        if bench is not None:
            p.bench = bool(bench)
        p.updated_at = _now_ms()
        self._save(p)
        return p

    def link_goal(self, project_id: str, goal_id: str) -> Project:
        """Attach a goal to the project (idempotent). Raises KeyError if unknown."""
        p = self.get(project_id)
        if p is None:
            raise KeyError(project_id)
        if goal_id not in p.goal_ids:
            p.goal_ids.append(goal_id)
            p.updated_at = _now_ms()
            self._save(p)
        return p

    def unlink_goal(self, project_id: str, goal_id: str) -> Project:
        p = self.get(project_id)
        if p is None:
            raise KeyError(project_id)
        if goal_id in p.goal_ids:
            p.goal_ids.remove(goal_id)
            p.updated_at = _now_ms()
            self._save(p)
        return p

    def delete(self, project_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            self._db.commit()
            return cur.rowcount == 1

    def _save(self, p: Project) -> None:
        with self._lock:
            self._db.execute(
                """UPDATE projects SET
                     name=?, repo_url=?, workspace_dir=?, preview_url=?, status=?,
                     goal_ids=?, notes=?, autodeploy=?,
                     review_gate=?, verify_done=?, browser_gate_mode=?, sandbox_image=?,
                     sandbox_memory=?, sandbox_cpus=?,
                     bench=?, updated_at=?
                   WHERE id=?""",
                (
                    p.name, p.repo_url, p.workspace_dir, p.preview_url, p.status,
                    json.dumps(p.goal_ids), p.notes,
                    _bool_db(p.autodeploy),
                    _bool_db(p.review_gate), _bool_db(p.verify_done),
                    p.browser_gate_mode, p.sandbox_image,
                    p.sandbox_memory, p.sandbox_cpus,
                    1 if p.bench else 0, p.updated_at, p.id,
                ),
            )
            self._db.commit()

    # ---- managed-repo provenance -------------------------------------------
    # Which GitHub repos devclaw itself stood up (via the create_repo tool).
    # delete_repo consults this ledger and refuses anything not in it, so
    # devclaw can only ever tear down what it created — a pre-existing,
    # human-owned repo (finance-sentry) is structurally undeletable over MCP
    # no matter what confirm string is passed. Slugs are stored lowercased
    # because GitHub slugs are case-insensitive.

    def record_managed_repo(self, slug: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO managed_repos (slug, created_at) VALUES (?, ?)",
                (slug.lower(), _now_ms()),
            )
            self._db.commit()

    def is_managed_repo(self, slug: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM managed_repos WHERE slug = ?", (slug.lower(),)
            ).fetchone()
        return row is not None

    def forget_managed_repo(self, slug: str) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM managed_repos WHERE slug = ?", (slug.lower(),)
            )
            self._db.commit()

    def resolve_override(self, project_id: Optional[str], field: str, default: Any) -> Any:
        """Resolve one per-project override for a goal/task belonging to
        ``project_id``: the owning project's value for ``field`` if it pins one
        (non-null), else ``default`` (the devclaw-wide env default). This is the
        single generic seam every override consumer routes through — a goal has
        no such setting of its own, only its owning project does.

        Keyed by the project reference key (#524 P3), NOT by a normalized
        workspace-path scan: a project's ``workspace_dir`` can now be renamed
        without silently unbinding its knobs, and two projects can't collide on a
        shared path. ``project_id`` may be None (a self-fix goal with no
        owning project) → falls to ``default``.

        ``field`` must be one of :data:`_OVERRIDE_FIELDS`; anything else is a
        programming error and raises, rather than silently returning the
        default and masking a typo."""
        if field not in _OVERRIDE_FIELDS:
            raise ValueError(f"not a per-project override field: {field!r}")
        if not project_id:
            return default
        project = self.get(project_id)
        if project is not None:
            value = getattr(project, field)
            if value is not None:
                return value
        return default

    def resolve_dispatch(self, project_id: str) -> ResolvedDispatch:
        """Resolve a dispatch reference key to its concrete workspace + repo.
        The registry is the single source of truth for dispatch (spec 003 /
        #520): callers name a ``project_id`` and never a raw path, and devclaw
        resolves the target here at the one place every dispatch tool crosses.

        Raises :class:`UnknownProject` (a KeyError) if the id is not registered —
        the tool layer turns that into a synchronous ToolError with zero task/
        engine work. Raises ValueError if the row carries no ``workspace_dir`` to
        run in. Does NOT check that the workspace exists on disk — that is the
        dispatch-time preflight's job (a loud reject at admission, not here)."""
        p = self.get(project_id)
        if p is None:
            raise UnknownProject(project_id)
        if not p.workspace_dir:
            raise ValueError(
                f"project {project_id!r} has no workspace_dir — set one via "
                f"update_project before dispatching to it"
            )
        return ResolvedDispatch(
            workspace_dir=p.workspace_dir, repo_url=p.repo_url, project_id=project_id
        )


def _normalize_workspace(path: Optional[str]) -> Optional[str]:
    """Canonicalize workspace paths for join purposes: strip trailing slash,
    collapse duplicate slashes, expand user. Stays purely string-shaped — we
    do NOT hit the filesystem here (projects may point at paths that don't
    exist on this host, e.g. the CLI reading a VPS registry snapshot)."""
    if not path:
        return None
    p = str(path).strip()
    if not p:
        return None
    # Expand a leading ~ without resolving symlinks/existence.
    if p.startswith("~"):
        p = str(Path(p).expanduser())
    # Collapse `//` runs and drop any trailing slash (except root).
    while "//" in p:
        p = p.replace("//", "/")
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def project_rollup(project: Project, all_goals: list[dict]) -> dict:
    """Join a project with live goal state via ``project_id`` match (#524 P3).

    ``all_goals`` is the pre-fetched output of ``goal_service.list_goals()``
    (or the CLI's GoalStore-backed equivalent). Every goal whose stored
    ``project_id`` equals the project's id is associated. Passing the full list
    in from the caller lets us render every project in a single ``list_goals``
    scan instead of an N-times per-project fetch. (Was a normalized-workspace-dir
    match — re-keyed to the project reference key so a workspace rename or a
    shared path can't drift the association.)

    ``health`` is a cheap derived signal for the control plane: ``blocked`` if
    any goal is blocked or flagged stalled by the watchdog, ``done`` if all
    goals are done, ``working`` if any is active, else ``idle``."""
    goals: list[dict] = []
    for g in all_goals:
        if g.get("project_id") != project.id:
            continue
        goals.append(
                {
                    "id": g.get("id"),
                    "phase": g.get("phase"),
                    "lifecycle": g.get("lifecycle"),
                    "blocked_on": g.get("blocked_on"),
                    "progress": g.get("progress"),
                    "direction": g.get("direction"),
                }
            )
    out = project.to_dict()
    out["goals"] = goals
    out["health"] = _health(project.status, goals)
    return out


def _health(status: ProjectStatus, goals: list[dict]) -> str:
    if status == "archived":
        return "archived"
    live = [g for g in goals if not g.get("missing")]
    if not live:
        return "idle"
    phases = [g.get("phase") for g in live]
    stalled = any((g.get("progress") or {}).get("stalled") for g in live)
    if "blocked" in phases or stalled:
        return "blocked"
    if all(p == "done" for p in phases):
        return "done"
    if any(p in ("in_flight", "verifying", "idle") for p in phases):
        return "working"
    return "idle"
