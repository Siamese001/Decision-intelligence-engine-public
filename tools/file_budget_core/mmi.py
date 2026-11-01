"""
Multi-Dimensional Modularity Index (MMI) AST Metric Analysis & Reporting.

Computes four-dimensional modularity metrics:
1. SLOC (Source Lines of Code)
2. Cognitive Complexity (Max function-level flow-break penalty)
3. Fan-out (Count of unique external non-stdlib imports)
4. Responsibility Count (Top-level classes and functions)
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set

STDLIB_MODULES: Set[str] = set(sys.stdlib_module_names)
DEFAULT_MAX_COGNITIVE_COMPLEXITY = 15
DEFAULT_MAX_EXTERNAL_IMPORTS = 12


class ModuleMetrics(NamedTuple):
    lines: int
    max_cc: int
    unique_external_imports: int
    external_import_names: List[str]
    has_branch_logic: bool
    responsibility_count: int


def count_lines(file_path: Path) -> int:
    """Count physical lines in file."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def calculate_ast_metrics_from_source(
    code: str,
    tree: Optional[ast.AST] = None,
    filename: str = "<memory>",
) -> ModuleMetrics:
    """Analyze Python source and optional parsed AST in memory to compute MMI dimensions."""
    lines = len(code.splitlines()) if code else 0
    if not code:
        return ModuleMetrics(
            lines=0,
            max_cc=0,
            unique_external_imports=0,
            external_import_names=[],
            has_branch_logic=False,
            responsibility_count=0,
        )

    parsed_tree = tree
    if parsed_tree is None:
        try:
            parsed_tree = ast.parse(code, filename=filename)
        except Exception:
            return ModuleMetrics(
                lines=lines,
                max_cc=0,
                unique_external_imports=0,
                external_import_names=[],
                has_branch_logic=False,
                responsibility_count=0,
            )

    from tools.shift_left_core.cognitive import (
        calculate_function_complexity,
        collect_function_nodes,
    )

    ext_imports: Set[str] = set()
    has_branch_logic = False
    max_cc = 0
    resp_count = 0

    body = getattr(parsed_tree, "body", [])
    for node in body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            resp_count += 1

    for node in ast.walk(parsed_tree):
        if isinstance(
            node,
            (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.Match, ast.ExceptHandler),
        ):
            has_branch_logic = True

        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                if root_pkg and root_pkg not in STDLIB_MODULES:
                    ext_imports.add(root_pkg)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                root_pkg = node.module.split(".")[0]
                if root_pkg and root_pkg not in STDLIB_MODULES:
                    ext_imports.add(root_pkg)

    for qual_name, func_node in collect_function_nodes(parsed_tree, skip_dunder=True):
        cc = calculate_function_complexity(func_node, qual_name)
        if cc > max_cc:
            max_cc = cc

    return ModuleMetrics(
        lines=lines,
        max_cc=max_cc,
        unique_external_imports=len(ext_imports),
        external_import_names=sorted(list(ext_imports)),
        has_branch_logic=has_branch_logic,
        responsibility_count=resp_count,
    )


def calculate_ast_metrics(file_path: Path) -> ModuleMetrics:
    """Analyze a Python file AST to compute MMI dimensions with a single file read."""
    if not file_path.is_file():
        return ModuleMetrics(
            lines=0,
            max_cc=0,
            unique_external_imports=0,
            external_import_names=[],
            has_branch_logic=False,
            responsibility_count=0,
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ModuleMetrics(
            lines=0,
            max_cc=0,
            unique_external_imports=0,
            external_import_names=[],
            has_branch_logic=False,
            responsibility_count=0,
        )

    return calculate_ast_metrics_from_source(content, filename=str(file_path))


def generate_mmi_snapshot(
    repo_root: Path,
    target_files: List[Path],
    output_path: Path,
    baseline_modules: Dict[str, int],
    cohesive_exemptions: Dict[str, Dict[str, Any]],
    get_tiered_max_lines_fn: Callable[[str, int], int],
    default_max_lines: int = 400,
    amber_zone_margin: int = 200,
) -> int:
    """Emits comprehensive Multi-Dimensional Modularity Index (MMI) snapshot."""
    modules_list = []
    status_counts = {
        "WITHIN_STANDARD_BUDGET": 0,
        "AMBER_ZONE_COMPLIANT": 0,
        "EXEMPTED_COHESIVE": 0,
        "RATCHET_BASELINE_REGISTERED": 0,
        "BUDGET_VIOLATION": 0,
    }

    for fp in sorted(target_files):
        try:
            rel = str(fp.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            rel = str(fp).replace("\\", "/")

        metrics = calculate_ast_metrics(fp)
        effective_max = get_tiered_max_lines_fn(rel, default_max_lines)

        if rel in cohesive_exemptions:
            status = "EXEMPTED_COHESIVE"
            classification = cohesive_exemptions[rel].get("classification", "cohesive_structural_module")
        elif metrics.lines <= effective_max:
            status = "WITHIN_STANDARD_BUDGET"
            classification = "standard"
        elif (
            metrics.lines <= effective_max + amber_zone_margin
            and metrics.max_cc <= DEFAULT_MAX_COGNITIVE_COMPLEXITY
            and metrics.unique_external_imports <= DEFAULT_MAX_EXTERNAL_IMPORTS
        ):
            status = "AMBER_ZONE_COMPLIANT"
            classification = "amber_compliant"
        elif rel in baseline_modules and metrics.lines <= baseline_modules[rel]:
            status = "RATCHET_BASELINE_REGISTERED"
            classification = "ratchet_grandfathered"
        else:
            status = "BUDGET_VIOLATION"
            classification = "unregistered_oversized"

        status_counts[status] += 1
        modules_list.append({
            "path": rel,
            "lines": metrics.lines,
            "max_cognitive_complexity": metrics.max_cc,
            "unique_external_imports": metrics.unique_external_imports,
            "external_import_names": metrics.external_import_names,
            "responsibility_count": metrics.responsibility_count,
            "status": status,
            "classification": classification,
        })

    snapshot = {
        "schema_version": "urn:nee:mmi:snapshot:1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "standard_max_lines": default_max_lines,
        "amber_zone_lines": default_max_lines + amber_zone_margin,
        "scanned_modules": len(target_files),
        "summary": status_counts,
        "modules": modules_list,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    print(f"✅ Generated MMI Snapshot report: {output_path}")
    print(f"   Total modules:          {len(target_files)}")
    print(f"   Within standard budget: {status_counts['WITHIN_STANDARD_BUDGET']}")
    print(f"   Amber zone compliant:   {status_counts['AMBER_ZONE_COMPLIANT']}")
    print(f"   Exempted cohesive:      {status_counts['EXEMPTED_COHESIVE']}")
    print(f"   Ratchet baseline:       {status_counts['RATCHET_BASELINE_REGISTERED']}")
    print(f"   Budget violations:      {status_counts['BUDGET_VIOLATION']}")
    return 0
