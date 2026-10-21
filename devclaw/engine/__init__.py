"""The engine seam.

DevClaw's orchestration (task queue, planner, state store) drives an *Engine* —
the thing that actually executes one coding task and streams back events.
``claude -p`` in the goal's worktree is the only implementation today
(:func:`devclaw.engine.claude.run_claude`), but the orchestration depends ONLY
on this interface, never on the agent behind it.

That's the "orchestration ⊥ engine" decoupling from the architecture: the
engine is pinned but swappable behind one method, and the
orchestration stays testable with a stub engine (the tests inject one).

An Engine is any async callable ``(EngineRequest) -> EngineResult`` — a plain
async function satisfies it, which is why ``run_claude`` is one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Union

from ..state_store import TaskKind


@dataclass
class EngineEvent:
    """One observation streamed by the engine while a task runs — one
    stream-json frame of the session, or a verify verdict."""

    id: Optional[str]
    type: str
    source: str
    ts: Union[int, str]
    payload: object


@dataclass
class EngineRequest:
    """Inputs to execute one task. The same kinds the MCP tool surface exposes;
    the engine picks the right behavior per kind."""

    kind: TaskKind
    workspace_dir: str
    goal: str
    #: optional callback, invoked once per :class:`EngineEvent` the engine emits
    on_event: Optional[Callable[[EngineEvent], None]] = None
    #: optional verify gate — a shell command the engine runs in the workspace
    #: AFTER the agent finishes. Its exit code is the real definition of "done"
    #: (the agent's own "I'm finished" is not trusted). None → no gate.
    verify_cmd: Optional[str] = None
    #: the Claude Code session to continue (``--resume``) — the row's own
    #: session after a quota pause kept its tree. None → a fresh session. A resume
    #: id the agent no longer knows falls back to a fresh session.
    resume_session_id: Optional[str] = None
    #: optional callback, invoked once with the session id as soon as the
    #: stream names it — persisted so a quota-paused session can be resumed.
    on_session: Optional[Callable[[str], None]] = None


#: Terminal verdict from one task. ``status == "ok"`` carries
#: ``workspaceDir``/``message`` (+ ``agent_output`` — the agent's final
#: message: the structured hand-back for code kinds, the report for reviews);
#: ``status == "error"`` carries ``error`` (+ optional ``trace``);
#: ``status == "rate_limited"`` carries ``error`` + ``retry_after`` (the host
#: pauses-and-resumes); ``status == "blocked"`` carries ``reason`` — the worker's
#: honest self-report that it genuinely cannot finish (missing capability,
#: contradictory/impossible instructions) — typed by ``block_kind``: ``"env"``
#: (``block_item`` names what the environment lacks; spec 032 US2 — the pipeline
#: holds the project) or ``"contract"`` (the owner is asked). A ``blocked``
#: verdict is NOT an approval: the host fails it CLOSED and does NOT retry
#: (a re-run re-blocks identically), surfacing the reason instead. When a ``verify_cmd`` ran,
#: ``result["verify"]`` carries the gate verdict
#: ``{ran, cmd, passed, exit_code, timed_out, output}`` — the orchestration
#: (not the engine) decides done-vs-failed from ``passed``. Any status may
#: carry ``session_id`` — the session that ran, resumable after an interruption.
EngineResult = dict


class Engine(Protocol):
    """Anything that can execute one task. A plain ``async def f(request)`` works."""

    async def __call__(self, request: EngineRequest, /) -> EngineResult: ...
