"""devclaw CLI — doctor, the run window, and the project registry, from a
terminal against the same SQLite file the server uses."""

from __future__ import annotations

import argparse
import json
from typing import Optional

from . import config as _config
from .dispatch_gate import _parse_hhmm
from .doctor import run_doctor
from .project_registry import ProjectExists, ProjectRegistry
from .state_store import StateStore


def _cmd_doctor(args) -> int:
    store = StateStore(_config.db_path())
    report = run_doctor(store, ProjectRegistry(_config.db_path()))
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for f in payload["findings"]:
            line = f"[{f['verdict']:>7}] {f['check_id']}: {f['evidence']}"
            if f["remedy"]:
                line += f"  → {f['remedy']}"
            print(line)
        c = payload["counts"]
        print(f"\n{'healthy' if payload['healthy'] else 'FINDINGS'} — ok {c['ok']} / warn {c['warn']} "
              f"/ fail {c['fail']} / unknown {c['unknown']}")
    return 1 if (payload["counts"]["fail"] or payload["counts"]["unknown"]) else 0


def _cmd_schedule(args) -> int:
    store = StateStore(_config.db_path())
    if args.cmd == "show":
        print(json.dumps(store.get_run_schedule(), indent=2))
        return 0
    from zoneinfo import ZoneInfo

    cur = store.get_run_schedule()
    start, end, tz = args.start or cur["start"], args.end or cur["end"], args.tz or cur["tz"]
    if _parse_hhmm(start) is None or _parse_hhmm(end) is None:
        print("start/end must be HH:MM")
        return 2
    try:
        ZoneInfo(tz)
    except Exception:
        print(f"unknown timezone {tz!r}")
        return 2
    enabled = True if args.enable else False if args.disable else cur["enabled"]
    store.set_run_schedule(enabled, start, end, tz)
    print(json.dumps(store.get_run_schedule(), indent=2))
    return 0


def _cmd_projects(args) -> int:
    reg = ProjectRegistry(_config.db_path())
    if args.cmd == "list":
        for p in reg.list(status=args.status):
            print(json.dumps(p.to_dict()) if args.json else f"{p.id:<28} {p.status:<9} {p.repo_url or '-'}")
        return 0
    if args.cmd == "show":
        found = reg.get(args.id)
        if found is None:
            print(f"unknown project: {args.id}")
            return 1
        print(json.dumps(found.to_dict(), indent=2))
        return 0
    if args.cmd == "register":
        try:
            p = reg.create(id=args.id, name=args.name, repo_url=args.repo_url,
                           workspace_dir=args.workspace_dir, notes=args.notes or "")
        except ProjectExists:
            print(f"project already exists: {args.id}")
            return 1
        print(json.dumps(p.to_dict(), indent=2))
        return 0
    if args.cmd == "update":
        try:
            p2 = reg.update(args.id, name=args.name, repo_url=args.repo_url,
                           workspace_dir=args.workspace_dir, status=args.status, notes=args.notes)
        except KeyError:
            print(f"unknown project: {args.id}")
            return 1
        print(json.dumps(p2.to_dict(), indent=2))
        return 0
    if args.cmd == "rm":
        print("deleted" if reg.delete(args.id) else f"unknown project: {args.id}")
        return 0
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devclaw", description="devclaw control-plane CLI")
    sub = parser.add_subparsers(dest="group", required=True)

    p_doctor = sub.add_parser("doctor", help="read-only instance diagnostics")
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_sched = sub.add_parser("schedule", help="the daily run window that gates NEW sessions")
    ssub = p_sched.add_subparsers(dest="cmd", required=True)
    ssub.add_parser("show").set_defaults(func=_cmd_schedule)
    s_set = ssub.add_parser("set")
    s_set.add_argument("--start")
    s_set.add_argument("--end")
    s_set.add_argument("--tz")
    grp = s_set.add_mutually_exclusive_group()
    grp.add_argument("--enable", action="store_true")
    grp.add_argument("--disable", action="store_true")
    s_set.set_defaults(func=_cmd_schedule)

    projects = sub.add_parser("projects", help="manage the project registry")
    psub = projects.add_subparsers(dest="cmd", required=True)
    p_list = psub.add_parser("list")
    p_list.add_argument("--status", choices=["active", "paused", "archived"])
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=_cmd_projects)
    p_show = psub.add_parser("show")
    p_show.add_argument("id")
    p_show.set_defaults(func=_cmd_projects)
    p_reg = psub.add_parser("register")
    p_reg.add_argument("id")
    p_reg.add_argument("name")
    p_reg.add_argument("--repo-url")
    p_reg.add_argument("--workspace-dir")
    p_reg.add_argument("--notes")
    p_reg.set_defaults(func=_cmd_projects)
    p_upd = psub.add_parser("update")
    p_upd.add_argument("id")
    p_upd.add_argument("--name")
    p_upd.add_argument("--repo-url")
    p_upd.add_argument("--workspace-dir")
    p_upd.add_argument("--status", choices=["active", "paused", "archived"])
    p_upd.add_argument("--notes")
    p_upd.set_defaults(func=_cmd_projects)
    p_rm = psub.add_parser("rm")
    p_rm.add_argument("id")
    p_rm.set_defaults(func=_cmd_projects)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
