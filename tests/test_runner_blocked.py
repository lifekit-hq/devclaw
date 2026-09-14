"""In-sandbox honest-exit — the structured ``status="blocked"`` self-report.

The task prompt already instructs the engineer to end its final hand-back with
``STATUS: DONE`` or ``BLOCKED: <reason>`` when it genuinely cannot finish, but
that string used to ride invisibly inside ``agent_output`` and never touched the
done/failed decision — so a truly-stuck agent either fabricated a plausible
result or failed generically and got retried pointlessly. The runner now parses
its OWN final message for that self-report and promotes it to a first-class
terminal ``status="blocked"`` (+ ``reason``). These pin the parser and prove the
prompt-echo can't false-positive (the parse reads the agent message, not the
captured decorative stdout that echoes the literal contract text).
"""

import importlib.util
import io
import json
from pathlib import Path

import pytest

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_blocked", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top-level import; the runner is stdlib-only (spec 011)
    return mod


# ---- parser: the BLOCKED self-report ----------------------------------------


def test_bare_blocked_line_is_parsed(runner):
    msg = (
        "I looked into it but the repo needs a paid API key I don't have.\n"
        "BLOCKED: the integration needs a Stripe secret key not present in the repo\n"
    )
    assert (
        runner._parse_blocked_reason(msg)
        == "the integration needs a Stripe secret key not present in the repo"
    )


def test_status_prefixed_blocked_is_parsed(runner):
    msg = (
        "CHANGED: nothing shippable.\n"
        "STATUS: BLOCKED: the task contradicts itself — cannot both keep and remove X\n"
    )
    assert (
        runner._parse_blocked_reason(msg)
        == "the task contradicts itself — cannot both keep and remove X"
    )


def test_markdown_decorated_blocked_is_parsed(runner):
    # models often bold/bullet the field — strip light decoration.
    msg = "**BLOCKED:** missing capability\n"
    assert runner._parse_blocked_reason(msg) == "missing capability"


def test_status_done_is_not_blocked(runner):
    msg = "STATUS: DONE\nCHANGED: added the endpoint.\nFOLLOW-UPS: none\n"
    assert runner._parse_blocked_reason(msg) is None


def test_mid_sentence_blocked_prose_does_not_false_positive(runner):
    # the load-bearing negative: only a line-start BLOCKED: (the contract field)
    # counts — prose that mentions being blocked must NOT fail a done task.
    msg = (
        "STATUS: DONE\n"
        "FOLLOW-UPS: I was briefly blocked: on a flaky test but retried and it passed.\n"
    )
    assert runner._parse_blocked_reason(msg) is None


def test_last_blocked_line_wins(runner):
    msg = "BLOCKED: first pass hit a wall\nlater...\nBLOCKED: real final reason\n"
    assert runner._parse_blocked_reason(msg) == "real final reason"


def test_blocked_with_empty_reason_still_surfaces(runner):
    # an honest "I'm blocked" with no stated reason must still be a block, never
    # silently lost as "no reason ⇒ not blocked".
    assert runner._parse_blocked_reason("BLOCKED:\n") is not None


def test_empty_and_none_are_not_blocked(runner):
    assert runner._parse_blocked_reason("") is None
    assert runner._parse_blocked_reason(None) is None


# ---- agent-message extraction from a MessageEvent payload -------------------


# ---- emission: the terminal `result:` line ----------------------------------


@pytest.mark.parametrize("reason,kind,item", [
    ("cannot access the private registry", "contract", ""),
    ("env — dotnet-ef not available", "env", "dotnet-ef not available"),
    ("ENVIRONMENT: postgres service unreachable", "env", "postgres service unreachable"),
    ("env - NODE_AUTH_TOKEN rejected by the registry", "env", "NODE_AUTH_TOKEN rejected by the registry"),
    ("environment —", "contract", ""),            # the typed form with no item is not typed
    ("the environment variable X is unset", "contract", ""),  # prose mentioning the word is not the form
])
def test_blocked_payload_emits_structured_result(runner, monkeypatch, reason, kind, item):
    """Spec 032 US2: the block is TYPED on the wire. ``BLOCKED: env — <item>``
    rides as ``block_kind == "env"`` with the item, everything else as
    ``contract``; the host routes on the kind, never on prose."""
    out = io.StringIO()
    monkeypatch.setattr(runner, "_PROTO_OUT", out)
    block_kind, block_item = runner._classify_block(reason)
    assert (block_kind, block_item) == (kind, item)
    runner._emit_result(
        {"status": "blocked", "reason": reason, "block_kind": block_kind,
         "block_item": block_item, "workspace_dir": "/ws", "agent_output": "banner"}
    )
    line = out.getvalue()
    assert line.startswith("result: ") and line.endswith("\n")
    result = json.loads(line[len("result: "):])
    assert result["status"] == "blocked"
    assert result["reason"] == reason
    assert result["block_kind"] == kind and result["block_item"] == item


# ---- spec 042 US2: a broken hop never costs a session -----------------------
# A credential the host declared, missing in the container, is devclaw's own
# mount. Discovering that by running a full Claude session — then describing it
# in prose that becomes a project-wide brake only a human can clear — is what
# five owner resumes in four days bought. The runner can answer it before the
# agent starts, for nothing.

def test_a_declared_credential_that_is_absent_refuses_the_session(runner):
    req = {"agent_env": ["NODE_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]}
    present, absent = runner.partition_agent_credentials(
        req, {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "NODE_AUTH_TOKEN": "   "},
    )
    assert present == ("CLAUDE_CODE_OAUTH_TOKEN",) and absent == ("NODE_AUTH_TOKEN",)
    assert runner.declared_hop_broken(req, absent) is True


def test_every_declared_credential_present_starts_the_session(runner):
    req = {"agent_env": ["NODE_AUTH_TOKEN"]}
    present, absent = runner.partition_agent_credentials(req, {"NODE_AUTH_TOKEN": "ghp_x"})
    assert present == ("NODE_AUTH_TOKEN",) and absent == ()
    assert runner.declared_hop_broken(req, absent) is False


def test_a_pre_042_host_declares_nothing_and_is_never_refused(runner):
    """No list in the payload ⇒ nothing was declared ⇒ nothing can be missing.
    The #644 fallback contract keeps working byte-identically."""
    req = {}
    _, absent = runner.partition_agent_credentials(req, {})
    assert absent == runner._PRE_042_AGENT_ENV
    assert runner.declared_hop_broken(req, absent) is False


# ---- parser: the options form (spec 047 US1) --------------------------------
# A malformed block still blocks and never fabricates an option: the runner is
# the ONE parser of the line, and the console's buttons are only ever the
# session's own words.


@pytest.mark.parametrize("reason,expected", [
    # two to four options, default by letter → resolved, default text = the option
    ("use SQLite or Postgres? — options: (a) SQLite | (b) Postgres — default: (a)",
     ("use SQLite or Postgres?", ["SQLite", "Postgres"], "SQLite", 0)),
    ("which? — options: a) one | b) two | c) three | d) four — default: c",
     ("which?", ["one", "two", "three", "four"], "three", 2)),
    # default by exact text, and by prefix, case-insensitive
    ("which? — options: keep the table | drop it — default: Drop it",
     ("which?", ["keep the table", "drop it"], "Drop it", 1)),
    ("which? — options: keep the table | drop it — default: keep",
     ("which?", ["keep the table", "drop it"], "keep", 0)),
    # a default naming none of the options → unranked, the default still stands
    ("which? — options: x | y — default: z", ("which?", ["x", "y"], "z", -1)),
    # today's form: no options → question/default split, nothing invented
    ("keep the flag? — default: yes, keep it", ("keep the flag?", [], "yes, keep it", -1)),
    # no separators at all → the reason is the question
    ("the ticket contradicts the README", ("the ticket contradicts the README", [], "", -1)),
    # one option or five → not a list; the head stays the question
    ("which? — options: only one — default: only one", ("which? — options: only one", [], "only one", -1)),
    ("which? — options: 1 | 2 | 3 | 4 | 5 — default: 1", ("which? — options: 1 | 2 | 3 | 4 | 5", [], "1", -1)),
    # garbage after options: → no options
    ("which? — options: | | — default: a", ("which? — options: | |", [], "a", -1)),
    # ascii dashes tolerated
    ("which? - options: p | q - default: q", ("which?", ["p", "q"], "q", 1)),
])
def test_block_options_parse_fail_closed_never_inventing_a_choice(runner, reason, expected):
    assert runner._parse_block_line(reason) == expected


def test_env_block_carries_no_options(runner):
    kind, item = runner._classify_block("env — NODE_AUTH_TOKEN for npm ci")
    assert (kind, item) == ("env", "NODE_AUTH_TOKEN for npm ci")
    # the payload site skips the options parser for env blocks; the parser
    # itself would also find nothing to rank here
    assert runner._parse_block_line("env — NODE_AUTH_TOKEN for npm ci")[1] == []
