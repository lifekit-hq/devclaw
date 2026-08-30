"""Minimal ACP client — the runner's agent-drive seam (spec 011).

Speaks newline-delimited JSON-RPC 2.0 over the agent subprocess's stdio to
any ACP agent (default: claude-agent-acp). Zero dependencies by design: the
runner must be importable and testable with no SDK installed, and the one
protocol library we ever pinned transitively (agent-client-protocol 0.11.0's
prompt() signature flip) already broke every sandbox turn once. The client
implements ONLY what the runner needs — initialize, one session, one prompt
turn, streamed updates, permission auto-grant, teardown — and fails loud on
anything else (spec 011 FR-011).

Concurrency model: single-threaded blocking read loop (research D2). The
runner is strictly sequential, so after sending a request we pump incoming
lines — dispatching `session/update` notifications and inbound agent
requests inline — until that request's response arrives.

Permission policy (clarified 2026-08-19): every `session/request_permission`
is auto-granted (the docker sandbox is the security boundary; delivery is
gated host-side). The grant never blocks.
"""

from __future__ import annotations


import json
import os
import select
import subprocess
import time

#: ACP protocol version this client requests at `initialize`.
PROTOCOL_VERSION = 1

#: Default seconds of total protocol+stderr silence before the agent is
#: declared hung, killed, and the task failed loud. Generous on purpose: one
#: long-running quiet tool call (a full test suite inside the agent's turn)
#: is legitimate silence; the host's container timeout is the hard backstop.
DEFAULT_IDLE_TIMEOUT_S = 1800

#: Bounded agent-stderr ring buffer — fallback material for the runner's
#: `_agent_last_words` when the agent dies without a final message.
_STDERR_KEEP = 16_384

_READ_CHUNK = 65_536


class AcpError(RuntimeError):
    """A protocol-level failure: malformed frame, agent death, JSON-RPC error
    response, or idle timeout. The message text is preserved verbatim — the
    runner's `_detect_usage_limit` and the host classifier regex it."""


class PromptOutcome:
    """What one `session/prompt` turn produced (data-model.md). A plain class
    on purpose — this module is loaded via ``spec_from_file_location`` (no
    ``sys.modules`` entry), where dataclass annotation resolution breaks."""

    def __init__(
        self,
        stop_reason: str,
        last_agent_message: str,
        usage: dict | None,
        stderr_tail: str,
    ) -> None:
        self.stop_reason = stop_reason
        self.last_agent_message = last_agent_message
        self.usage = usage
        self.stderr_tail = stderr_tail


# --- usage extraction (research D6) -----------------------------------------
# ACP 0.x standardizes no token-usage report. This tolerant extractor keeps
# the door open: any update (or its _meta) carrying a usage-shaped dict is
# accumulated; nothing recognizable → the result omits `usage` entirely
# (declared-absent, never fabricated). All-zero reads as "unknown", not free.

_USAGE_FIELD_ALIASES = {
    "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
    "output_tokens": ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
    "cache_read_tokens": ("cache_read_tokens", "cacheReadTokens", "cache_read_input_tokens"),
    "cost_usd": ("cost_usd", "costUsd", "total_cost_usd", "totalCostUsd"),
}


def _usage_shaped(obj: object) -> dict | None:
    """The canonical-keyed usage numbers in ``obj``, or None if it carries no
    recognizable usage field. Best-effort, never raises."""
    if not isinstance(obj, dict):
        return None
    out: dict = {}
    for canon, aliases in _USAGE_FIELD_ALIASES.items():
        for alias in aliases:
            v = obj.get(alias)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[canon] = v
                break
    return out or None


def accumulate_usage(acc: dict, params: object) -> None:
    """Fold any usage report found in one ``session/update``'s params into
    ``acc``. Looks at ``update.usage``, ``params.usage`` and ``_meta.usage``."""
    if not isinstance(params, dict):
        return
    candidates = []
    update = params.get("update")
    for holder in (params, update if isinstance(update, dict) else None):
        if holder is None:
            continue
        candidates.append(holder.get("usage"))
        meta = holder.get("_meta")
        if isinstance(meta, dict):
            candidates.append(meta.get("usage"))
    for cand in candidates:
        found = _usage_shaped(cand)
        if found:
            for k, v in found.items():
                acc[k] = acc.get(k, 0) + v


def finalize_usage(acc: dict) -> dict | None:
    """The result-payload ``usage`` block, or None when nothing was reported
    (or everything was zero — "no report" must read as unknown, not free)."""
    if not acc or not any(acc.values()):
        return None
    usage = {
        "input_tokens": int(acc.get("input_tokens", 0) or 0),
        "output_tokens": int(acc.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(acc.get("cache_read_tokens", 0) or 0),
        "cost_usd": round(float(acc.get("cost_usd", 0.0) or 0.0), 6),
    }
    return usage if any(usage.values()) else None


def _content_text(block: object) -> str:
    """Text of one ACP content block (``{"type": "text", "text": ...}``)."""
    if isinstance(block, dict) and block.get("type") == "text":
        text = block.get("text")
        if isinstance(text, str):
            return text
    return ""


def _pick_permission_option(options: object) -> str | None:
    """The optionId to auto-grant with: allow_always ≻ allow_once ≻ first."""
    if not isinstance(options, list) or not options:
        return None
    by_kind: dict[object, str] = {}
    for opt in options:
        if isinstance(opt, dict) and opt.get("optionId") is not None:
            by_kind.setdefault(opt.get("kind"), opt["optionId"])
    for kind in ("allow_always", "allow_once"):
        if kind in by_kind:
            return by_kind[kind]
    first = options[0]
    return first.get("optionId") if isinstance(first, dict) else None


class AcpClient:
    """Drives one agent subprocess through one task turn.

    ``on_event`` receives runner-wire event envelopes
    (``{"id", "type", "source", "ts", "payload"}``) mapped per research D4 —
    the same vocabulary the host console classifier already renders. The
    callback must not raise; we guard anyway.
    """

    def __init__(
        self,
        argv: list[str],
        env: dict[str, str],
        idle_timeout_s: int | None = None,
        on_event=None,
        on_update=None,
    ) -> None:
        self.argv = list(argv)
        self.env = dict(env)
        if idle_timeout_s is None:
            try:
                idle_timeout_s = int(
                    os.environ.get("DEVCLAW_ACP_IDLE_TIMEOUT_S", DEFAULT_IDLE_TIMEOUT_S)
                )
            except ValueError:
                idle_timeout_s = DEFAULT_IDLE_TIMEOUT_S
        self.idle_timeout_s = idle_timeout_s
        self._on_event = on_event or (lambda ev: None)
        #: Raw-params observer for every ``session/update`` (spec 021): the
        #: runner's slice watcher / context tripwire read the stream here.
        #: Guarded like ``on_event`` — an observer failure never breaks a turn.
        self._on_update = on_update or (lambda params: None)
        self.proc: subprocess.Popen | None = None
        self.session_id: str | None = None
        #: Last COMPLETE agent message flushed this turn — feeds `agent_output`
        #: and the BLOCKED/REPO NOTES parsing. Readable mid-turn (except path).
        self.last_agent_message: str = ""
        #: True while the CURRENT turn's cancel was requested by the runner
        #: itself (slice watcher / tripwire — spec 021). The runner uses this
        #: to tell a deliberate landing from an external cancel: a
        #: ``cancelled`` stopReason with this flag False is a failure, never
        #: an ok result. Reset by each new prompt() turn.
        self.turn_cancel_requested = False
        self._buf = bytearray()
        self._stderr = bytearray()
        self._next_id = 0
        self._msg_parts: list[str] = []
        self._thought_parts: list[str] = []
        self._usage_acc: dict = {}
        self._cancelling = False

    # --- lifecycle -----------------------------------------------------------

    def run(self, workspace_dir: str, prompt_text: str) -> PromptOutcome:
        """spawn → initialize → session/new → one prompt turn. Raises AcpError
        on any protocol failure; the caller owns close()."""
        self.start(workspace_dir)
        return self.prompt(prompt_text)

    def start(self, workspace_dir: str) -> None:
        """spawn → initialize → session/new, without prompting. Split out of
        run() (spec 021) so the runner can send a FOLLOW-UP prompt turn in the
        same session — the land-now sequence after a runner-initiated turn
        cancel. Raises AcpError on any protocol failure."""
        self._spawn(cwd=workspace_dir)
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                # No fs/terminal capabilities: the agent is self-sufficient in
                # the sandbox; an agent calling them anyway gets -32601, loud.
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
            },
        )
        new_sess = self._request(
            "session/new", {"cwd": workspace_dir, "mcpServers": []}
        )
        session_id = new_sess.get("sessionId") if isinstance(new_sess, dict) else None
        if not session_id:
            raise AcpError(f"session/new returned no sessionId: {new_sess!r}")
        self.session_id = session_id

    def prompt(self, prompt_text: str) -> PromptOutcome:
        """One ``session/prompt`` turn on the started session. There is no
        mid-turn message injection in ACP — a follow-up instruction is a NEW
        turn, sent after cancel_turn() ended the current one (spec 021)."""
        if not self.session_id:
            raise AcpError("prompt() before start(): no session")
        self.turn_cancel_requested = False
        result = self._request(
            "session/prompt",
            {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": prompt_text}],
            },
        )
        self._flush_thought()
        self._flush_message()
        stop = result.get("stopReason") if isinstance(result, dict) else None
        return PromptOutcome(
            stop_reason=str(stop or "end_turn"),
            last_agent_message=self.last_agent_message,
            usage=finalize_usage(self._usage_acc),
            stderr_tail=self.stderr_tail(),
        )

    def cancel_turn(self) -> None:
        """Runner-initiated cancel of the CURRENT prompt turn (spec 021):
        sends ``session/cancel`` and marks the cancel as deliberate, so the
        turn's ``cancelled`` stopReason reads as a landing step, not a
        failure. Safe to call from inside an on_event/on_update observer (the
        pump keeps running until the prompt response arrives). Never raises —
        if the notify fails the turn ends via the normal error path anyway."""
        if self.turn_cancel_requested:
            return
        self.turn_cancel_requested = True
        try:
            if self.session_id:
                self._notify("session/cancel", {"sessionId": self.session_id})
        except Exception:
            pass

    def close(self) -> None:
        """Teardown escalation: session/cancel → SIGTERM → SIGKILL. Never
        raises — close is best-effort by contract."""
        proc = self.proc
        if proc is None:
            return
        self._cancelling = True
        if proc.poll() is None:
            try:
                if self.session_id:
                    self._notify("session/cancel", {"sessionId": self.session_id})
            except Exception:
                pass
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    def stderr_tail(self) -> str:
        return self._stderr.decode("utf-8", errors="replace")

    # --- transport -----------------------------------------------------------

    def _spawn(self, cwd: str) -> None:
        try:
            self.proc = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=self.env,
                cwd=cwd,
            )
        except OSError as exc:
            raise AcpError(f"could not spawn ACP agent {self.argv!r}: {exc}") from exc

    def _send(self, obj: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise AcpError("ACP agent not spawned")
        try:
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AcpError(
                f"ACP agent pipe closed while sending {obj.get('method', 'response')}: "
                f"{exc}; stderr: {self.stderr_tail()[-2000:]}"
            ) from exc

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return self._pump_until_response(req_id, method)

    def _pump_until_response(self, want_id: int, method: str) -> dict:
        while True:
            msg = self._read_message()
            if "method" in msg:
                if "id" in msg:
                    self._handle_agent_request(msg)
                else:
                    self._handle_notification(msg)
                continue
            if msg.get("id") == want_id:
                if "error" in msg:
                    err = msg["error"]
                    detail = (
                        err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    )
                    data = err.get("data") if isinstance(err, dict) else None
                    if data:
                        detail = f"{detail} ({json.dumps(data, ensure_ascii=False)[:2000]})"
                    raise AcpError(f"{method} failed: {detail}")
                result = msg.get("result")
                return result if isinstance(result, dict) else {}
            # A response we're not waiting for — tolerated, logged as an event.
            self._emit("ACPUpdateEvent", "agent", {"stray_response": msg})

    def _read_message(self) -> dict:
        line = self._read_line()
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AcpError(
                f"malformed frame from ACP agent: {exc}: {line[:500]!r}"
            ) from exc
        if not isinstance(msg, dict):
            raise AcpError(f"malformed frame from ACP agent (not an object): {line[:500]!r}")
        return msg

    def _read_line(self) -> str:
        """Next newline-terminated frame from the agent's stdout. Drains
        stderr opportunistically. Raises AcpError on EOF or idle timeout."""
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        out_fd = proc.stdout.fileno()
        err_fd = proc.stderr.fileno() if proc.stderr else None
        deadline = time.monotonic() + self.idle_timeout_s
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = self._buf[:nl].decode("utf-8", errors="replace")
                del self._buf[: nl + 1]
                if line.strip():
                    return line
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill_hung()
                raise AcpError(
                    f"ACP agent idle timeout: no protocol traffic for "
                    f"{self.idle_timeout_s}s; killed. stderr: {self.stderr_tail()[-2000:]}"
                )
            fds = [out_fd] + ([err_fd] if err_fd is not None else [])
            try:
                ready, _, _ = select.select(fds, [], [], min(remaining, 30.0))
            except OSError as exc:
                raise AcpError(f"ACP agent pipe select failed: {exc}") from exc
            if not ready:
                continue
            if err_fd is not None and err_fd in ready:
                chunk = os.read(err_fd, _READ_CHUNK)
                if chunk:
                    self._stderr.extend(chunk)
                    del self._stderr[:-_STDERR_KEEP]
                    # stderr chatter proves the agent is alive — reset idle.
                    deadline = time.monotonic() + self.idle_timeout_s
            if out_fd in ready:
                chunk = os.read(out_fd, _READ_CHUNK)
                if not chunk:
                    code = proc.poll()
                    raise AcpError(
                        f"ACP agent exited (code={code}) before completing the turn; "
                        f"stderr: {self.stderr_tail()[-2000:]}"
                    )
                self._buf.extend(chunk)
                deadline = time.monotonic() + self.idle_timeout_s

    def _kill_hung(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    # --- inbound dispatch ----------------------------------------------------

    def _handle_agent_request(self, msg: dict) -> None:
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "session/request_permission":
            raw_params = msg.get("params")
            params = raw_params if isinstance(raw_params, dict) else {}
            options = params.get("options")
            chosen = _pick_permission_option(options)
            if self._cancelling or chosen is None:
                outcome: dict = {"outcome": "cancelled"}
            else:
                outcome = {"outcome": "selected", "optionId": chosen}
            # Auditability: the grant shows in the execution trace.
            self._emit(
                "PermissionRequestEvent",
                "agent",
                {"toolCall": params.get("toolCall"), "options": options, "chosen": chosen},
            )
            self._send({"jsonrpc": "2.0", "id": req_id, "result": {"outcome": outcome}})
            return
        # Unadvertised capability (fs/*, terminal/*, …) — loud, not silent.
        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"method not supported by devclaw runner: {method}",
                },
            }
        )

    def _handle_notification(self, msg: dict) -> None:
        method = msg.get("method")
        raw_params = msg.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        if method != "session/update":
            self._emit("ACPUpdateEvent", "agent", {"method": method, "params": params})
            return
        accumulate_usage(self._usage_acc, params)
        try:
            self._on_update(params)
        except Exception:
            # An observer failure must never crash the agent turn (same
            # contract as the on_event sink).
            pass
        raw_update = params.get("update")
        update = raw_update if isinstance(raw_update, dict) else {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            self._flush_thought()
            self._msg_parts.append(_content_text(update.get("content")))
            return
        if kind == "agent_thought_chunk":
            self._flush_message()
            self._thought_parts.append(_content_text(update.get("content")))
            return
        # Any non-chunk update is a message boundary.
        self._flush_thought()
        self._flush_message()
        if kind in ("tool_call", "tool_call_update"):
            payload = dict(update)
            # snake_case aliases the host console classifier reads (D4).
            payload.setdefault("tool_call_id", update.get("toolCallId"))
            payload.setdefault("raw_input", update.get("rawInput"))
            payload.setdefault("is_error", update.get("status") == "failed")
            self._emit("ACPToolCallEvent", "agent", payload)
        elif kind == "plan":
            self._emit("PlanEvent", "agent", dict(update))
        else:
            self._emit("ACPUpdateEvent", "agent", dict(update))

    # --- event emission ------------------------------------------------------

    def _flush_message(self) -> None:
        text = "".join(self._msg_parts)
        self._msg_parts.clear()
        if not text.strip():
            return
        self.last_agent_message = text
        self._emit(
            "MessageEvent",
            "agent",
            {"llm_message": {"content": [{"type": "text", "text": text}]}},
        )

    def _flush_thought(self) -> None:
        text = "".join(self._thought_parts)
        self._thought_parts.clear()
        if not text.strip():
            return
        self._emit(
            "MessageEvent",
            "agent",
            {
                "llm_message": {"content": [{"type": "text", "text": text}]},
                "thought": True,
            },
        )

    def _emit(self, etype: str, source: str, payload: dict) -> None:
        try:
            self._on_event(
                {
                    "id": None,
                    "type": etype,
                    "source": source,
                    "ts": int(time.time() * 1000),
                    "payload": payload,
                }
            )
        except Exception:
            # An event-sink failure must never crash the agent turn.
            pass
