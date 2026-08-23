"""devclaw's git author identity — ONE configurable identity for every commit
devclaw produces, wherever the commit is made: the worker agent inside the
sandbox, the host-engine worker, or the delivery layer's own safety-net commit.

Why this module exists (live finding, 2026-07-28): commits went out under
whatever ambient git config the execution environment happened to inherit —
worker commits were authored as the OWNER's personal identity (a gitconfig
leaked through the environment), while delivery commits said a hardcoded
``devclaw@local``. Two identities, neither chosen. The owner wants devclaw's
work attributable at a glance: **author = devclaw**, with the worker model's
own ``Co-Authored-By: Claude …`` trailer riding along untouched (the model
stays visible as co-author; devclaw is the author).

Mechanism: the ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` env vars, not repo-local
config — git gives environment precedence over every config level, so the
identity holds regardless of what the sandbox image, a mounted homedir, or the
agent itself configures. Point ``DEVCLAW_GIT_EMAIL`` at a machine account's
GitHub noreply address to link the commits to a real GitHub profile; until
then the default keeps the pre-existing ``devclaw@local``.
"""

from __future__ import annotations


from . import config as _config

_DEFAULT_NAME = "devclaw"
_DEFAULT_EMAIL = "devclaw@local"


def git_name() -> str:
    v = _config.git_name()
    return v if v is not None else _DEFAULT_NAME


def git_email() -> str:
    v = _config.git_email()
    return v if v is not None else _DEFAULT_EMAIL


def git_identity_env() -> dict[str, str]:
    """The four env vars that pin both author and committer to devclaw."""
    name, email = git_name(), git_email()
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
