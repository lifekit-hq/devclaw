"""The done-gate (spec 046, pillar 7): "done" is never the agent's word.

A session that proposes DONE gets a fresh, read-only review session over the
delivered head against the live contract. The reviewer answers in JSON; THIS
module validates it mechanically — achieved only when every clause is
satisfied with evidence — and renders the verdict as a PR comment so the
world carries it. No model call happens on the host.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import github as _gh

_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class Verdict:
    achieved: bool
    clauses: tuple[dict, ...] = ()
    question: str = ""
    summary: str = ""
    structural_health: str = ""
    concerns: tuple[str, ...] = ()
    #: the review produced no readable JSON — a devclaw defect, never a pass
    unreadable: bool = False
    raw_error: str = ""

    def to_dict(self) -> dict:
        return {"achieved": self.achieved, "clauses": list(self.clauses), "question": self.question,
                "summary": self.summary, "structural_health": self.structural_health,
                "concerns": list(self.concerns), "unreadable": self.unreadable}


def parse_verdict(agent_output: str) -> Verdict:
    """Read the reviewer's JSON; validate the achieved claim against the
    clauses. Anything unreadable is ``unreadable`` (fails closed)."""
    matches = _JSON_FENCE_RE.findall(agent_output or "")
    raw = matches[-1] if matches else None
    if raw is None:
        # a bare object at the end of the message, last resort
        m = re.search(r"\{.*\}", agent_output or "", re.DOTALL)
        raw = m.group(0) if m else None
    if raw is None:
        return Verdict(False, unreadable=True, raw_error="no JSON verdict in the review")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return Verdict(False, unreadable=True, raw_error=f"unparseable JSON: {exc}")
    if not isinstance(data, dict):
        return Verdict(False, unreadable=True, raw_error="verdict is not an object")
    clauses_raw = data.get("clauses")
    clauses: list[dict] = []
    if isinstance(clauses_raw, list):
        for c in clauses_raw:
            if isinstance(c, dict):
                clauses.append({
                    "clause": str(c.get("clause") or "").strip(),
                    "satisfied": bool(c.get("satisfied")),
                    "evidence": str(c.get("evidence") or "").strip(),
                })
    if not clauses:
        return Verdict(False, unreadable=True, raw_error="verdict names no clauses")
    all_ok = all(c["satisfied"] and c["evidence"] for c in clauses)
    question = str(data.get("question") or "").strip()
    # the mechanical rule: the claim never beats the clauses, and a question
    # is not an achievement
    achieved = bool(data.get("achieved")) and all_ok and not question
    concerns_raw = data.get("concerns")
    concerns: list = concerns_raw if isinstance(concerns_raw, list) else []
    return Verdict(
        achieved=achieved, clauses=tuple(clauses), question=question,
        summary=str(data.get("summary") or "").strip(),
        structural_health=str(data.get("structural_health") or "").strip(),
        concerns=tuple(str(x) for x in concerns),
    )


def render_verdict(verdict: Verdict, *, head: str, task_id: str) -> str:
    """The PR comment: the record the world carries (marker + human text)."""
    achieved = "1" if verdict.achieved else "0"
    lines = [_gh.marker("verdict", head=head, task=task_id, achieved=achieved,
                        unreadable="1" if verdict.unreadable else "0")]
    if verdict.unreadable:
        lines += ["**devclaw done-gate: the review produced no readable verdict**",
                  "", verdict.raw_error]
        return "\n".join(lines)
    lines.append("**devclaw done-gate: " + ("ACHIEVED" if verdict.achieved else "NOT achieved") + f"** (head `{head[:10]}`)")
    if verdict.summary:
        lines += ["", verdict.summary]
    lines += ["", "| clause | satisfied | evidence |", "|---|---|---|"]
    for c in verdict.clauses:
        lines.append(f"| {c['clause'][:200]} | {'✅' if c['satisfied'] else '❌'} | {c['evidence'][:300]} |")
    if verdict.question:
        lines += ["", f"**Question for the owner:** {verdict.question}"]
    if verdict.structural_health and verdict.structural_health != "clean":
        lines += ["", f"Structural health: **{verdict.structural_health}**"]
        lines += [f"- {c}" for c in verdict.concerns[:10]]
    if not verdict.achieved:
        lines += ["", "The goal stops here (no correction round). Reply mentioning the bot with how to proceed."]
    return "\n".join(lines)


def render_block(*, task_id: str, head: str, kind: str, text: str, default: str = "",
                 options: "list[str] | tuple[str, ...]" = (), recommended: int = -1) -> str:
    lines = [_gh.marker("block", task=task_id or "-", head=head or "-", why=kind.replace(" ", "_")),
             f"**devclaw stopped: {kind}**", "", text]
    if options:
        lines.append("")
        for i, o in enumerate(options):
            lines.append(f"- ({chr(97 + i)}) {o}" + (" — the session would take this" if i == recommended else ""))
    if default:
        lines += ["", f"Default the session would take: {default}"]
    lines += ["", "Reply on this thread mentioning the bot to continue, or cancel the goal."]
    return "\n".join(lines)


def block_fields(result_json: "str | None", exit_detail: "str | None") -> dict:
    """The session's block as the runner parsed it (spec 047: ONE parser, in
    the runner); a row without the fields reads as question-only."""
    try:
        r = json.loads(result_json or "{}")
    except ValueError:
        r = {}
    if not isinstance(r, dict):
        r = {}
    raw_options = r.get("options")
    options: list = raw_options if isinstance(raw_options, list) else []
    rec = r.get("recommended", -1)  # 0 is a real index, never "absent"
    return {
        "question": str(r.get("question") or exit_detail or "").strip(),
        "options": [str(o) for o in options],
        "default": str(r.get("default") or ""),
        "recommended": rec if isinstance(rec, int) and not isinstance(rec, bool) else -1,
        "kind": str(r.get("block_kind") or "contract"),
        "item": str(r.get("block_item") or ""),
    }


__all__ = ["Verdict", "parse_verdict", "render_verdict", "render_block", "block_fields", "field"]
