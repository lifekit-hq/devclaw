"""The four always-hard gates over ONE materialized span (specs 013, 032, 046).

A gate is a pure verdict producer: it reads the run artifacts and returns a
verdict, never writes state. The pipeline short-circuits on the first failure.
Every gate fails CLOSED — a crash is never an approval (#186).

  verify        the sandbox's own verify run (a red run never ships)
  materialize   the span itself could be determined (an undeterminable span
                is not an empty one)
  change_class  no gate-input edit, no committed binary (the verdict of record
                is the project's CI; a worker never edits what CI reads)
  integrity     the change did not delete or skip tests to go green
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Sequence

from .loom.test_integrity import present_test_names, scan_diff

if TYPE_CHECKING:
    from .task_change import ChangeSet

CHANGE_CLASS_MARKER = "change_class:"


@dataclass
class GateInput:
    workspace_dir: str
    #: the runner's ``verify`` sub-result (``{"ran": bool, "passed": bool, ...}``)
    verify: Optional[dict]
    #: async producer of the materialized span — called at most once
    change_fn: Callable[[], Awaitable["ChangeSet"]]
    _change: object = field(default=None, repr=False)
    _computed: bool = field(default=False, repr=False)

    async def change(self) -> "ChangeSet":
        if not self._computed:
            self._change = await self.change_fn()
            self._computed = True
        return self._change  # type: ignore[return-value]


@dataclass(frozen=True)
class GateVerdict:
    gate_id: str
    ok: bool
    reason: str = ""


def _verify_failure_summary(verify: dict) -> str:
    cmd = verify.get("cmd", "")
    if verify.get("timed_out"):
        head = f"verify timed out: `{cmd}`"
    else:
        head = f"verify failed (exit {verify.get('exit_code')}): `{cmd}`"
    out = (verify.get("output") or "").strip()
    return f"{head}\n{out[-1500:]}" if out else head


async def verify_gate(gi: GateInput) -> GateVerdict:
    v = gi.verify
    if v and v.get("ran") and not v.get("passed"):
        return GateVerdict("verify", False, _verify_failure_summary(v))
    return GateVerdict("verify", True)


async def materialize_gate(gi: GateInput) -> GateVerdict:
    try:
        change = await gi.change()
    except Exception as err:  # noqa: BLE001 — undeterminable ⇒ fail closed
        return GateVerdict("materialize", False,
                           f"the change could not be determined ({err.__class__.__name__}: {err})")
    if change.is_error:
        return GateVerdict("materialize", False, f"the change could not be determined: {change.reason}")
    return GateVerdict("materialize", True)


async def change_class_gate(gi: GateInput) -> GateVerdict:
    change = await gi.change()
    if not change.is_change:
        return GateVerdict("change_class", True)
    if change.gate_input_paths:
        return GateVerdict(
            "change_class", False,
            f"{CHANGE_CLASS_MARKER} gate-input edit(s) {', '.join(change.gate_input_paths)} — "
            "a session never edits what the project's CI reads (workflows, AGENTS.md, "
            "test-runner/build configuration, install scripts, toolchain pins). Revert them; "
            "if the ticket is ABOUT those files it names the path in scope.",
        )
    if change.binary_paths:
        return GateVerdict(
            "change_class", False,
            f"{CHANGE_CLASS_MARKER} committed binary file(s) {', '.join(change.binary_paths)} — "
            "binaries never ship from a sandbox. Remove them.",
        )
    return GateVerdict("change_class", True)


def _integrity_failure(diff: str, workspace_dir: Optional[str] = None) -> Optional[str]:
    """A weakened test suite (deleted/skipped tests) — a removed test whose
    name still exists elsewhere in the tree is a move, not a weakening."""
    try:
        report = scan_diff(diff)
    except Exception as err:  # noqa: BLE001 — fail closed
        return f"test-integrity scan crashed (failing closed): {err.__class__.__name__}: {err}"
    if report.ok:
        return None
    if report.removed_tests > 0 and report.removed_names and workspace_dir:
        try:
            present = present_test_names(workspace_dir)
        except Exception:  # noqa: BLE001
            present = set()
        credited = min(report.removed_tests, len({n for n in report.removed_names if n in present}))
        report.removed_tests = max(0, report.removed_tests - credited)
    if report.ok:
        return None
    return (
        f"{report.summary()}. The change weakened the test suite — restore the tests "
        "and make the code genuinely pass them; never delete, skip, or gut tests to go green."
    )


async def integrity_gate(gi: GateInput) -> GateVerdict:
    change = await gi.change()
    failure = _integrity_failure(change.diff, gi.workspace_dir)
    if failure is not None:
        return GateVerdict("test_integrity", False, failure)
    return GateVerdict("test_integrity", True)


GATES: Sequence[Callable[[GateInput], Awaitable[GateVerdict]]] = (
    verify_gate, materialize_gate, change_class_gate, integrity_gate,
)


async def run_gates(gi: GateInput, gates=GATES) -> Optional[GateVerdict]:
    """First failing verdict, or None when every gate passed."""
    for gate in gates:
        verdict = await gate(gi)
        if not verdict.ok:
            return verdict
    return None
