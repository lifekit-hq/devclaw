"""The honest exit — the structured ``status="blocked"`` self-report.

The session ends its final hand-back with ``BLOCKED: <reason>`` when it
genuinely cannot finish. The engine parses the session's OWN final message (the
``result`` frame's text, never the prompt) and promotes it to a terminal
``status="blocked"`` with a typed kind. These pin the parser, the typed wire
shape, and the options form that never invents a choice.
"""

import pytest

from devclaw.engine import claude as engine


# ---- parser: the BLOCKED self-report ----------------------------------------


def test_bare_blocked_line_is_parsed():
    msg = (
        "I looked into it but the repo needs a paid API key I don't have.\n"
        "BLOCKED: the integration needs a Stripe secret key not present in the repo\n"
    )
    assert (
        engine.parse_blocked_reason(msg)
        == "the integration needs a Stripe secret key not present in the repo"
    )


def test_status_prefixed_blocked_is_parsed():
    msg = (
        "CHANGED: nothing shippable.\n"
        "STATUS: BLOCKED: the task contradicts itself — cannot both keep and remove X\n"
    )
    assert (
        engine.parse_blocked_reason(msg)
        == "the task contradicts itself — cannot both keep and remove X"
    )


def test_markdown_decorated_blocked_is_parsed():
    # models often bold/bullet the field — strip light decoration.
    msg = "**BLOCKED:** missing capability\n"
    assert engine.parse_blocked_reason(msg) == "missing capability"


def test_status_done_is_not_blocked():
    msg = "STATUS: DONE\nCHANGED: added the endpoint.\nFOLLOW-UPS: none\n"
    assert engine.parse_blocked_reason(msg) is None


def test_mid_sentence_blocked_prose_does_not_false_positive():
    # the load-bearing negative: only a line-start BLOCKED: (the contract field)
    # counts — prose that mentions being blocked must NOT fail a done task.
    msg = (
        "STATUS: DONE\n"
        "FOLLOW-UPS: I was briefly blocked: on a flaky test but retried and it passed.\n"
    )
    assert engine.parse_blocked_reason(msg) is None


def test_last_blocked_line_wins():
    msg = "BLOCKED: first pass hit a wall\nlater...\nBLOCKED: real final reason\n"
    assert engine.parse_blocked_reason(msg) == "real final reason"


def test_blocked_with_empty_reason_still_surfaces():
    # an honest "I'm blocked" with no stated reason must still be a block, never
    # silently lost as "no reason ⇒ not blocked".
    assert engine.parse_blocked_reason("BLOCKED:\n") is not None


def test_empty_and_none_are_not_blocked():
    assert engine.parse_blocked_reason("") is None
    assert engine.parse_blocked_reason(None) is None


@pytest.mark.parametrize("reason,kind,item", [
    ("cannot access the private registry", "contract", ""),
    ("env — dotnet-ef not available", "env", "dotnet-ef not available"),
    ("ENVIRONMENT: postgres service unreachable", "env", "postgres service unreachable"),
    ("env - NODE_AUTH_TOKEN rejected by the registry", "env", "NODE_AUTH_TOKEN rejected by the registry"),
    ("environment —", "contract", ""),            # the typed form with no item is not typed
    ("the environment variable X is unset", "contract", ""),  # prose mentioning the word is not the form
])
def test_blocked_payload_carries_the_typed_block(reason, kind, item):
    """Spec 032 US2: the block is TYPED on the wire. ``BLOCKED: env — <item>``
    rides as ``block_kind == "env"`` with the item, everything else as
    ``contract``; the host routes on the kind, never on prose."""
    result = engine.blocked_result(reason, workspace_dir="/ws", agent_output="words", usage=None)
    assert result["status"] == "blocked"
    assert result["reason"] == reason
    assert result["block_kind"] == kind and result["block_item"] == item


# ---- parser: the options form (spec 047 US1) --------------------------------
# A malformed block still blocks and never fabricates an option: the engine is
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
def test_block_options_parse_fail_closed_never_inventing_a_choice(reason, expected):
    assert engine.parse_block_line(reason) == expected


def test_env_block_carries_no_options():
    kind, item = engine.classify_block("env — NODE_AUTH_TOKEN for npm ci")
    assert (kind, item) == ("env", "NODE_AUTH_TOKEN for npm ci")
    # the payload site skips the options parser for env blocks; the parser
    # itself would also find nothing to rank here
    assert engine.parse_block_line("env — NODE_AUTH_TOKEN for npm ci")[1] == []
