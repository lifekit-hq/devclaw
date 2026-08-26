# Contract: project sizing write surface (MCP + console)

Mirrors the `sandbox_image` override contract (ADR 0005) exactly.

## MCP `update_project`

Two new optional parameters:

| Param | Type | Semantics |
|---|---|---|
| `sandbox_memory` | `str \| None` | docker mem string (`"6g"`, `"3072m"`); `"inherit"` clears to instance default; `None`/omitted = leave unchanged |
| `sandbox_cpus` | `str \| None` | positive float string (`"4.0"`); `"inherit"` clears; `None` = unchanged |

Errors (raised as `ToolError`, text verbatim from the registry validator):

- malformed value → names the accepted grammar
- unadmittable memory → `sandbox_memory <v> can never be admitted on this
  host: <v> + cognition reserve <r> exceeds host MemTotal <t>. Lower the
  override or grow the host.`

`register_project` is intentionally unchanged (it does not expose
`sandbox_image` today either; the asymmetry is pre-existing and out of
scope).

## Console HTTP (`server/routes/projects.py`)

`sandbox_memory`, `sandbox_cpus` join `_OVR_FREE_STR`; the route pre-checks
grammar for a friendly 400, the registry raise stays the backstop. Both
surface in `_project_overrides` and `Project.to_dict`
(`sandboxMemory`/`sandboxCpus`).

## Doctor (spec 016 FR-014)

New `checks_instance` check `project_sandbox_sizing`: every stored override
parses AND remains admittable against the current host MemTotal (a host
shrink after a valid write is exactly the drift class doctor exists for).
Ships with a seeded-fault test.
