"""Deterministic fake ACP agent — the executable proof of the agent-command
seam (spec 011, FR-008 / US2).

Runnable as a program (``python tests/acp_fake_agent.py --script <name>``) so
the runner can drive it via ``DEVCLAW_ACP_COMMAND`` with zero code change,
and importable for the protocol-level unit tests. Speaks newline-delimited
JSON-RPC 2.0 per ``specs/011-acp-runner-swap/contracts/runner-agent-acp.md``.

Scripts (what happens on ``session/prompt``):
  ok           thought + messages + a tool call + final message → end_turn
  usage        like ok, minimal, with a usage report on an update → end_turn
  permission   session/request_permission round-trip; final message reports
               PERMISSION-OK / WRONG-OPTION / PERMISSION-CANCELLED
  client_call  calls fs/read_text_file; final message reports FS-DENIED-OK
               on the expected -32601, UNEXPECTED-FS-ANSWER otherwise
  blocked      final message is a BLOCKED: self-report → end_turn
  refusal      short message → stopReason "refusal"
  rate_limit   session/prompt answered with a quota-worded JSON-RPC error
  hang         swallows session/prompt and sleeps forever (idle-timeout prey)
  malformed    emits a non-JSON line mid-turn
  cancelled    ends the turn stopReason "cancelled" with no runner cancel —
               the runner must fail closed (spec 021)
  slice_flip   completes slice US1 then touches a US2 row → the watcher's
               stop condition; answers the land-now follow-up prompt (spec 021)
  wrapup_only  completes ONE slice then only wraps up — must NOT be stopped

Every script first checks the environment: if an Anthropic API key leaked
into the agent env, the final message is ``LEAKED-API-KEY`` — the named
key-stripping regression asserts its absence (Principle I).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return _read()
    return json.loads(line)


class FakeAgent:
    def __init__(self, script: str) -> None:
        self.script = script
        self.session_id = "sess-fake-1"
        self._next_id = 1000
        #: prompt turns served so far — the spec-021 landing-sequence scripts
        #: behave differently on the follow-up (land-now) prompt.
        self.prompt_count = 0

    # --- protocol helpers ----------------------------------------------------

    def update(self, update: dict) -> None:
        _send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": self.session_id, "update": update},
            }
        )

    def message(self, text: str) -> None:
        self.update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            }
        )

    def request(self, method: str, params: dict) -> dict:
        """Send an agent→client request and wait for its response."""
        self._next_id += 1
        req_id = self._next_id
        _send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        while True:
            msg = _read()
            if msg is None:
                sys.exit(1)
            if msg.get("id") == req_id and "method" not in msg:
                return msg

    # --- the one prompt turn -------------------------------------------------

    def run_prompt(self, prompt_id: int) -> None:
        self.prompt_count += 1
        leaked = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
            "ANTHROPIC_AUTH_TOKEN"
        )
        if leaked:
            self.message("LEAKED-API-KEY")
            _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})
            return
        handler = getattr(self, f"script_{self.script}")
        handler(prompt_id)

    def script_echo_oauth(self, prompt_id: int) -> None:
        """Report whether the sanctioned setup-token reached the AGENT env —
        the seam #644's original test missed (it asserted the container env,
        one hop above where the allowlist dropped the token)."""
        present = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        self.message("OAUTH-TOKEN-PRESENT" if present else "OAUTH-TOKEN-ABSENT")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_echo_bash_env(self, prompt_id: int) -> None:
        """Report the BASH_ENV the AGENT process sees — the OOM-shield seam
        (spec 020 US2): the runner sets it only when the sandbox image ships
        /opt/devclaw/oom-shield.sh, so agent bash children self-raise their
        oom_score_adj."""
        self.message(os.environ.get("BASH_ENV") or "BASH-ENV-ABSENT")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_ok(self, prompt_id: int) -> None:
        self.update(
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "planning the change"},
            }
        )
        self.message("Working on it. ")
        self.message("Reading the repo first.")
        self.update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc-1",
                "title": "Read README.md",
                "kind": "read",
                "status": "pending",
            }
        )
        self.update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc-1",
                "status": "completed",
                "content": [{"content": {"type": "text", "text": "readme text"}}],
            }
        )
        self.message("All done.\n\nREPO NOTES: fake repo, tests are fast.")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_usage(self, prompt_id: int) -> None:
        self.message("done with usage")
        self.update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": ""},
                "usage": {"inputTokens": 120, "outputTokens": 34},
            }
        )
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_permission(self, prompt_id: int) -> None:
        resp = self.request(
            "session/request_permission",
            {
                "sessionId": self.session_id,
                "toolCall": {"toolCallId": "tc-perm", "title": "Run rm -rf build/"},
                "options": [
                    {"optionId": "opt-reject", "name": "Reject", "kind": "reject_once"},
                    {"optionId": "opt-once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "opt-always", "name": "Allow always", "kind": "allow_always"},
                ],
            },
        )
        outcome = (resp.get("result") or {}).get("outcome") or {}
        if outcome.get("outcome") == "cancelled":
            self.message("PERMISSION-CANCELLED")
        elif outcome.get("outcome") == "selected" and outcome.get("optionId") == "opt-always":
            self.message("PERMISSION-OK")
        else:
            self.message(f"WRONG-OPTION: {json.dumps(outcome)}")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_client_call(self, prompt_id: int) -> None:
        resp = self.request(
            "fs/read_text_file",
            {"sessionId": self.session_id, "path": "/etc/hostname"},
        )
        err = resp.get("error") or {}
        if err.get("code") == -32601:
            self.message("FS-DENIED-OK")
        else:
            self.message(f"UNEXPECTED-FS-ANSWER: {json.dumps(resp)}")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_blocked(self, prompt_id: int) -> None:
        self.message("BLOCKED: the task needs a credential only the owner has")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_refusal(self, prompt_id: int) -> None:
        self.message("I can't help with that.")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "refusal"}})

    def script_rate_limit(self, prompt_id: int) -> None:
        _send(
            {
                "jsonrpc": "2.0",
                "id": prompt_id,
                "error": {
                    "code": -32000,
                    "message": "Claude AI usage limit reached — try again in 30 minutes",
                },
            }
        )

    def script_hang(self, prompt_id: int) -> None:
        while True:
            time.sleep(3600)

    def script_cancelled(self, prompt_id: int) -> None:
        """An externally-cancelled turn (spec 021 T003): the agent ends the
        turn with stopReason 'cancelled' WITHOUT any runner-initiated cancel —
        the runner must fail closed, never settle ok."""
        self.message("half-finished work left behind")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "cancelled"}})

    # --- spec 021 slice-watcher scripts --------------------------------------

    def _tool_step(self, call_id: str) -> None:
        self.update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": "completed",
            }
        )

    def _write(self, rel_path: str, text: str) -> None:
        os.makedirs(os.path.dirname(rel_path) or ".", exist_ok=True)
        with open(rel_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _await_cancel_then_end(self, prompt_id: int) -> None:
        """Consume inbound frames until the runner's session/cancel arrives,
        then end the current turn with stopReason 'cancelled' (the agent-side
        half of the landing sequence)."""
        while True:
            msg = _read()
            if msg is None:
                sys.exit(1)
            if msg.get("method") == "session/cancel":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": prompt_id,
                        "result": {"stopReason": "cancelled"},
                    }
                )
                return

    def script_slice_flip(self, prompt_id: int) -> None:
        """Completes slice US1 then touches a US2 row — the exact stop
        condition. The runner cancels the turn; the follow-up (land-now)
        prompt gets an honest landing hand-back."""
        if self.prompt_count > 1:
            self.message(
                "LANDED: slice committed honestly.\n\nSTATUS: DONE\n"
                "REPO NOTES: none"
            )
            _send(
                {"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}}
            )
            return
        tasks = "specs/001-f/tasks.md"
        self._write(
            tasks,
            "- [x] T001 [US1] first slice work\n"
            "- [x] T002 [US1] first slice tests\n"
            "- [ ] T003 [US2] second slice work\n",
        )
        self._tool_step("tc-flip-1")
        # Sync barrier: a request/response round-trip guarantees the client's
        # single-threaded pump has processed tc-flip-1 (and its watcher
        # observation of the intermediate file state) BEFORE the next write —
        # updates alone are notifications and would race the second write.
        self.request(
            "session/request_permission",
            {
                "sessionId": self.session_id,
                "toolCall": {"toolCallId": "tc-flip-sync", "title": "sync"},
                "options": [
                    {"optionId": "opt-ok", "name": "ok", "kind": "allow_once"}
                ],
            },
        )
        self._write(
            tasks,
            "- [x] T001 [US1] first slice work\n"
            "- [x] T002 [US1] first slice tests\n"
            "- [x] T003 [US2] second slice work\n",
        )
        self._tool_step("tc-flip-2")
        self._await_cancel_then_end(prompt_id)

    def script_wrapup_only(self, prompt_id: int) -> None:
        """Completes slice US1 and then only wraps up (no rows outside the
        advanced slice touched) — the watcher must NOT stop this session."""
        tasks = "specs/001-f/tasks.md"
        self._write(
            tasks,
            "- [x] T001 [US1] first slice work\n"
            "- [x] T002 [US1] first slice tests\n"
            "- [ ] T003 [US2] second slice work\n",
        )
        self._tool_step("tc-wrap-1")
        self._tool_step("tc-wrap-2")
        self.message("done after one slice.\n\nSTATUS: DONE")
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    def script_malformed(self, prompt_id: int) -> None:
        sys.stdout.write("this is not json-rpc\n")
        sys.stdout.flush()
        _send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})

    # --- main loop -----------------------------------------------------------

    def serve(self) -> None:
        while True:
            msg = _read()
            if msg is None:
                return
            method = msg.get("method")
            if method == "initialize":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "result": {"protocolVersion": 1, "agentCapabilities": {}},
                    }
                )
            elif method == "session/new":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "result": {"sessionId": self.session_id},
                    }
                )
            elif method == "session/prompt":
                self.run_prompt(msg["id"])
            elif method == "session/cancel":
                return
            elif "id" in msg and method is not None:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": f"fake agent: {method}?"},
                    }
                )
            # notifications we don't know: ignore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", default="ok")
    args = parser.parse_args()
    FakeAgent(args.script).serve()


if __name__ == "__main__":
    main()
