# Prompts — the two the host writes

Applies to `devclaw/prompts/session.md`, `devclaw/prompts/done-gate.md` and
their one builder module, `devclaw/goal/prompts.py`. `tests/test_harness_docs_map.py`
asserts every path named here resolves.

- Templates render through `load_prompt(slug, **kwargs)` with `str.format` —
  literal braces are `{{ }}`. A new `{placeholder}` needs its kwarg in the builder.
- **Untrusted content is fenced.** Issue bodies, comments and CI logs enter
  through `fence_untrusted` from `devclaw/loom/untrusted.py`, and the template
  carries `UNTRUSTED_NOTE`. Anything a session or a stranger wrote is data.
- **State each rule ONCE, imperatively.** No persona, no incident history in a
  template — the war story goes in the commit message. Every line costs tokens
  on every session; the bar for a line is "changes model behaviour".
- **Facts, not conclusions.** The prompt hands the session the world — the PR,
  the CI state and failing logs, the newest instruction and devclaw records,
  the last exit — and never the host's opinion of what to do next.
- **The hand-back is the protocol.** The four exit lines in `session.md` are
  parsed by `devclaw/queue/settle.py`; the review JSON in `done-gate.md` is
  validated by `devclaw/goal/donegate.py`. Change a template and its parser together.
- **No host cognition.** There is no `claude --print` on the host; a judgment
  the host needs is a read-only session in the sandbox.
