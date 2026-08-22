"""HTTP route modules for the console-facing JSON API.

``server/http.py`` had grown to ~1,830 lines and forty routes in one module —
navigable only by grep. The routes are split by RESOURCE here; behaviour is
unchanged, and every module registers its routes on the shared FastMCP
instance the moment it is imported.

Two conventions this package depends on, both inherited from ``http.py``:

* **Registration is an import side effect.** ``@mcp.custom_route`` binds at
  module import, so a module that nothing imports serves nothing. ``http.py``
  imports every module here for exactly that reason — never delete one of
  those imports as "unused".
* **State is rebound at module level** (``from .._state import store``), which
  is what lets a test ``monkeypatch.setattr(<module>, "store", …)``. The patch
  reaches only the module where the route is DEFINED, so a route and the test
  that patches it move together.
"""
