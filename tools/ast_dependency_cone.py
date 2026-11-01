#!/usr/bin/env python3
"""AST reverse-dependency blast-radius cone calculator and impacted test resolver."""

from __future__ import annotations

import argparse
import ast
from collections import deque
import json
from pathlib import Path
import re
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS: tuple[str, ...] = (
    "dual_pipeline",
    "unified_pipeline",
    "storage",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "config",
    "tools",
    "tests",
)

_FAST_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(\.+[a-zA-Z0-9_\.]*|[a-zA-Z0-9_\.]+)\s+import\s+(?:\(([^)]*)\)|([^\n#]+))|import\s+([^\n#]+))",
    re.MULTILINE,
)

_GRAPH_CACHE: Dict[Tuple[str, int], Tuple[Dict[str, Set[str]], Dict[str, str]]] = {}


def _rel_to_module(rel_path: str) -> str:
    norm = rel_path.replace("\\", "/").strip("/")
    if norm.endswith("/__init__.py"):
        return norm[: -len("/__init__.py")].replace("/", ".")
    if norm.endswith(".py"):
        return norm[:-3].replace("/", ".")
    return norm.replace("/", ".")


def _extract_imports_from_source(source: str, current_pkg: str) -> Set[str]:
    """Extracts imported module names from Python source via fast regex with relative import support."""
    imported: Set[str] = set()
    for match in _FAST_IMPORT_RE.finditer(source):
        frm, paren_names, inline_names, imp = match.groups()
        names = paren_names if paren_names is not None else inline_names
        if frm:
            base = frm
            if base.startswith(".") and current_pkg:
                level = len(base) - len(base.lstrip("."))
                rel_mod = base.lstrip(".")
                parts = current_pkg.split(".")
                keep = max(0, len(parts) - (level - 1))
                prefix = ".".join(parts[:keep])
                base = f"{prefix}.{rel_mod}" if rel_mod else prefix
            imported.add(base)
            for n in (names or "").split(","):
                tok = n.strip().split()[0] if n.strip() else ""
                if tok and tok != "*" and not tok.startswith("#"):
                    imported.add(f"{base}.{tok}")
        elif imp:
            for item in imp.split(","):
                tok = item.strip().split()[0] if item.strip() else ""
                if tok:
                    imported.add(tok)
    return imported


def _iter_repo_python_files(repo_root: Path) -> List[Tuple[str, Path]]:
    results: List[Tuple[str, Path]] = []
    for d in SCAN_DIRS:
        dp = repo_root / d
        if not dp.is_dir():
            continue
        for fp in dp.rglob("*.py"):
            try:
                rel = str(fp.relative_to(repo_root)).replace("\\", "/")
                results.append((rel, fp))
            except ValueError:
                continue
    for fp in repo_root.glob("*.py"):
        results.append((fp.name, fp))
    return results


def build_reverse_import_graph(repo_root: Optional[Path] = None) -> Tuple[Dict[str, Set[str]], Dict[str, str]]:
    """Returns (reverse_adj: {target_rel_path -> set of caller_rel_paths}, mod_to_rel: {module_name -> rel_path})."""
    root = (repo_root or ROOT).resolve()
    py_files = _iter_repo_python_files(root)
    cache_key = (str(root), len(py_files))
    if cache_key in _GRAPH_CACHE and root == ROOT.resolve():
        return _GRAPH_CACHE[cache_key]

    mod_to_rel: Dict[str, str] = {}
    for rel, _ in py_files:
        mod_to_rel[_rel_to_module(rel)] = rel

    reverse_adj: Dict[str, Set[str]] = {rel: set() for rel, _ in py_files}
    for rel, fp in py_files:
        try:
            src = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        pkg = _rel_to_module(str(Path(rel).parent)) if "/" in rel else ""
        for imp in _extract_imports_from_source(src, pkg):
            # Match exact module or submodule; skip package __init__.py fallback on unresolved symbols
            cand = imp
            while cand:
                target_rel = mod_to_rel.get(cand)
                if target_rel and target_rel != rel:
                    if cand != imp and target_rel.endswith("/__init__.py"):
                        break
                    reverse_adj.setdefault(target_rel, set()).add(rel)
                    break
                if "." not in cand:
                    break
                cand = cand.rsplit(".", 1)[0]

    if root == ROOT.resolve():
        _GRAPH_CACHE[cache_key] = (reverse_adj, mod_to_rel)
    return reverse_adj, mod_to_rel


MAX_TRANSITIVE_HUB_CALLERS = 50
MAX_SEED_HUB_CALLERS = 40


def compute_reverse_dependency_cone(
    changed_files: Iterable[str],
    repo_root: Optional[Path] = None,
    max_depth: int = 2,
) -> Set[str]:
    """Computes the reverse dependency cone of files that directly or 2-hop import any file in changed_files."""
    reverse_adj, _ = build_reverse_import_graph(repo_root)
    seeds = {f.replace("\\", "/").strip("/") for f in changed_files if f and f.strip()}
    visited: Set[str] = set(seeds)
    queue: deque[Tuple[str, int]] = deque(
        (s, 0 if len(reverse_adj.get(s, ())) <= MAX_SEED_HUB_CALLERS else max_depth - 1)
        for s in seeds
    )

    while queue:
        curr, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for caller in reverse_adj.get(curr, ()):
            if caller not in visited:
                visited.add(caller)
                if not caller.startswith("tests/") and len(reverse_adj.get(caller, ())) <= MAX_TRANSITIVE_HUB_CALLERS:
                    queue.append((caller, depth + 1))
    return visited


def resolve_impacted_tests(
    changed_files: Sequence[str],
    repo_root: Optional[Path] = None,
) -> List[str]:
    """Returns sorted dotted test module names (`tests.foo`) transitively importing any changed file."""
    cone = compute_reverse_dependency_cone(changed_files, repo_root)
    test_mods: Set[str] = set()
    for rel in cone:
        if rel.startswith("tests/") and rel.endswith(".py") and not rel.endswith("__init__.py"):
            name = Path(rel).name
            if name.startswith("test_") or name.endswith("_test.py"):
                test_mods.add(_rel_to_module(rel))
    return sorted(test_mods)


def check_import_cone_overlap(
    branch_files: Iterable[str],
    main_files: Iterable[str],
    repo_root: Optional[Path] = None,
) -> Tuple[bool, Set[str]]:
    """Checks whether the reverse dependency cone of branch_files intersects the cone of main_files."""
    b_py = [f for f in branch_files if f.endswith(".py")]
    m_py = [f for f in main_files if f.endswith(".py")]
    if not b_py or not m_py:
        return False, set()
    cone_b = compute_reverse_dependency_cone(b_py, repo_root)
    cone_m = compute_reverse_dependency_cone(m_py, repo_root)
    overlap = cone_b & cone_m
    return bool(overlap), overlap


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compute transitive AST reverse-dependency cone and impacted tests.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--changed", nargs="+", required=True, help="Changed file paths")
    args = parser.parse_args(argv)

    cone = sorted(compute_reverse_dependency_cone(args.changed, args.repo_root))
    tests = resolve_impacted_tests(args.changed, args.repo_root)
    print(
        json.dumps(
            {
                "changed": list(args.changed),
                "cone_count": len(cone),
                "impacted_tests_count": len(tests),
                "impacted_tests": tests,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
