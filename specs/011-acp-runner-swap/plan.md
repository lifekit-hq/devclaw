# Implementation Plan: ACP-Direct Runner (retire the OpenHands SDK)

**Branch**: `011-acp-runner-swap` | **Date**: 2026-08-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/011-acp-runner-swap/spec.md`

## Summary

Replace the OpenHands SDK middleman in the sandbox runner with a runner-owned,
zero-dependency ACP client (JSON-RPC 2.0 over the `claude-agent-acp`
subprocess's stdio). Everything around the drive seam — skills/hooks loading,
toolchain provisioning, verify gate, blocked/repo-notes parsing, the
`event:`/`result:` NDJSON wire contract, key-stripping — is preserved
byte-compatible; layers 1–4 need zero code change. The swap is proven by a
deterministic fake ACP agent in the stubbed suite plus the live-shakedown
ladder. Full decisions in [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.11+ (sandbox image + host venv; stdlib only for the new client)

**Primary Dependencies**: NONE added. Removed: `openhands-sdk`, `openhands-tools`, `agent-client-protocol` (pip). Unchanged: `@agentclientprotocol/claude-agent-acp` + `@anthropic-ai/claude-code` (npm, in-image).

**Storage**: N/A (runner is stateless; result rides stdout)

**Testing**: pytest, fully stubbed (fake ACP agent = scripted line sequences / subprocess; no docker, no claude), same `spec_from_file_location` import pattern as existing `tests/test_runner_*.py`

**Target Platform**: the per-task docker sandbox (`.sandcastle/Dockerfile`) and `DEVCLAW_ENGINE=host` mode

**Project Type**: worker-layer harness component (layer 5)

**Performance Goals**: N/A beyond parity — protocol overhead is noise next to agent turns

**Constraints**: runner⇄host wire contract byte-compatible (FR-002); OAuth-only env allowlist (FR-005); idle-timeout so a hung session fails loud (D7)

**Scale/Scope**: ~300-line new module, ~120-line surgical edit in `runner.py` (the `main()` drive block), requirements.txt shrink, Dockerfile pip-layer shrink, ~4 test modules touched/added, constitution + CLAUDE.md wording amendment, docs sweep

## Constitution Check

*GATE evaluated pre-Phase-0 and re-checked post-design — PASS (with one
explicit, spec-declared amendment).*

- **I. OAuth only** — PASS. `_refuse_api_key()` retained; agent env becomes an
  explicit allowlist (D9), strictly tighter than today.
- **II. Model-agnostic worker layer** — PASS with declared amendment (spec
  FR-010): the principle's letter names the `ACPAgent` call, which this
  feature deletes. The seam survives strengthened (one payload/env-selectable
  agent command driving a neutral protocol). Constitution + CLAUDE.md wording
  updated in the swap PR itself — never silently. Skills stay plain markdown,
  hooks stay bash, MCP wiring unchanged.
- **III. Zero-token idle** — PASS. No tick-path change; the runner runs only
  when a task is dispatched.
- **IV. Single writer to state** — PASS. Runner writes no host state; result
  still rides stdout to the engine.
- **V. Verification fails closed** — PASS. Verify gate, blocked short-circuit
  (blocked-before-verify), and failure classification preserved verbatim.
- **VI. Loud failure** — PASS and improved: new idle-timeout turns a silent
  hang into a legible failure; unknown ACP frames are tolerated-and-logged or
  fail loud, never silently dropped mid-contract.
- **VII. Fix the class, not the instance** — PASS: the swap removes the
  dependency-pin fragility *class* (D1) rather than re-pinning around it.

## Project Structure

### Documentation (this feature)

```text
specs/011-acp-runner-swap/
├── spec.md
├── plan.md              # this file
├── research.md          # D1–D9 decisions
├── data-model.md        # client/session/outcome shapes
├── quickstart.md        # validation ladder
├── contracts/
│   ├── runner-host-wire.md    # frozen NDJSON contract (FR-002)
│   └── runner-agent-acp.md    # the ACP subset the client speaks (FR-011)
└── tasks.md             # /speckit-tasks output (not created by plan)
```

### Source Code (repository root)

```text
openhands-runner/            # renamed → runner/ by a trailing mechanical PR (clarified)
├── runner.py                # EDIT: drive block swapped (openhands imports → acp_client), rest untouched
├── acp_client.py            # NEW: zero-dep JSON-RPC/ACP client (D1–D3, D7)
├── requirements.txt         # EDIT: empty of SDKs (stdlib-only runner)
├── sandbox-mcp.json         # unchanged
├── skills/ · hooks/         # unchanged (plain markdown / bash)
.sandcastle/Dockerfile       # EDIT: drop the pip openhands layer; npm layer unchanged
devclaw/                     # NO behavioral change (SC-005); docs/comments only
tests/
├── acp_fake_agent.py        # NEW: deterministic scripted ACP agent (fixture, FR-008)
├── test_acp_client.py       # NEW: protocol-level unit tests (D2, D3, D7)
├── test_runner_acp.py       # NEW: runner-through-fake-agent + workspace vendor-neutrality named regression (FR-004)
├── test_runner_usage.py     # EDIT: usage extractor semantics (D6)
├── test_runner_*.py         # unchanged (import pattern untouched)
docs/                        # architecture.md, flows/task-execution.md, reference/env-vars.md + INDEX.md tags
.specify/memory/constitution.md  # EDIT: Principle II wording (FR-010)
CLAUDE.md                    # EDIT: matching sentence (FR-010)
```

**Structure Decision**: single-project layout; the feature is one new sibling
module + a surgical edit inside the existing worker directory. The directory
rename to `runner/` is deliberately NOT in the swap PR (clarified 2026-08-19)
— it lands as a mechanical rename-only follow-up closing the arc.

## Slicing (firmed, per spec Assumptions)

- **PR 1 — the swap** (US1 + US2 + US3): `acp_client.py`, runner.py drive
  block, requirements/Dockerfile shrink, fake agent + all tests, constitution
  + CLAUDE.md wording, docs sweep. One PR because the image cannot build
  half-swapped (requirements and code must move together).
- **PR 2 — mechanical rename** `openhands-runner/` → `runner/` (paths in
  Dockerfile, tests, docs; zero logic).
- Live proof (quickstart L1–L3/L5 + #538 scenario replay) gates calling the
  arc done — evidence, not a PR.

## Complexity Tracking

No constitution violations; the Principle II wording change is a declared
amendment carried by FR-010, not a violation to justify.
