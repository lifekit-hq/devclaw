"""P2a of the cognition demolition (docs/proposals/cognition-demolition.md): the
pure wayfinder plan-map core — model, parser, and the mechanical frontier
selectors that REPLACE the planner LLM's per-tick "what's next" with a
deterministic walk of persisted state. No I/O; no docker/claude."""

from __future__ import annotations

from devclaw.goal.wayfinder import (
    WayfinderMap,
    WayfinderTicket,
    is_complete,
    is_stalled_on_deps,
    next_frontier_ticket,
    parse_map,
)


def _map_issue(number=1, destination="Ship the CRUD API", notes="", oos=()):
    body = f"## Destination\n{destination}\n"
    if notes:
        body += f"\n## Notes\n{notes}\n"
    if oos:
        body += "\n## Out of scope\n" + "".join(f"- {x}\n" for x in oos)
    return {"number": number, "title": "map", "state": "open",
            "body": body, "labels": ["wayfinder:map"]}


def _ticket_issue(number, title, kind="task", state="open", body=""):
    return {"number": number, "title": title, "state": state, "body": body,
            "labels": [f"wayfinder:{kind}"]}


# ---- parse_map --------------------------------------------------------------


def test_parse_map_basic_index_and_tickets():
    m = parse_map([
        _map_issue(1, destination="Ship /api/crons", notes="dotnet minimal API",
                   oos=["auth", "the UI"]),
        _ticket_issue(2, "GET or POST first?", kind="grilling"),
        _ticket_issue(3, "which ORM?", kind="research"),
    ])
    assert m is not None
    assert m.map_number == 1
    assert m.destination == "Ship /api/crons"
    assert m.notes == "dotnet minimal API"
    assert m.out_of_scope == ("auth", "the UI")
    assert [t.number for t in m.tickets] == [2, 3]
    assert m.tickets[0].kind == "grilling"


def test_parse_map_none_when_no_map_issue():
    # decision tickets but no wayfinder:map index → not charted yet
    assert parse_map([_ticket_issue(2, "a question")]) is None
    assert parse_map([]) is None


def test_parse_map_ignores_unrelated_issues():
    m = parse_map([
        _map_issue(1),
        {"number": 9, "title": "unrelated bug", "state": "open",
         "body": "", "labels": ["bug"]},
        _ticket_issue(2, "real ticket"),
    ])
    assert m is not None
    assert [t.number for t in m.tickets] == [2]


def test_parse_map_labels_as_dicts():
    # gh sometimes returns labels as {"name": ...} objects, not bare strings
    m = parse_map([
        {"number": 1, "title": "map", "state": "open",
         "body": "## Destination\nX", "labels": [{"name": "wayfinder:map"}]},
        {"number": 2, "title": "q", "state": "open", "body": "",
         "labels": [{"name": "wayfinder:task"}]},
    ])
    assert m is not None and [t.number for t in m.tickets] == [2]


def test_parse_map_tolerates_malformed_ticket():
    # missing number/body/title must degrade, never raise
    m = parse_map([
        _map_issue(1),
        {"state": "open", "labels": ["wayfinder:task"]},  # no number/title/body
    ])
    assert m is not None
    assert m.tickets[0].number == 0
    assert m.tickets[0].blocked_by == ()


def test_parse_blocked_by_and_resolution_from_body():
    m = parse_map([
        _map_issue(1),
        _ticket_issue(5, "impl", state="open",
                      body="do the thing.\nBlocked by #2\nBlocked by #3"),
        _ticket_issue(2, "decide shape", state="closed",
                      body="Resolution: use a record type"),
    ])
    impl = next(t for t in m.tickets if t.number == 5)
    decided = next(t for t in m.tickets if t.number == 2)
    assert impl.blocked_by == (2, 3)
    assert decided.resolution == "use a record type"


def test_resolution_only_captured_when_closed():
    m = parse_map([
        _map_issue(1),
        _ticket_issue(2, "open one", state="open",
                      body="Resolution: premature (still open)"),
    ])
    assert m.tickets[0].resolution == ""  # open ticket carries no resolution


def test_blocked_by_dedups_preserving_order():
    m = parse_map([
        _map_issue(1),
        _ticket_issue(4, "x", body="Blocked by #3\nBlocked by #2\nBlocked by #3"),
    ])
    assert m.tickets[0].blocked_by == (3, 2)


# ---- next_frontier_ticket (the mechanical "what's next") --------------------


def test_next_frontier_picks_lowest_unblocked_open():
    m = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(3, "c", "task", "open"),
        WayfinderTicket(2, "b", "task", "open"),
    ))
    nxt = next_frontier_ticket(m)
    assert nxt is not None and nxt.number == 2  # deterministic: lowest number


def test_next_frontier_skips_blocked_by_open_dependency():
    m = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "blocker", "task", "open"),
        WayfinderTicket(3, "blocked", "task", "open", blocked_by=(2,)),
    ))
    nxt = next_frontier_ticket(m)
    assert nxt.number == 2  # #3 waits on the still-open #2


def test_next_frontier_unblocks_once_dependency_closed():
    m = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "blocker", "task", "closed", resolution="done"),
        WayfinderTicket(3, "blocked", "task", "open", blocked_by=(2,)),
    ))
    nxt = next_frontier_ticket(m)
    assert nxt.number == 3  # blocker closed → #3 is now the frontier


def test_next_frontier_none_when_all_closed():
    m = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "a", "task", "closed"),
    ))
    assert next_frontier_ticket(m) is None


def test_next_frontier_none_on_dependency_cycle():
    m = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "a", "task", "open", blocked_by=(3,)),
        WayfinderTicket(3, "b", "task", "open", blocked_by=(2,)),
    ))
    assert next_frontier_ticket(m) is None  # neither is unblocked


# ---- is_complete / is_stalled_on_deps --------------------------------------


def test_is_complete_true_only_when_tickets_exist_and_all_closed():
    all_closed = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "a", "task", "closed"),
        WayfinderTicket(3, "b", "task", "closed"),
    ))
    assert is_complete(all_closed) is True


def test_is_complete_false_with_an_open_ticket():
    m = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "a", "task", "closed"),
        WayfinderTicket(3, "b", "task", "open"),
    ))
    assert is_complete(m) is False


def test_is_complete_false_when_empty():
    # an uncharted map (no tickets) is "not yet planned", never "done"
    assert is_complete(WayfinderMap(map_number=1, destination="d")) is False


def test_is_stalled_on_deps_true_only_when_open_but_all_blocked():
    cycle = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "a", "task", "open", blocked_by=(3,)),
        WayfinderTicket(3, "b", "task", "open", blocked_by=(2,)),
    ))
    assert is_stalled_on_deps(cycle) is True

    healthy = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "a", "task", "open"),
    ))
    assert is_stalled_on_deps(healthy) is False  # #2 is dispatchable

    done = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "a", "task", "closed"),
    ))
    assert is_stalled_on_deps(done) is False  # nothing open → not stalled, complete


def test_decisions_so_far_gists_closed_tickets():
    m = WayfinderMap(map_number=1, destination="d", tickets=(
        WayfinderTicket(2, "which ORM", "research", "closed", resolution="EF Core"),
        WayfinderTicket(3, "open one", "task", "open"),
    ))
    assert m.decisions_so_far == ("#2 which ORM: EF Core",)


def test_end_to_end_parse_then_walk():
    """The whole P2a contract in one: parse a real-shaped issue set, then walk it
    exactly as the tick will — dispatch the unblocked frontier, and only propose
    done once everything is closed."""
    issues = [
        _map_issue(1, destination="Ship /api/crons CRUD"),
        _ticket_issue(2, "record vs class?", kind="grilling", state="closed",
                      body="Resolution: record"),
        _ticket_issue(3, "write the endpoint", kind="task", state="open",
                      body="Blocked by #2"),
    ]
    m = parse_map(issues)
    assert not is_complete(m)
    nxt = next_frontier_ticket(m)
    assert nxt.number == 3  # #2 resolved → #3 is the frontier

    # simulate the worker closing #3
    closed = WayfinderMap(
        map_number=m.map_number, destination=m.destination,
        tickets=tuple(
            WayfinderTicket(t.number, t.title, t.kind, "closed", t.blocked_by,
                            t.resolution)
            for t in m.tickets
        ),
    )
    assert next_frontier_ticket(closed) is None
    assert is_complete(closed) is True  # → propose done (still gated on done-gate)
