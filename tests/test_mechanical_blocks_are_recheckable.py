"""Structural tripwire: a ``mechanical:*`` block always has a way back.

``blocked_kind="mechanical:<site>"`` is a PROMISE — the condition is cheaply
re-checkable without an LLM, so the goal is parked, not abandoned. A mechanical
kind with neither an auto-heal branch nor a declared human-gated disposition
breaks that promise silently: the goal parks forever, ``resume_goal`` re-enters
the same condition, and nothing in the stubbed suite notices because no test
asserts the absence of a code path.

That is not hypothetical. ``mechanical:slice_hold`` shipped with no heal branch
and held two finance-sentry goals for four days
(specs/tiny/slice-guard-observes-the-goal); the same scan caught
``mechanical:corrupt_doc``, whose raise site had documented a self-heal since
spec 021 FR-004 that was never wired.

This guard is deliberately structural rather than behavioural: it reads the
source, so it covers kinds no test happens to exercise — which is exactly the
class that bit us.
"""
from __future__ import annotations

import ast
import pathlib

from devclaw.goal.tick import HUMAN_GATED_MECHANICAL_KINDS

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "devclaw"
_TICK = _PKG / "goal" / "tick.py"


def _written_mechanical_kinds() -> set[str]:
    """Every ``mechanical:*`` literal the package can assign to a blocked_kind.

    Scans string constants rather than ``blocked_kind=`` keywords on purpose:
    a kind routed through a variable or a helper still reaches the store, and
    the point is to catch the kind that ships without anyone wiring its exit.
    The bare ``"mechanical:"`` prefix used for ``startswith`` classification is
    not a kind and is excluded.
    """
    kinds: set[str] = set()
    for path in _PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("mechanical:")
                and node.value != "mechanical:"
            ):
                kinds.add(node.value)
    return kinds


def _healed_mechanical_kinds() -> set[str]:
    """Kinds tick.py's auto-heal dispatch actually branches on.

    Reads the ``status.blocked_kind == "mechanical:x"`` comparisons in the heal
    block, so a branch deleted in a refactor stops counting immediately.
    """
    tree = ast.parse(_TICK.read_text(encoding="utf-8"), filename=str(_TICK))
    healed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.comparators) != 1:
            continue
        left, right = node.left, node.comparators[0]
        if (
            isinstance(left, ast.Attribute)
            and left.attr == "blocked_kind"
            and isinstance(right, ast.Constant)
            and isinstance(right.value, str)
            and right.value.startswith("mechanical:")
        ):
            healed.add(right.value)
    return healed


def test_every_mechanical_kind_heals_or_is_declared_human_gated() -> None:
    written = _written_mechanical_kinds()
    healed = _healed_mechanical_kinds()
    stranded = sorted(written - healed - set(HUMAN_GATED_MECHANICAL_KINDS))
    assert not stranded, (
        f"mechanical block kind(s) with no way back: {stranded}. "
        "`mechanical:` promises a cheap re-check, so each kind must either get "
        "an auto-heal branch in devclaw/goal/tick.py or be added to "
        "tick.HUMAN_GATED_MECHANICAL_KINDS with a reason a human must act. "
        "A kind with neither parks its goals permanently."
    )


def test_human_gated_declaration_has_no_dead_entries() -> None:
    """The declaration is a fact about the code, so it rots the moment a kind
    is retired. A stale entry would silently license a future kind of the same
    name to ship with no heal."""
    written = _written_mechanical_kinds()
    dead = sorted(set(HUMAN_GATED_MECHANICAL_KINDS) - written)
    assert not dead, (
        f"HUMAN_GATED_MECHANICAL_KINDS names kind(s) nothing writes: {dead}. "
        "Remove them — the list must describe the code as it is."
    )


def test_a_kind_that_heals_is_not_also_declared_human_gated() -> None:
    """Both dispositions at once is a contradiction: the heal would clear a
    block the declaration says only a human may clear."""
    both = sorted(_healed_mechanical_kinds() & set(HUMAN_GATED_MECHANICAL_KINDS))
    assert not both, (
        f"kind(s) both auto-healed and declared human-gated: {both}. "
        "Pick one — the auto-heal wins at runtime, so the declaration is a lie."
    )


def test_the_retired_slice_hold_kind_is_gone() -> None:
    """Named regression: the brake whose dead end motivated this guard must not
    come back without an exit. Retired by
    specs/tiny/slice-guard-observes-the-goal."""
    assert "mechanical:slice_hold" not in _written_mechanical_kinds(), (
        "mechanical:slice_hold was retired — the dispatch boundary no longer "
        "predicts build-ahead from sibling spec dirs."
    )


# --- the ROW level: a capability row inside a healable kind ------------------
# The guards above are about the block KIND. `mechanical:env` has a heal
# branch, so it passes them — and three goals still parked for days on a
# credential the instance already had (specs/tiny/env-hold-defers-to-a-live-
# probe). The unreckeckable thing was one level down, in the capability row a
# worker reports from prose. These pin that level.

import json  # noqa: E402

import pytest  # noqa: E402

from devclaw import credentials as _credentials  # noqa: E402
from devclaw import env_cap  # noqa: E402


class _Meta:
    """Minimal MetaStore double — env_cap reads and writes nothing else."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get_meta(self, key: str) -> str:
        return self._d.get(key, "")

    def set_meta(self, key: str, value: str) -> None:
        self._d[key] = value


def _seed_worker_gap(store: _Meta, item: str, project_id: str | None = "proj") -> str:
    return env_cap.record_worker_deficiency(store, project_id, item)


def _seed_probe(store: _Meta, cap_id: str, status: str) -> None:
    store.set_meta(
        env_cap._meta_key(cap_id, None),  # noqa: SLF001 — instance-scoped row
        json.dumps({"status": status, "evidence": "seeded", "remedy": ""}),
    )


def test_every_superseding_mapping_targets_a_probeable_capability() -> None:
    """A mapping whose target has no probe runner can never read green, so it
    would silently mean 'never supersedes' — the exact shape of the bug this
    fixes, re-introduced one level up."""
    unprobeable = sorted(
        target for _var, target in env_cap._SUPERSEDING_CREDENTIALS  # noqa: SLF001
        if target not in env_cap._PROBE_RUNNERS  # noqa: SLF001
    )
    assert not unprobeable, (
        f"superseding mapping(s) target a capability with no probe runner: "
        f"{unprobeable}. The supersede reads that capability's persisted probe "
        "result; without a runner the row is never written and the worker's "
        "report holds forever."
    )


def test_every_superseding_mapping_names_a_registered_credential() -> None:
    """The map is registry-driven by contract (R2): a hand-typed name here
    would be a second spelling of a credential and would drift."""
    registered = {c.var for c in _credentials.REGISTRY}
    stray = sorted(
        var for var, _t in env_cap._SUPERSEDING_CREDENTIALS  # noqa: SLF001
        if var not in registered
    )
    assert not stray, (
        f"superseding mapping(s) name credential(s) not in the registry: {stray}."
    )


@pytest.mark.parametrize(
    "probe_status, still_red",
    [
        ("green", False),   # the fact arrived — the report stops holding
        ("red", True),      # the gap is real — hold, exactly as before
        ("unknown", True),  # unrunnable probe is not evidence (FR-007)
        (None, True),       # never probed — hold
    ],
)
def test_a_worker_gap_naming_a_registry_credential_defers_to_its_probe(
    probe_status: "str | None", still_red: bool,
) -> None:
    """The class: a brake must be able to observe its own release condition.
    A worker's prose naming NODE_AUTH_TOKEN is answered by the credential's own
    probe — the one doctor runs and the sweep refreshes — instead of waiting
    for a human to type resume_goal."""
    store = _Meta()
    cap_id = _seed_worker_gap(
        store,
        f"{_credentials.REGISTRY_TOKEN.var} (GitHub token with `read:packages`) "
        "is absent, so `npm ci` 401s on `@lifekit-hq/*`",
    )
    if probe_status is not None:
        _seed_probe(store, env_cap.CAP_REGISTRY_NPM_GITHUB, probe_status)

    red = env_cap.red_caps_for(store, (), "proj")

    assert bool(red) is still_red, (
        f"worker row {cap_id} with a {probe_status!r} credential probe: "
        f"expected {'held' if still_red else 'superseded'}, got {red!r}"
    )


def test_a_worker_gap_naming_no_registry_credential_still_waits_for_a_human() -> None:
    """The human-only exit is narrowed, not removed: prose devclaw cannot check
    holds until resume_goal, and a green credential probe elsewhere on the
    instance must not release it."""
    store = _Meta()
    _seed_worker_gap(store, "a human must click Approve in the vendor console")
    _seed_probe(store, env_cap.CAP_REGISTRY_NPM_GITHUB, "green")

    assert env_cap.red_caps_for(store, (), "proj"), (
        "an unmappable worker report was released by an unrelated green probe"
    )
    assert env_cap.clear_worker_deficiencies(store, "proj"), "resume_goal still clears it"
    assert not env_cap.red_caps_for(store, (), "proj")
