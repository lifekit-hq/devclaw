"""Decode raw OpenHands worker events into a readable, turn-by-turn trace.

The worker (openhands-runner) emits one ``event:`` line per SDK turn; the host
stores each as a ``TaskEvent`` whose ``payload_json`` is that event's
``model_dump`` (``llm_message`` for a message, the tool/args for an action, the
output for an observation). The console's existing ``_event_kind`` maps only the
event *type* to a coarse bucket and never extracts the *content* — so a
``MessageEvent`` renders as the blip "MessageEvent" with an empty preview, and a
settled task has no readable trace at all.

This module is the missing reader — the mirror of what ``trace_view`` does for
cognition transcripts (#455), one layer down at the worker. It turns each event
into ``{kind, title, summary, detail, raw}`` so the owner can watch what the
agent actually did, turn by turn. It is:

  * **pure** — no I/O, trivially testable;
  * **defensive** — the exact OpenHands payload shapes are SDK internals and may
    drift, so every extractor best-efforts a known field then falls back to a
    pretty JSON dump. It NEVER raises and NEVER hides content: ``raw`` always
    carries the full untruncated payload, exactly the #455 "show the whole thing"
    guarantee.
"""

from __future__ import annotations

import json
from typing import Any, Iterable


def _safe_load(payload_json: str) -> dict:
    """Parse a stored ``payload_json`` cell to a dict; tolerate corruption."""
    try:
        obj = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return {"_unparseable": payload_json}
    return obj if isinstance(obj, dict) else {"_value": obj}


def _first_str(d: Any, keys: Iterable[str]) -> str:
    """First key in ``keys`` whose value on ``d`` is a non-empty string."""
    if not isinstance(d, dict):
        return ""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _message_text(payload: dict) -> str:
    """Plain text of a MessageEvent — the same ``llm_message.content[].text``
    shape the runner's ``_agent_message_text`` reads (kept in sync with it)."""
    msg = payload.get("llm_message") if isinstance(payload, dict) else None
    if not isinstance(msg, dict):
        # some message events carry the text directly
        return _first_str(payload, ("text", "content", "message"))
    parts: list[str] = []
    for item in msg.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts)


def _pretty(payload: Any) -> str:
    """Readable full dump — the never-hide-anything fallback."""
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(payload)


def _one_line(text: str, limit: int = 200) -> str:
    """First line of ``text``, whitespace-collapsed and length-capped, for the
    index/preview. The full text stays in ``detail``."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _acp_content_text(content: Any) -> str:
    """Human-readable text out of an ACP tool-call ``content`` block — a list of
    ``{"content": {"text": ...}}`` (or ``{"text": ...}``) items the ACP worker
    emits as a tool's OUTPUT. Best-effort: unknown shapes contribute nothing
    rather than raising (the raw payload is always preserved on ``raw``)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        inner = item.get("content")
        if isinstance(inner, dict):
            txt = inner.get("text")
        elif isinstance(inner, str):
            txt = inner
        else:
            txt = item.get("text")
        if isinstance(txt, str) and txt.strip():
            parts.append(txt)
    return "\n".join(parts)


def _classify(etype: str, source: str, payload: dict) -> tuple[str, str, str]:
    """(kind, title, detail) for one event. ``kind`` is one of message / action /
    observation / error / other — the execution-level taxonomy, not the mock's
    cognition/subprocess/dispatch buckets."""
    t = (etype or "").lower()
    # ACP tool call — the dominant worker event on the ACP path (Claude Code).
    # Its type ("ACPToolCallEvent") contains none of the words below, so without
    # this branch it fell through to "other" and dumped raw JSON. The payload
    # already carries a human ``title`` ("Read Program.cs") and the tool OUTPUT
    # under ``content`` — pull both out so the Execution trace reads like an
    # engineer's turn, not a JSON blob.
    if "toolcall" in t or (isinstance(payload, dict) and payload.get("tool_call_id")):
        tool_title = _first_str(payload, ("title", "tool_name", "tool", "name")) or "tool call"
        out = _acp_content_text(payload.get("content"))
        args = payload.get("raw_input")
        # Output first so the one-line summary previews the RESULT (a file's text,
        # a command's stdout); the input args follow in the expanded detail.
        parts: list[str] = []
        if out:
            parts.append(out)
        if isinstance(args, dict) and args:
            parts.append("input:\n" + _pretty(args))
        elif isinstance(args, str) and args.strip():
            parts.append("input: " + args)
        # A just-initiated (pending) call has no output/args yet — the human
        # ``title`` already carries it, so leave detail empty rather than dumping
        # raw JSON. The full payload is always on ``raw`` (the never-hide guarantee).
        detail = "\n\n".join(parts)
        kind = "error" if payload.get("is_error") else "action"
        return kind, tool_title, detail
    if "systemprompt" in t:
        return "other", "system prompt", _first_str(payload, ("system_prompt", "content", "text", "prompt")) or _pretty(payload)
    if "message" in t:
        who = source or "agent"
        return "message", f"{who} message", _message_text(payload) or _pretty(payload)
    if "action" in t:
        thought = _first_str(payload, ("thought", "reasoning"))
        act = payload.get("action")
        tool = _first_str(payload, ("tool_name", "tool", "name")) or (
            _first_str(act, ("tool_name", "tool", "name", "kind")) if isinstance(act, dict)
            else (act if isinstance(act, str) else "")
        )
        cmd = _first_str(payload, ("command", "code", "content")) or (
            _first_str(act, ("command", "code", "content")) if isinstance(act, dict) else ""
        )
        title = f"action: {tool}" if tool else "action"
        parts = [p for p in (f"thought: {thought}" if thought else "", cmd) if p]
        return "action", title, "\n\n".join(parts) if parts else _pretty(payload)
    if "observation" in t:
        out = _first_str(payload, ("content", "output", "observation", "result", "stdout", "message"))
        return "observation", "observation", out or _pretty(payload)
    if "error" in t or (isinstance(payload, dict) and payload.get("error")):
        return "error", etype or "error", _first_str(payload, ("message", "error", "detail")) or _pretty(payload)
    return "other", etype or "event", _pretty(payload)


def decode_event(ev: Any) -> dict:
    """Decode one ``TaskEvent`` into a readable row.

    ``ev`` needs ``.id``, ``.ts``, ``.type``, ``.source``, ``.payload_json``.
    Returns a flat dict the console renders directly:
      ``id`` · ``ts`` · ``type`` (raw SDK class) · ``source`` · ``kind`` ·
      ``title`` (short label) · ``summary`` (one-line preview) ·
      ``detail`` (full extracted text) · ``raw`` (full untruncated payload).
    """
    payload = _safe_load(getattr(ev, "payload_json", "") or "")
    etype = getattr(ev, "type", "") or ""
    source = getattr(ev, "source", "") or ""
    kind, title, detail = _classify(etype, source, payload)
    return {
        "id": getattr(ev, "id", None),
        "ts": getattr(ev, "ts", None),
        "type": etype,
        "source": source,
        "kind": kind,
        "title": title,
        "summary": _one_line(detail) or title,
        "detail": detail,
        "raw": payload,
    }
