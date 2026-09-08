# Implementation Plan: One credential registry

**Branch**: `feat/one-credential-registry` | **Date**: 2026-09-08 | **Spec**: [spec.md](./spec.md)

## Summary

A leaf module `devclaw/credentials.py` declares every credential once (name, scope, hops). The boot guard, doctor, `engine/sandcastle.py`, `engine/host.py`, `cognition.py`, `llm_call.py` and `env_cap.py` derive their behaviour from it; the task payload carries `agent_env` so `runner/runner.py` forwards exactly the registry's `agent` names. A structural test forbids a credential name anywhere else.

## Technical Context

Layer 4 (engine) + the leaf modules; runner (layer 5) reads one new payload key. No new tables, no new env vars, no new kinds. Import-linter gains a leaf contract for `devclaw.credentials` and an `ignore_imports` line for `llm_call -> credentials` (a leaf reading a leaf).

## Constitution Check

- I OAuth only: strengthened — one strip, one refused set. II model-agnostic: the runner spells nothing; the host declares. III zero-token: no tick-path change. IV single writer: untouched. V/VI: unchanged. VII class: six instance fixes named; the registry replaces the per-hop lists. IX: a missing FACT closed as a fact (the credential reaches the agent) with the brake being a build-time structural test, inside the safety domain.

## Project Structure

```
devclaw/credentials.py                 NEW — the registry (leaf)
devclaw/engine/sandcastle.py           iterates the registry; payload agent_env
devclaw/engine/host.py                 strip via registry; payload agent_env
devclaw/boot_guard.py                  required set from the registry
devclaw/env_cap.py · cognition.py · llm_call.py   prefixes / strip via registry
runner/runner.py                       _agent_env_vars(req) — forwards payload names
tests/test_credentials_single_registry.py   NEW — the structural guard
tests/test_sandbox_isolation.py        class-level forwarding + payload + runner tests
docs/reference/env-vars.md · docs/architecture.md · docs/flows/task-execution.md · CLAUDE.md · docs/INDEX.md
pyproject.toml                         import-linter leaf contract
```
