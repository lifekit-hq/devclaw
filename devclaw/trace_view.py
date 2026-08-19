"""Read cognition transcripts — the window onto what ``claude --print`` was
actually fed and what it said back.

Every real goal tick writes each cognition call's FULL prompt + response to
``<goals_dir>/<goal_id>/transcripts/<utc-ts>-<role>.md`` (see
:meth:`devclaw.loom.trace.PersistentTracer.write_transcript`). Those files are
the ground truth of execution — but nothing surfaced them, so debugging the loop
meant reasoning about prompts we could never see. This module is the surface: an
index of every cognition call in a run, and a full dump of any one.

It is a PURE READER — stdlib only, no devclaw imports, no state store, no
network. That is deliberate: the transcripts are self-describing markdown, so you
can ``scp -r`` a goal's ``transcripts/`` off the box and read it anywhere, and
the tool can never perturb a live run.

Usage::

    python -m devclaw.trace_view <transcripts-dir>             # index table
    python -m devclaw.trace_view <goal-dir>                    # auto-finds transcripts/
    python -m devclaw.trace_view <goals-dir> --goal <goal_id>  # picks the goal
    python -m devclaw.trace_view <dir> --show 3                # full prompt+response of call #3
    python -m devclaw.trace_view <dir> --errors                # only calls that carried an error
    python -m devclaw.trace_view <dir> --role goal_eval        # filter by cognition role
    python -m devclaw.trace_view <dir> --grep done_when        # calls whose text matches
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The section markers PersistentTracer.write_transcript emits. Kept as module
# constants so a format change there is a one-line change here (and the test,
# which drives the REAL writer, catches drift either way).
_PROMPT_MARKER = "\n## prompt\n"
_RESPONSE_MARKER = "\n## response\n"
_HEADER_LINE = re.compile(r"^- (\w+): (.*)$")


@dataclass
class Transcript:
    """One cognition call, parsed from its ``.md`` file."""

    filename: str
    ts: str = ""
    role: str = ""
    model: str = ""
    goal_id: str = ""
    tokens_in: str = ""
    tokens_out: str = ""
    cost_usd: str = ""
    error: str = ""
    prompt: str = ""
    response: str = ""
    #: extra header keys we don't model explicitly — kept so nothing is silently lost.
    extra: dict = field(default_factory=dict)

    @property
    def prompt_chars(self) -> int:
        return len(self.prompt)

    @property
    def response_chars(self) -> int:
        return len(self.response)


def parse_transcript(path: Path) -> Transcript:
    """Parse one transcript ``.md`` into a :class:`Transcript`. Tolerant by
    design: a malformed file still yields a row (with whatever header/body it
    could recover) rather than crashing a whole run's index — a truncated
    transcript from a killed process is exactly the case you most want to see."""
    text = path.read_text(errors="replace")
    t = Transcript(filename=path.name)

    head, sep, rest = text.partition(_PROMPT_MARKER)
    if not sep:
        # No prompt section (unexpected / truncated) — recover the header only.
        head, rest = text, ""
    for line in head.splitlines():
        m = _HEADER_LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if key in ("ts", "role", "model", "goal_id", "tokens_in", "tokens_out",
                   "cost_usd", "error"):
            setattr(t, key, val)
        else:
            t.extra[key] = val

    # Split prompt / response on the FIRST response marker after the prompt
    # (the response section is last; a prompt that itself contains the literal
    # "## response" line is rare, and mis-attributing a few bytes only skews the
    # size estimate, never the content dump — the file is printed whole).
    prompt, sep, response = rest.partition(_RESPONSE_MARKER)
    t.prompt = prompt.strip("\n")
    t.response = response.strip("\n") if sep else ""
    return t


def resolve_transcripts_dir(path: Path, goal: str | None = None) -> Path:
    """Resolve a user-supplied path to the directory that actually holds the
    ``.md`` files. Accepts, in order of specificity:

    - a ``transcripts/`` dir directly,
    - a goal dir (contains ``transcripts/``),
    - a goals dir + ``--goal <id>`` (``<goals>/<id>/transcripts/``).
    """
    path = Path(path)
    if goal:
        cand = path / goal / "transcripts"
        if cand.is_dir():
            return cand
        cand = path / goal
        if cand.is_dir():
            return cand
        raise FileNotFoundError(f"no transcripts for goal {goal!r} under {path}")
    if (path / "transcripts").is_dir():
        return path / "transcripts"
    return path


def load_dir(path: Path, goal: str | None = None) -> list[Transcript]:
    """Every transcript under the resolved dir, oldest first. Sort is by
    filename, whose ``<utc-ts>`` prefix sorts chronologically by construction."""
    tdir = resolve_transcripts_dir(path, goal)
    files = sorted(tdir.glob("*.md"), key=lambda p: p.name)
    return [parse_transcript(p) for p in files]


def _human(n: int) -> str:
    """Char counts as compact human strings — 105.4k reads at a glance where
    107993 does not (and prompt size is the whole point of this tool)."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def _short_ts(ts: str) -> str:
    """The time-of-day from an ISO ts, for a scannable index column."""
    m = re.search(r"T(\d{2}:\d{2}:\d{2})", ts)
    return m.group(1) if m else (ts[:19] or "?")


def format_index(transcripts: list[Transcript]) -> str:
    """A one-line-per-call table: sequence #, time, role, model, prompt/response
    size, cost, and an error flag. The sequence # is what you pass to --show."""
    if not transcripts:
        return "(no transcripts found)"
    rows = [("#", "time", "role", "model", "in", "out", "cost", "")]
    for i, t in enumerate(transcripts, 1):
        rows.append((
            str(i),
            _short_ts(t.ts),
            t.role or "?",
            (t.model or "").replace("claude-", "") or "-",
            _human(t.prompt_chars),
            _human(t.response_chars),
            (t.cost_usd if t.cost_usd not in ("", "n/a") else "-"),
            "ERR" if t.error else "",
        ))
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    out = []
    for ri, r in enumerate(rows):
        out.append("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(r)).rstrip())
        if ri == 0:
            out.append("  ".join("-" * widths[c] for c in range(len(widths))))
    total_in = sum(t.prompt_chars for t in transcripts)
    out.append("")
    out.append(
        f"{len(transcripts)} cognition calls · "
        f"{sum(1 for t in transcripts if t.error)} with errors · "
        f"largest prompt {_human(max((t.prompt_chars for t in transcripts), default=0))} · "
        f"total prompt bytes {_human(total_in)}"
    )
    return "\n".join(out)


def format_show(t: Transcript, seq: int | None = None) -> str:
    """The full call — every byte of prompt and response. This is the payoff:
    read exactly what cognition was handed and exactly what it returned."""
    head = f"═══ call {seq}: {t.role} ({t.model or 'default'}) ═══" if seq else f"═══ {t.role} ═══"
    lines = [
        head,
        f"ts={t.ts}  goal={t.goal_id}  tokens_in={t.tokens_in}  "
        f"tokens_out={t.tokens_out}  cost={t.cost_usd}",
    ]
    if t.error:
        lines.append(f"ERROR: {t.error}")
    lines += [
        f"prompt: {t.prompt_chars} chars  response: {t.response_chars} chars",
        "",
        "──── PROMPT ────",
        t.prompt,
        "",
        "──── RESPONSE ────",
        t.response or "(empty — the call produced no response)",
    ]
    return "\n".join(lines)


def _apply_filters(
    transcripts: list[Transcript], *, role: str | None, errors_only: bool, grep: str | None
) -> list[Transcript]:
    out = transcripts
    if role:
        out = [t for t in out if t.role == role]
    if errors_only:
        out = [t for t in out if t.error]
    if grep:
        needle = grep.lower()
        out = [t for t in out if needle in t.prompt.lower() or needle in t.response.lower()]
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m devclaw.trace_view",
        description="Read cognition transcripts (prompt + response of every claude --print call).",
    )
    p.add_argument("path", help="a transcripts/ dir, a goal dir, or a goals dir (with --goal)")
    p.add_argument("--goal", default=None, help="goal id, when path is a goals dir")
    p.add_argument("--show", type=int, default=None, metavar="N",
                   help="dump the full prompt+response of call #N (from the index)")
    p.add_argument("--role", default=None, help="only calls with this cognition role")
    p.add_argument("--errors", action="store_true", help="only calls that carried an error")
    p.add_argument("--grep", default=None, help="only calls whose prompt or response contains this")
    p.add_argument("--full", action="store_true", help="dump every (filtered) call in full, not the index")
    return p


def _handle_broken_pipe() -> None:
    """A downstream pager (``head`` / ``less``) closed the pipe before we
    finished writing — routine, not an error. Redirect stdout to devnull so the
    interpreter's exit-time flush doesn't re-raise BrokenPipeError, then let the
    caller exit clean. You WILL pipe an 80 KB dump to ``head`` all day; it must
    not spew a traceback when you do."""
    import os
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except Exception:  # noqa: BLE001 — fake stdout in tests has no fileno; benign
        pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        transcripts = load_dir(Path(args.path), args.goal)
    except FileNotFoundError as exc:
        sys.stderr.write(f"trace_view: {exc}\n")
        return 2

    shown = _apply_filters(
        transcripts, role=args.role, errors_only=args.errors, grep=args.grep
    )

    if args.show is not None:
        if not 1 <= args.show <= len(shown):
            sys.stderr.write(
                f"trace_view: --show {args.show} out of range (1..{len(shown)})\n"
            )
            return 2
        output = format_show(shown[args.show - 1], seq=args.show)
    elif args.full:
        output = "\n\n".join(
            format_show(t, seq=i) for i, t in enumerate(shown, 1)
        ) or "(no transcripts found)"
    else:
        output = format_index(shown)

    try:
        print(output)
        sys.stdout.flush()  # surface the pipe break HERE, not at exit-time flush
    except BrokenPipeError:
        _handle_broken_pipe()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
