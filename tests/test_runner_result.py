"""The runner's result contract: ``agent_output`` is the agent's last words.

Named regression (2026-08-19 fs-book-figures night run): the runner shipped the
SDK's captured decorative stdout — whose head is a verbatim echo of the ~16 KB
wrapped prompt — as ``agent_output``. Every head-bounded downstream consumer
(the deliveries "Agent summary", the direction evaluator) then saw 100% worker
preamble and zero agent-written characters, and the done-gate spun off_track on
starved evidence. The result now carries the agent's own final message; the
transcript tail is only the fallback for runs that died before the agent ever
spoke.
"""

import importlib.util
from pathlib import Path

import pytest

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "openhands-runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("oh_runner_result", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top-level only; openhands imports live in main()
    return mod


def test_agent_output_is_final_message_not_transcript_echo(runner):
    transcript = "Message from User " + "preamble doctrine " * 2000 + "\ntool panels\n"
    final = "STATUS: DONE\nCHANGED: rewired the three consumers.\nFOLLOW-UPS: none"
    out = runner._agent_last_words(final, transcript)
    assert out == final
    assert "preamble doctrine" not in out


def test_agent_output_falls_back_to_transcript_tail_when_agent_never_spoke(runner):
    # A run that dies mid-flight has no final message; the TAIL holds the last
    # actions before death — the head only banner + prompt echo.
    transcript = ("HEAD banner + prompt echo " * 2000) + "TAIL: last action before death"
    out = runner._agent_last_words("", transcript)
    assert out.endswith("TAIL: last action before death")
    assert len(out) <= 20_000


def test_blank_final_message_counts_as_never_spoke(runner):
    out = runner._agent_last_words("  \n ", "x" * 100)
    assert out == "x" * 100
