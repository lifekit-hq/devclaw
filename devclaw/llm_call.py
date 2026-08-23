"""The low-level LLM-call primitive — a LEAF module by design.

One `claude --print` subprocess call (:func:`call_claude`), its argv/envelope
plumbing, the :class:`PlannerError` it raises, the :func:`extract_json` helper
callers parse responses with, and the :func:`claude_with_model` factory that
binds a model + role into a one-argument caller.

Extracted from ``planner.py`` (2026-07-19) so the quality gate can depend on
the call primitive without dragging the whole planner (and its
state_store/task_git imports) behind it — this module's only internal import
is ``loom.trace`` (itself pure stdlib), which keeps
``quality → llm_call → loom`` a straight leafward chain instead of the old
``quality → planner → loom → goal`` cycle. ``planner`` re-exports every public
name here, so existing ``from .planner import …`` call sites (and tests that
patch ``planner.call_claude``) are untouched.

Auth comes from the OAuth session — no API key, ever (stripped per call).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable

from .loom import trace as _trace

#: A bound, one-argument LLM caller: ``await caller(prompt) -> response``. The
#: cognition callers are all this shape (``claude_with_model`` builds one). Lives
#: here in the leaf so goal-layer modules type against it without importing a
#: heavier module. (Relocated from the deleted goal/planner.py — demolition P3b.)
ClaudeCaller = Callable[[str], Awaitable[str]]
from .loom.limits import FailureKind, classify_failure

#: fallback (seconds) for the default cognition ceiling when
#: ``DEVCLAW_COGNITION_TIMEOUT_S`` is unset or unusable.
_COGNITION_TIMEOUT_DEFAULT_S = 180


def _cognition_timeout_ms_from_env(raw: str | None) -> int:
    """Parse ``DEVCLAW_COGNITION_TIMEOUT_S`` (seconds) → milliseconds.
    Fail-safe on purpose: an invalid or ``<= 0`` value falls back to the
    default rather than crashing import — a typo in ``.env`` must never take
    cognition down."""
    try:
        seconds = int(str(raw).strip())
    except (TypeError, ValueError):
        seconds = _COGNITION_TIMEOUT_DEFAULT_S
    if seconds <= 0:
        seconds = _COGNITION_TIMEOUT_DEFAULT_S
    return seconds * 1000


#: default ceiling for any cognition call when the caller doesn't supply its
#: own. Env-configurable via ``DEVCLAW_COGNITION_TIMEOUT_S`` (seconds; default
#: 180). The old hardcoded 90s left no headroom: in production (2026-07-14/15
#: night) successful calls on MODERATE prompts ran 50–78s at peak hours with
#: p90 hugging the cap, and five calls timed out at exactly 90s — each timeout
#: burns a full model call plus a 15-minute tick. Each role's
#: ``default_caller`` may still pass a larger value via
#: :func:`claude_with_model` when its expected output volume warrants — the
#: decomposer is the canonical example (opus generating multi-KB YAML
#: routinely needs more than the default).
PLANNER_TIMEOUT_MS = _cognition_timeout_ms_from_env(
    os.environ.get("DEVCLAW_COGNITION_TIMEOUT_S")
)
CLAUDE_BIN = os.environ.get("DEVCLAW_CLAUDE_BIN", "claude")

#: Bounded retry for a TRANSIENT cognition failure — the clean-run fix
#: (2026-07-30). The #1 wedge of an unattended run was the host-side
#: ``claude --print`` call itself failing transiently: the CLI OOM-killed
#: mid-call (``exited -9`` under host memory pressure — many 2g sandboxes + host
#: cognition on one VPS), a timeout, or an ``overloaded``/network blip. Each used
#: to raise straight through and dead-stop the goal + ping the owner. Now the one
#: primitive retries the TRANSIENT class a bounded number of times with a short
#: backoff, so a momentary hiccup becomes a recovered blip instead of a dead
#: night. Bounded + backed-off so a PERSISTENT failure still fails (never an
#: infinite burn); ONLY TRANSIENT retries (QUOTA/RATE/AUTH still pause, REAL still
#: fails fast — see :func:`devclaw.loom.limits.classify_failure`). Env-configurable,
#: fail-safe parse like the timeout above; ``0`` disables retries.
_COGNITION_RETRIES_DEFAULT = 2


def _cognition_retries_from_env(raw: str | None) -> int:
    """Parse ``DEVCLAW_COGNITION_RETRIES`` → a non-negative retry count. Invalid
    or negative → the default; ``0`` is honored (retries off). Never crashes
    import — a typo in ``.env`` must not take cognition down."""
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return _COGNITION_RETRIES_DEFAULT
    return n if n >= 0 else _COGNITION_RETRIES_DEFAULT


COGNITION_MAX_RETRIES = _cognition_retries_from_env(
    os.environ.get("DEVCLAW_COGNITION_RETRIES")
)
#: backoff (seconds) before a transient retry: geometric base·4**attempt (5s,
#: 20s, …) capped, unless the provider STATED a retry-after hint (then that
#: wins, up to the cap). Kept short because a signal-death returns instantly —
#: the common case recovers in seconds, not the full timeout.
_COGNITION_RETRY_BASE_S = 5
_COGNITION_RETRY_CAP_S = 60
#: indirection so tests neutralize the real backoff sleep (no multi-second waits).
_RETRY_SLEEP = asyncio.sleep


def _retry_backoff_s(attempt: int, retry_after_s: int | None) -> float:
    """Seconds to wait before retry ``attempt`` (0-indexed). A provider-stated
    ``retry_after_s`` hint wins; otherwise geometric ``base·4**attempt``, capped."""
    if retry_after_s and retry_after_s > 0:
        return float(min(retry_after_s, _COGNITION_RETRY_CAP_S))
    return float(min(_COGNITION_RETRY_BASE_S * (4 ** attempt), _COGNITION_RETRY_CAP_S))


#: Cap on CONCURRENT host-side ``claude --print`` subprocesses (2026-08-13).
#: Every host cognition call — review gate, evaluator, planner, done-gate —
#: funnels through this one spawn chokepoint, but the population is invisible
#: to the task cap (``DEVCLAW_MAX_CONCURRENT`` counts sandboxed tasks, not host
#: processes): last night up to 4 review gates + goal cognition ran
#: concurrently and the kernel OOM-killed them (``exited -9``, ×117 lifetime).
#: A module-level semaphore, sized by ``DEVCLAW_MAX_HOST_COGNITION`` (default
#: 2), bounds the host ``claude`` population; queued callers simply wait —
#: no errors, no behavior change besides serialization. The zero-token idle
#: guard is unaffected (this wraps only REAL calls), and the retry backoff in
#: :func:`call_claude` sleeps OUTSIDE the semaphore (each attempt re-acquires).
_MAX_HOST_COGNITION_DEFAULT = 2


def _max_host_cognition_from_env(raw: str | None) -> int:
    """Parse ``DEVCLAW_MAX_HOST_COGNITION`` → a positive concurrency cap.
    Fail-safe like the timeout/retry parsers above: invalid or ``< 1`` falls
    back to the default — a typo in ``.env`` must never take cognition down
    (and ``0`` would deadlock every call, so it is NOT honored)."""
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return _MAX_HOST_COGNITION_DEFAULT
    return n if n >= 1 else _MAX_HOST_COGNITION_DEFAULT


#: Lazy holder for the semaphore, keyed to the event loop it was created under.
#: Lazy so importing this module never touches an event loop, and so the env
#: var is read at first use (tests monkeypatch it). Loop-keyed because an
#: ``asyncio.Semaphore`` binds to the running loop on first acquire and raises
#: if reused from another loop — production runs one loop forever (one stable
#: semaphore), while each ``asyncio.run`` in the test suite gets a fresh one.
_host_cognition_sem: asyncio.Semaphore | None = None
_host_cognition_sem_loop: asyncio.AbstractEventLoop | None = None


def _host_cognition_semaphore() -> asyncio.Semaphore:
    global _host_cognition_sem, _host_cognition_sem_loop
    loop = asyncio.get_running_loop()
    if _host_cognition_sem is None or _host_cognition_sem_loop is not loop:
        _host_cognition_sem = asyncio.Semaphore(
            _max_host_cognition_from_env(os.environ.get("DEVCLAW_MAX_HOST_COGNITION"))
        )
        _host_cognition_sem_loop = loop
    return _host_cognition_sem


class PlannerError(Exception):
    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


def extract_json(text: str) -> str:
    """Pull the first JSON object out of a model response. Tolerates leading
    prose or markdown fences even though the prompt forbids them."""
    trimmed = text.strip()
    if trimmed.startswith("{"):
        return trimmed
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", trimmed)
    if fence and fence.group(1):
        return fence.group(1)
    first = trimmed.find("{")
    last = trimmed.rfind("}")
    if first >= 0 and last > first:
        return trimmed[first : last + 1]
    raise PlannerError("No JSON object found in planner response", text)


def _build_claude_argv(prompt: str, model: str | None) -> list[str]:  # noqa: ARG001
    """Argv for a ``claude --print`` call. ``--model`` is inserted only when a
    model is given (else the CLI uses the account default). Pure → unit-tested.

    ``--output-format=json`` (T0.5, 2026-07-10): the CLI wraps the response in
    a result envelope carrying REAL token usage + cost, which the trace records
    instead of the old len/4 guesses. :func:`parse_cli_envelope` unwraps it;
    anything that doesn't parse as the envelope falls back to raw-stdout
    behavior, so callers still just receive the response text.

    The ``prompt`` parameter is kept for backwards-compat with the pre-2026-07-03
    signature but is NO LONGER appended to argv — it now rides on stdin (see
    :func:`call_claude`) to avoid ``[Errno 7] Argument list too long`` when the
    goal-planner's prompt (log + deliveries + steering) crosses the OS ARG_MAX
    limit (~128 KB on Linux). Live-hit closeloop-mission-v2 2026-07-03T18:35Z."""
    argv = [CLAUDE_BIN, "--print", "--output-format=json"]
    if model:
        argv += ["--model", model]
    return argv


@dataclass(frozen=True)
class CliEnvelope:
    """The ``claude --print --output-format json`` result envelope — only the
    fields devclaw consumes. Shape verified empirically 2026-07-10 against the
    live CLI; a scrubbed capture is committed at
    ``tests/fixtures/claude_print_json_envelope.json``::

        {"type": "result", "subtype": "success", "is_error": false,
         "result": "<text>", "total_cost_usd": 0.0188989, "duration_ms": 2298,
         "usage": {"input_tokens": 10, "output_tokens": 38,
                   "cache_read_input_tokens": 17209,
                   "cache_creation_input_tokens": 8201, ...}, ...}
    """

    result_text: str = ""
    subtype: str = ""
    is_error: bool = False
    #: human-readable failure wording for error envelopes. The quota guard
    #: (loom.limits.classify_failure) regexes PlannerError messages, so this
    #: MUST carry the CLI's raw wording verbatim.
    error_text: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read: int | None = None
    cache_creation: int | None = None
    cost_usd: float | None = None


def _usage_int(usage: dict, key: str) -> int | None:
    v = usage.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def parse_cli_envelope(stdout: str) -> CliEnvelope | None:
    """Parse CLI stdout as the ``--output-format json`` result envelope.

    Returns ``None`` whenever stdout is NOT the envelope — not JSON, not a
    dict, missing ``type == "result"``, or a "success" without a string
    ``result``. The caller then falls back to treating raw stdout as the
    response text, exactly like the pre-json-mode behavior. Deliberately
    defensive: a CLI version drift must degrade to the old behavior, never
    crash cognition."""
    try:
        parsed = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or parsed.get("type") != "result":
        return None
    subtype = str(parsed.get("subtype") or "")
    is_error = bool(parsed.get("is_error")) or subtype != "success"
    result = parsed.get("result")
    if is_error:
        # Prefer the CLI's own wording; fall back to the whole envelope so no
        # failure text (quota wording!) is ever lost to the classifier.
        if isinstance(result, str) and result.strip():
            error_text = result
        elif isinstance(parsed.get("error"), str) and parsed["error"].strip():
            error_text = parsed["error"]
        else:
            error_text = json.dumps(parsed, default=str)
    else:
        if not isinstance(result, str):
            return None  # envelope-shaped but no usable text → raw fallback
        error_text = ""
    raw_usage = parsed.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    cost = parsed.get("total_cost_usd")
    return CliEnvelope(
        result_text=result if isinstance(result, str) else "",
        subtype=subtype,
        is_error=is_error,
        error_text=error_text,
        tokens_in=_usage_int(usage, "input_tokens"),
        tokens_out=_usage_int(usage, "output_tokens"),
        cache_read=_usage_int(usage, "cache_read_input_tokens"),
        cache_creation=_usage_int(usage, "cache_creation_input_tokens"),
        cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
    )


def _record_timeout_diagnostics(
    *,
    role: str,
    model: str | None,
    prompt: str,
    argv_head: str,
    started: int,
    bytes_out: int | None,
    first_byte_at: int | None,
) -> None:
    """Make a cognition timeout LEGIBLE (2026-07-24, cognition-timeout-observability).

    A bare ``"timeout"`` couldn't distinguish the two causes that need OPPOSITE
    fixes: the CLI hung before emitting a byte (``bytes_out == 0`` /
    ``first_byte_ms`` never set → auth / network / queue, an *infra* stall) vs a
    genuinely slow-or-large generation (``bytes_out > 0`` → a *work-size*
    problem). ``input_chars`` correlates timeouts with prompt size (the third
    discriminator). The signals land on the trace AND on one greppable
    ``devclaw.cognition.timeout`` stderr line so the cross-night distribution is
    recoverable from container logs. ``bytes_out`` is ``None`` only on the
    buffered fallback path (test doubles); real subprocesses always report it.

    Deliberately does NOT touch the raised ``PlannerError`` wording — the quota
    classifier (``loom.limits.classify_failure``) regexes that message, so it
    stays byte-identical."""
    latency = _trace.now_ms() - started
    input_chars = len(prompt)
    bytes_str = "unknown" if bytes_out is None else str(bytes_out)
    fb_ms = (first_byte_at - started) if first_byte_at is not None else None
    fb_str = "none" if fb_ms is None else str(fb_ms)
    diag = (
        f"timeout input_chars={input_chars} bytes_out={bytes_str} "
        f"first_byte_ms={fb_str}"
    )
    sys.stderr.write(
        f"devclaw.cognition.timeout role={role} model={model or '-'} "
        f"elapsed_ms={latency} input_chars={input_chars} "
        f"bytes_out={bytes_str} first_byte_ms={fb_str}\n"
    )
    _trace.record_cognition(
        role=role, model=model or "", prompt=prompt, response="",
        latency_ms=latency, error=diag,
    )
    _trace.record_subprocess(
        cmd="claude --print", argv_head=argv_head, latency_ms=latency,
        exit_code=None, error=diag,
    )


async def call_claude(
    prompt: str,
    model: str | None = None,
    *,
    role: str = "unknown",
    timeout_ms: int | None = None,
) -> str:
    """Spawn ``claude --print`` and return the response text, with a bounded
    retry on a TRANSIENT failure.

    A single attempt is :func:`_call_claude_once` (the whole subprocess +
    envelope path). Wrapping it here is the clean-run fix (2026-07-30): a
    transient infra hiccup — the CLI OOM-killed mid-call (``exited -9``) under
    host memory pressure, a timeout, an ``overloaded``/network blip — used to
    raise straight to the caller and dead-stop the goal. Now it retries up to
    :data:`COGNITION_MAX_RETRIES` times with a short backoff, so the run carries
    on when the machine was only momentarily overwhelmed.

    The fail-closed / pause invariants are untouched, by construction:
    - ONLY a ``TRANSIENT`` classification retries. QUOTA / RATE / AUTH re-raise
      on the FIRST failure so the caller's pause-and-resume machinery still fires
      (retrying into a live usage cap would just burn it); a ``REAL`` bug fails
      fast with its feedback.
    - Classification is on the SAME ``str(exc)`` the downstream classifiers read,
      so the quota-guard wording (see :func:`_call_claude_once`) still routes a
      usage limit to a pause, never a retry.
    - When the retries exhaust, the LAST error is raised exactly as a single
      attempt would — the review gate still fails closed, the planner still
      surfaces the error. Retries fire only on an already-failed real call, never
      on an idle tick, so the zero-token idle guard is unaffected.
    """
    attempt = 0
    while True:
        try:
            return await _call_claude_once(
                prompt, model, role=role, timeout_ms=timeout_ms
            )
        except PlannerError as exc:
            cls = classify_failure(str(exc))
            if cls.kind is not FailureKind.TRANSIENT or attempt >= COGNITION_MAX_RETRIES:
                raise
            delay = _retry_backoff_s(attempt, cls.retry_after_s)
            sys.stderr.write(
                f"devclaw.cognition.retry role={role} kind=transient "
                f"matched={cls.matched or 'transient'} "
                f"attempt={attempt + 1}/{COGNITION_MAX_RETRIES} backoff_s={delay:g}\n"
            )
            attempt += 1
            await _RETRY_SLEEP(delay)


async def _call_claude_once(
    prompt: str,
    model: str | None = None,
    *,
    role: str = "unknown",
    timeout_ms: int | None = None,
) -> str:
    """One attempt, gated by the host-cognition semaphore: acquire BEFORE the
    subprocess spawns, release only after the process has exited — so at most
    ``DEVCLAW_MAX_HOST_COGNITION`` host ``claude`` processes ever coexist. A
    queued caller just waits its turn; nothing errors. The cognition timeout
    (``asyncio.wait_for`` inside :func:`_spawn_claude_once`) starts AFTER the
    acquire, so time spent queued behind the semaphore never counts against a
    call's timeout budget."""
    async with _host_cognition_semaphore():
        return await _spawn_claude_once(prompt, model, role=role, timeout_ms=timeout_ms)


async def _spawn_claude_once(
    prompt: str,
    model: str | None = None,
    *,
    role: str = "unknown",
    timeout_ms: int | None = None,
) -> str:
    """One ``claude --print --output-format json`` attempt: return the response
    TEXT (the envelope's ``result`` field — callers parse their own
    YAML/JSON out of it, contract unchanged; stdout that doesn't parse as the
    envelope is returned raw, as before json mode). Real token usage + cost from
    the envelope flow into the cognition trace. ``model``
    picks the tier (alias or full id); None → account default. ``role`` labels
    the cognition site (planner / evaluator / grill / judge / summary / review /
    research) for the trace recorder. ``timeout_ms`` overrides
    :data:`PLANNER_TIMEOUT_MS` for this call — roles whose output volume warrants
    a larger budget (decomposer) pass their own value. Injected into cognition
    roles so tests can stub the subprocess; each role binds its own
    model+role+timeout via :func:`claude_with_model`."""
    effective_timeout_ms = timeout_ms if timeout_ms is not None else PLANNER_TIMEOUT_MS
    env = dict(os.environ)
    # Belt + suspenders: never let an API key override the OAuth session.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    argv = _build_claude_argv(prompt, model)
    argv_head = f"{CLAUDE_BIN} --print" + (f" --model {model}" if model else "")
    started = _trace.now_ms()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            # 2026-07-03 argv → stdin migration: the goal-planner's prompt (log
            # + deliveries + steering) crossed ARG_MAX on closeloop-mission-v2
            # after ~20 dispatches, and every subsequent plan attempt hit
            # ``[Errno 7] Argument list too long``. The prompt now rides on
            # stdin instead. ``claude --print`` reads the whole stdin as the
            # prompt when argv doesn't provide one; the read path below feeds it
            # and closes stdin, avoiding the "no stdin data received in 3s"
            # warning the old ``stdin=DEVNULL`` path was working around.
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        latency = _trace.now_ms() - started
        _trace.record_cognition(
            role=role, model=model or "", prompt=prompt, response="",
            latency_ms=latency, error=f"spawn failed: {exc}",
        )
        _trace.record_subprocess(
            cmd="claude --print", argv_head=argv_head, latency_ms=latency,
            exit_code=None, error=f"spawn failed: {exc}",
        )
        raise PlannerError(f"Failed to spawn {CLAUDE_BIN}: {exc}") from exc

    prompt_bytes = prompt.encode("utf-8")
    timeout_s = effective_timeout_ms / 1000
    # Real subprocesses (``stdout`` is an asyncio ``StreamReader``) take the
    # streaming read path so a timeout can capture partial output + first-byte
    # timing — the two signals that separate "hung before first token" from
    # "slow/large generation" (see :func:`_record_timeout_diagnostics`). Test
    # doubles that implement only ``.communicate()`` (no ``.stdout``) fall
    # through to the original buffered call, byte-for-byte unchanged.
    if getattr(proc, "stdout", None) is not None and getattr(proc, "stderr", None) is not None:
        stdout_buf = bytearray()
        stderr_buf = bytearray()
        first_byte_at: list[int | None] = [None]

        async def _feed_stdin() -> None:
            stdin = proc.stdin
            if stdin is None:
                return
            try:
                stdin.write(prompt_bytes)
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # child exited before consuming stdin — not a call failure
            finally:
                try:
                    stdin.close()
                except Exception:  # noqa: BLE001 — closing a dead pipe is benign
                    pass

        async def _drain(stream, buf: bytearray, mark_first: bool) -> None:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                if mark_first and first_byte_at[0] is None:
                    first_byte_at[0] = _trace.now_ms()
                buf.extend(chunk)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _feed_stdin(),
                    _drain(proc.stdout, stdout_buf, True),
                    _drain(proc.stderr, stderr_buf, False),
                ),
                timeout=timeout_s,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            _record_timeout_diagnostics(
                role=role, model=model, prompt=prompt, argv_head=argv_head,
                started=started, bytes_out=len(stdout_buf),
                first_byte_at=first_byte_at[0],
            )
            raise PlannerError(f"claude --print timed out after {effective_timeout_ms}ms")
        stdout_b, stderr_b = bytes(stdout_buf), bytes(stderr_buf)
    else:
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=prompt_bytes),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            _record_timeout_diagnostics(
                role=role, model=model, prompt=prompt, argv_head=argv_head,
                started=started, bytes_out=None, first_byte_at=None,
            )
            raise PlannerError(f"claude --print timed out after {effective_timeout_ms}ms")

    stdout = stdout_b.decode("utf-8", "replace")
    stderr = stderr_b.decode("utf-8", "replace")
    latency = _trace.now_ms() - started
    envelope = parse_cli_envelope(stdout)
    _trace.record_subprocess(
        cmd="claude --print", argv_head=argv_head, latency_ms=latency,
        exit_code=proc.returncode, error=(stderr[:200] if proc.returncode != 0 else ""),
    )
    if proc.returncode != 0:
        # CRITICAL INVARIANT — the quota guard. A Claude usage-limit ("You're
        # out of extra usage · resets 10pm (UTC)") comes back on STDOUT with an
        # EMPTY stderr, and the entire pause machinery keys off
        # ``classify_failure`` regexing this PlannerError's message. Whatever
        # json mode does with that wording — wraps it in an error envelope, or
        # prints it as plain non-JSON text — the raw wording MUST land verbatim
        # in the message: envelope error text when it parses, raw stdout tail
        # when it doesn't.
        detail = (
            envelope.error_text
            if envelope is not None and envelope.is_error
            else stdout[-500:]
        )
        _trace.record_cognition(
            role=role, model=model or "", prompt=prompt, response=stdout,
            latency_ms=latency, error=f"exit={proc.returncode}; stderr={stderr[:200]}",
        )
        raise PlannerError(
            f"claude --print exited {proc.returncode}. stderr:\n{stderr}\n"
            f"stdout:\n{detail}",
            stdout,
        )
    if envelope is None:
        # Not the JSON envelope (CLI version drift, plain-text output). Degrade
        # to the pre-json-mode behavior: raw stdout IS the response text. One
        # breadcrumb on stderr so the drift is visible without breaking cognition.
        sys.stderr.write(
            "devclaw: claude --print stdout did not parse as a JSON result "
            "envelope; treating raw stdout as the response text\n"
        )
        _trace.record_cognition(
            role=role, model=model or "", prompt=prompt, response=stdout, latency_ms=latency,
        )
        return stdout
    if envelope.is_error:
        # Exit 0 but the envelope reports an error. Same invariant as above:
        # the envelope's wording flows verbatim into the message for the
        # classifier (quota/rate-limit wording must survive).
        _trace.record_cognition(
            role=role, model=model or "", prompt=prompt, response=stdout,
            latency_ms=latency,
            error=f"cli error envelope ({envelope.subtype or 'unknown'}): "
                  f"{envelope.error_text[:200]}",
        )
        raise PlannerError(
            f"claude --print returned an error envelope "
            f"(subtype={envelope.subtype or 'unknown'}): {envelope.error_text}",
            stdout,
        )
    _trace.record_cognition(
        role=role, model=model or "", prompt=prompt,
        response=envelope.result_text, latency_ms=latency,
        tokens_in=envelope.tokens_in, tokens_out=envelope.tokens_out,
        cache_read=envelope.cache_read, cache_creation=envelope.cache_creation,
        cost_usd=envelope.cost_usd,
    )
    return envelope.result_text


def claude_with_model(
    model: str | None,
    *,
    role: str = "unknown",
    timeout_ms: int | None = None,
) -> Callable[[str], Awaitable[str]]:
    """A one-argument cognition caller bound to a model + role label. Routes
    through the configured :class:`~devclaw.cognition.Cognition` (claude by
    default; ``DEVCLAW_COGNITION=stub`` for offline harnesses). ``timeout_ms``
    overrides the default ceiling for this role — pass it when the role's
    expected output volume routinely exceeds the global default (decomposer).
    Backend-swap happens at the cognition seam — this factory keeps its
    historical name + signature so existing callers stay untouched."""
    from .cognition import bind

    return bind(model, role=role, timeout_ms=timeout_ms)
