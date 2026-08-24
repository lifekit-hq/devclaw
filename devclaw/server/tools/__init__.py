"""All MCP tool decorators — the chef's menu.

Each tool delegates to the long-lived services in ``_state`` (queue, store,
goals, registry) or to a sibling module (``deploy``, ``repo``). The tools stay
thin on purpose: validate inputs, dispatch, return JSON. Cognition lives below
(planner / evaluator / review gate), not here.

``server/tools.py`` had grown to ~1,740 lines and forty-seven tools in one
module — the same smell ``http.py`` had before the ``routes/`` split. The
tools are split by DOMAIN here; behaviour is unchanged. Two conventions this
package depends on, both inherited from ``routes/``:

* **Registration is an import side effect.** ``@mcp.tool`` binds at module
  import, so a module that nothing imports registers nothing. This package
  imports every module below for exactly that reason — never delete one of
  those imports as "unused". The only external consumer is
  ``devclaw/server/__init__.py`` (``from . import tools``).
* **State is rebound at module level** (``from .._state import queue``), which
  is what lets a test ``monkeypatch.setattr(<module>, "queue", …)``. The patch
  reaches only the module where the tool is DEFINED, so a tool and the test
  that patches it move together (project resolution crosses ``_common``, so
  its ``registry`` is patched there).

Every tool callable is re-exported below so ``getattr(tools, name)`` keeps
resolving the full menu (the eval harness picks tools by name).
"""

from __future__ import annotations

from . import control, delivery, goals, intake, observability, projects, tasks  # noqa: F401

from .control import (  # noqa: F401
    get_run_schedule,
    clear_usage_pause,
    set_operator_hold,
    set_run_schedule,
)
from .delivery import (  # noqa: F401
    create_repo,
    delete_repo,
    deploy_project,
    deploy_status,
    list_deploys,
    stop_deploy,
)
from .goals import (  # noqa: F401
    cancel_goal,
    create_goal,
    dry_evaluate,
    evaluate_goal,
    get_goal,
    get_trace,
    list_goals,
    resume_goal,
    scope_grill,
    set_goal_strictness,
    start_program,
    steer_goal,
    tail_goal,
    verify_goal,
)
from .intake import (  # noqa: F401
    file_intake,
    grade_backlog,
    onboard,
    regrade_intake,
)
from .observability import (  # noqa: F401
    get_events,
    get_program,
    get_scorecard_metrics,
    get_status,
    list_problems,
    list_programs,
    list_tasks,
    review_trends,
)
from .projects import (  # noqa: F401
    delete_project,
    link_goal,
    list_projects,
    project_status,
    register_project,
    update_project,
)
from .tasks import (  # noqa: F401
    cancel_program,
    cancel_task,
    dispatch_task,
    fix_bug,
    implement_feature,
    review_repository,
)
