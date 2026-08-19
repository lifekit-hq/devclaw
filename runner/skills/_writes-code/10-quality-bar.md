# Quality bar

Code quality is part of your output, not just whether tests pass. Hold to a **production** bar — code you'd approve in a thorough review.

- **Before editing**, read the file + surrounding folder and judge it: coherent unit or a god object mixing concerns? If you see smells — god objects, mixed concerns, duplication crying for abstraction, catch-all spec files, names that don't earn their length — **refactor first, then add**. Sound engineering beats matching a bad local pattern.
- Put new code where it _belongs_; note any structural move in your summary.
- **Adding a mechanism** (a gate, a cache, a retry, a config knob)? Find the nearest existing pattern in this repo and copy its shape; name that precedent in your commit body. No precedent — say so and justify the new shape.
- Write **NO dead, placeholder, or no-op code** — every line does real work. A disabled button + `expect(visible)` is not implementation; it's a stub in disguise.
- Handle real edge and error cases, not only the happy path.
- Tests must genuinely exercise behaviour (including failure paths). **Never weaken or delete an existing test to go green.**

**Before finishing**, re-read your diff and answer two questions: (1) does it work — tests pass, behaviour correct, edges handled? (2) is the codebase healthier than before, or worse? A passing suite is necessary but not sufficient; if either answer is no, fix it before finishing.
