#!/usr/bin/env python3
"""Zero-LLM engineering-health metrics for the devclaw tree.

The measure half of the ``/eng-health`` ratchet: every number here is
mechanical (AST, grep, git), runs in seconds, and is comparable across runs.
Judgment happens in the skill, on top of these numbers, never instead of them.

    python evals/measure_eng_health.py                 # JSON to stdout
    python evals/measure_eng_health.py --compare docs/audits/eng-health.json

``--compare`` prints a delta table (metric | previous | now | direction) after
the JSON; a metric whose ``worse`` direction is "up" regresses when it grows.

A metric graduates OUT of this file the day a tripwire test pins it — the
script is meant to shrink.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIRS = ("devclaw", "runner")

# Package -> packages it must NOT import at runtime (layer rule, CLAUDE.md).
LAYER_DENY: dict[str, set[str]] = {
    "engine": {"goal", "queue", "server", "quality", "delivery"},
    "loom": {"goal", "queue", "server", "quality", "delivery", "engine", "state_store"},
    "quality": {"goal", "queue", "server"},
    "state_store": {"goal", "queue", "server", "engine", "quality", "delivery"},
    "delivery": {"goal", "queue", "server"},
    "goal": {"server"},
    "queue": {"server"},
}

# prompt template -> the caller module whose parser reads its output
PROMPT_CALLERS = {
    "devclaw/prompts/goal-evaluator.md": "devclaw/goal/evaluator.py",
    "devclaw/prompts/admission-lint.md": "devclaw/goal/admission_lint.py",
    "devclaw/prompts/intake-readiness.md": "devclaw/intake_readiness.py",
    "devclaw/prompts/self-triage.md": "devclaw/goal/triage.py",
    "devclaw/quality/prompts/review-gate.md": "devclaw/quality/__init__.py",
    "devclaw/quality/prompts/browser-reachability.md": "devclaw/quality/reachability.py",
}
GROUNDED_PROMPTS = set(PROMPT_CALLERS) - {"devclaw/prompts/admission-lint.md"}
# keys a caller reads from its INPUT rows (rendered into the prompt), not from
# the model's output — excluded from the schema-vs-parser check
INPUT_KEYS = {
    "devclaw/prompts/self-triage.md": {"category", "kind", "summary", "count", "terminal_count", "fingerprint", "last_seen"},
}

# direction in which a metric gets WORSE
WORSE = {
    "functions_over_80": "up", "functions_over_200": "up", "modules_over_1000": "up",
    "signatures_over_8_params": "up", "private_cross_imports": "up",
    "layer_violations": "up", "duplicate_body_groups": "up",
    "silent_broad_excepts": "up", "issue_refs_in_source": "up",
    "legacy_phrases": "up", "spec_status_mismatches": "up",
    "index_rows_over_1500": "up", "index_bytes": "up", "claude_md_lines": "up",
    "prompt_static_tokens_total": "up", "prompt_python_literal_chars": "up",
    "parser_keys_missing_from_schema": "up", "prompts_missing_grounding": "up",
    "prompts_without_eval_fixtures": "up", "sandbox_hardening_missing": "up",
    "config_env_vars": "up", "source_loc": "flat",
}


def py_files() -> list[Path]:
    out: list[Path] = []
    for d in SRC_DIRS:
        out += sorted(p for p in (ROOT / d).rglob("*.py") if "__pycache__" not in p.parts)
    return out


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def parse(p: Path) -> ast.Module | None:
    try:
        return ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def package_of(relpath: str) -> str:
    parts = relpath.split("/")
    if parts[0] == "runner":
        return "runner"
    return parts[1] if len(parts) > 2 else parts[1].removesuffix(".py")


def structure(files: list[Path]) -> dict:
    over80, over200, big, fat = [], [], [], []
    priv, layer = [], []
    loc = 0
    for p in files:
        r = rel(p)
        n = sum(1 for _ in p.open(encoding="utf-8"))
        loc += n
        if n > 1000:
            big.append({"file": r, "lines": n})
        tree = parse(p)
        if tree is None:
            continue
        pkg = package_of(r)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length > 200:
                    over200.append({"fn": f"{r}:{node.name}", "lines": length})
                if length > 80:
                    over80.append(f"{r}:{node.name}")
                a = node.args
                nparams = len(a.args) + len(a.kwonlyargs) + len(a.posonlyargs)
                if nparams > 8:
                    fat.append({"fn": f"{r}:{node.name}", "params": nparams})
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                # resolve relative imports inside devclaw/
                if node.level and r.startswith("devclaw/"):
                    base = r.split("/")[1:-1]
                    up = node.level - 1
                    base = base[: len(base) - up] if up else base
                    mod = ".".join(["devclaw", *base, mod])
                if not mod.startswith("devclaw."):
                    continue
                tgt = mod.split(".")[1]
                names = [al.name for al in node.names]
                if tgt != pkg and any(nm.startswith("_") for nm in names):
                    priv.append(f"{r} <- {mod}.{','.join(nm for nm in names if nm.startswith('_'))}")
                if tgt in LAYER_DENY.get(pkg, set()) and tgt != pkg:
                    layer.append(f"{r} -> {tgt}")
    return {
        "source_loc": loc,
        "functions_over_80": len(over80),
        "functions_over_200": sorted(over200, key=lambda d: -d["lines"]),
        "modules_over_1000": sorted(big, key=lambda d: -d["lines"]),
        "signatures_over_8_params": sorted(fat, key=lambda d: -d["params"]),
        "private_cross_imports": sorted(set(priv)),
        "layer_violations": sorted(set(layer)),
    }


class _Strip(ast.NodeTransformer):
    """Normalise a body so renamed locals/args still hash equal."""

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.AST:  # noqa: N802
        return ast.copy_location(ast.arg(arg="_", annotation=None), node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:  # noqa: N802
        if isinstance(node.value, str) and len(node.value) > 20:
            return ast.copy_location(ast.Constant(value="…"), node)
        return node


def duplication(files: list[Path]) -> dict:
    bodies: dict[str, set[str]] = defaultdict(set)
    silent: Counter = Counter()
    silent_total = 0
    for p in files:
        r = rel(p)
        tree = parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]
                if len(body) < 5:
                    continue
                dump = ast.dump(_Strip().visit(ast.Module(body=body, type_ignores=[])))
                bodies[hashlib.md5(dump.encode()).hexdigest()].add(f"{r}:{node.name}")
            if isinstance(node, ast.ExceptHandler):
                t = node.type
                broad = t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))
                if not broad:
                    continue
                loud = False
                for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if isinstance(sub, ast.Raise):
                        loud = True
                    if isinstance(sub, ast.Call):
                        f = sub.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                        if re.search(r"log|warn|error|print|trace|emit|record|notify|fail|mark|block", name, re.I):
                            loud = True
                if not loud:
                    silent[r] += 1
                    silent_total += 1
    groups = [sorted(v) for v in bodies.values() if len({x.split(":")[0] for x in v}) > 1]
    return {
        "duplicate_body_groups": sorted(groups),
        "silent_broad_excepts": silent_total,
        "silent_broad_excepts_top": silent.most_common(6),
    }


def noise(files: list[Path]) -> dict:
    refs: Counter = Counter()
    legacy = 0
    pat_ref = re.compile(r"#\d{3}\b")
    pat_leg = re.compile(r"\b(formerly|used to|legacy|was deleted|was removed|pre-0\d\d|retired)\b", re.I)
    for p in files:
        txt = p.read_text(encoding="utf-8")
        refs[rel(p)] += len(pat_ref.findall(txt))
        legacy += len(pat_leg.findall(txt))
    mism = []
    for spec in sorted((ROOT / "specs").glob("[0-9]*/spec.md")):
        s = spec.read_text(encoding="utf-8")
        m = re.search(r"^\*\*Status:?\*\*:? *(.*)$", s, re.M)
        status = (m.group(1) if m else "").strip()
        tasks = spec.with_name("tasks.md")
        if not tasks.exists():
            continue
        t = tasks.read_text(encoding="utf-8")
        done, open_ = len(re.findall(r"^- \[[xX]\]", t, re.M)), len(re.findall(r"^- \[ \]", t, re.M))
        if done and not open_ and (not status or status.lower().startswith("draft")):
            mism.append(f"{spec.parent.name}: status={status or 'ABSENT'} tasks={done}/{done}")
    index = (ROOT / "docs/INDEX.md").read_text(encoding="utf-8")
    rows = [ln for ln in index.splitlines() if ln.startswith("| [`")]
    return {
        "issue_refs_in_source": sum(refs.values()),
        "issue_refs_top": refs.most_common(6),
        "legacy_phrases": legacy,
        "spec_status_mismatches": mism,
        "index_bytes": len(index.encode()),
        "index_rows_over_1500": [ln.split("`")[1] for ln in rows if len(ln) > 1500],
        "claude_md_lines": sum(1 for _ in (ROOT / "CLAUDE.md").open(encoding="utf-8")),
    }


def prompts() -> dict:
    per: list[dict] = []
    total = 0
    literal_chars = 0
    missing_schema: list[str] = []
    missing_ground: list[str] = []
    no_fixtures: list[str] = []
    for md, caller in PROMPT_CALLERS.items():
        text = (ROOT / md).read_text(encoding="utf-8")
        toks = int(len(text.split()) * 1.3)
        total += toks
        schema_keys = set(re.findall(r'"([a-z_]+)"\s*:', text))
        code = (ROOT / caller).read_text(encoding="utf-8")
        tree = ast.parse(code)
        read_keys: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and re.fullmatch(r"[a-z_]+", a.value):
                    read_keys.add(a.value)
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                if re.fullmatch(r"[a-z_]+", node.slice.value):
                    read_keys.add(node.slice.value)
        lit = sum(len(n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) >= 400)
        literal_chars += lit
        # a key the parser reads that the template never mentions anywhere —
        # input-side keys (catalog rows, context) appear in the prose and drop out
        gap = sorted(k for k in read_keys - schema_keys - INPUT_KEYS.get(md, set()) if k not in text and re.search(r'\.get\("' + k + r'"', code))
        if schema_keys and gap:
            missing_schema.append(f"{md}: {', '.join(gap)}")
        grounded = "working directory" in text
        if md in GROUNDED_PROMPTS and not grounded:
            missing_ground.append(md)
        slug = Path(md).stem
        fxroot = ROOT / "tests/cognition/fixtures"
        fx = [f for d in fxroot.glob("*") if d.is_dir() and any(part in d.name or d.name in part for part in slug.split("-")) for f in d.glob("*")] if fxroot.exists() else []
        if not fx:
            no_fixtures.append(md)
        per.append({"prompt": md, "tokens": toks, "schema_keys": len(schema_keys), "python_literal_chars_in_caller": lit, "fixtures": len(fx)})
    return {
        "prompt_static_tokens_total": total,
        "prompt_python_literal_chars": literal_chars,
        "parser_keys_missing_from_schema": missing_schema,
        "prompts_missing_grounding": missing_ground,
        "prompts_without_eval_fixtures": no_fixtures,
        "prompts": per,
    }


def harness() -> dict:
    sc = (ROOT / "devclaw/engine/sandcastle.py").read_text(encoding="utf-8")
    want = ["--pids-limit", "--cap-drop", "--security-opt", "--read-only", "--memory", "--cpus"]
    present = {f: (f'"{f}"' in sc) for f in want}
    net = re.search(r'"--network",\s*\n?\s*"([a-z]+)"', sc)
    cfg = (ROOT / "devclaw/config.py").read_text(encoding="utf-8")
    envs = sorted(set(re.findall(r'"(DEVCLAW_[A-Z0-9_]+)"', cfg)))
    return {
        "sandbox_flags": present,
        "sandbox_network": net.group(1) if net else "unknown",
        "sandbox_hardening_missing": [f for f, ok in present.items() if not ok],
        "config_env_vars": len(envs),
    }


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except OSError:
        return "unknown"


def measure() -> dict:
    files = py_files()
    out = {"head": git_head(), "generated_by": "evals/measure_eng_health.py"}
    for section in (structure(files), duplication(files), noise(files), prompts(), harness()):
        out.update(section)
    return out


def scalar(v):  # counts for lists, ints as-is
    if isinstance(v, list):
        return len(v)
    if isinstance(v, (int, float)):
        return v
    return None


def compare(prev: dict, now: dict) -> list[tuple[str, object, object, str]]:
    rows = []
    for k, direction in WORSE.items():
        a, b = scalar(prev.get(k)), scalar(now.get(k))
        if a is None or b is None or a == b:
            continue
        if direction == "flat":
            verdict = "changed"
        else:
            verdict = "REGRESSED" if (b > a) == (direction == "up") else "improved"
        rows.append((k, a, b, verdict))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--compare", metavar="PREV_JSON", help="previous run to diff against")
    ap.add_argument("--out", metavar="JSON", help="also write the JSON here")
    args = ap.parse_args()
    now = measure()
    text = json.dumps(now, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.compare and os.path.exists(args.compare):
        prev = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        rows = compare(prev, now)
        print("\n# delta vs", args.compare, f"(head {prev.get('head')} -> {now['head']})", file=sys.stderr)
        if not rows:
            print("no metric moved", file=sys.stderr)
        for k, a, b, v in rows:
            print(f"{k:36} {a!s:>8} -> {b!s:<8} {v}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
