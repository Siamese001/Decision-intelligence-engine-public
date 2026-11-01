#!/usr/bin/env python3
"""tools/run_testing_gaps_ast_scan.py

Deterministic AST complexity, git churn, fan-in, and test-coverage hotspot
ranker for testing_gaps_assurance.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROD_DIRS: tuple[str, ...] = (
    "dual_pipeline",
    "storage",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "tools",
)
GENERIC_STEMS: frozenset[str] = frozenset(
    {"utils", "models", "types", "config", "constants", "base", "helpers", "cli", "main"}
)
FIX_KEYWORDS: tuple[str, ...] = (
    "fix",
    "rca",
    "bug",
    "defect",
    "regression",
    "hotfix",
    "repair",
    "resolv",
)


def _discover_prod_files(repo_root: Path, prod_dirs: tuple[str, ...]) -> list[str]:
    prod_files: list[str] = []
    for dname in prod_dirs:
        target = repo_root / dname
        if not target.exists():
            continue
        for dirpath, _, filenames in os.walk(target):
            rel_dir = Path(dirpath).relative_to(repo_root)
            if any(p.startswith(".") or p == "__pycache__" for p in rel_dir.parts):
                continue
            for fname in sorted(filenames):
                if fname.endswith(".py") and fname != "__init__.py":
                    prod_files.append((rel_dir / fname).as_posix())
    return prod_files


def _collect_git_churn(repo_root: Path, git_commits: int) -> tuple[dict[str, int], dict[str, int]]:
    churn: dict[str, int] = defaultdict(int)
    fix_churn: dict[str, int] = defaultdict(int)
    try:
        out = subprocess.check_output(
            ["git", "log", "-n", str(git_commits), "--pretty=format:COMMIT:%H|%s", "--name-only"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
            errors="replace",
        )
    except Exception:
        return churn, fix_churn

    is_fix = False
    for line in out.splitlines():
        if line.startswith("COMMIT:"):
            msg = line.split("|", 1)[1].lower() if "|" in line else ""
            is_fix = any(k in msg for k in FIX_KEYWORDS)
        elif line.strip().endswith(".py"):
            rel = line.strip()
            churn[rel] += 1
            if is_fix:
                fix_churn[rel] += 1
    return churn, fix_churn


def _is_silent_swallow_handler(node: ast.ExceptHandler) -> bool:
    is_broad = node.type is None or (
        isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
    )
    if not is_broad:
        return False
    has_raise_or_log = any(
        isinstance(sub, ast.Raise)
        or (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in ("error", "warning", "exception", "critical")
        )
        for sub in ast.walk(node)
    )
    if has_raise_or_log:
        return False
    return any(
        isinstance(sub, (ast.Pass, ast.Return))
        for sub in node.body
    )


def _count_prod_node_metrics(
    tree: ast.AST, fp: str, mod_map: dict[str, str], fan_in: dict[str, int]
) -> dict[str, int]:
    br = exc = broad = silent_swallows = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.BoolOp, ast.IfExp)):
            br += 1
        elif isinstance(node, ast.ExceptHandler):
            exc += 1
            if node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
            ):
                broad += 1
            if _is_silent_swallow_handler(node):
                silent_swallows += 1
        elif isinstance(node, ast.ImportFrom) and node.module:
            for mod_name, target_fp in mod_map.items():
                if target_fp != fp and (
                    node.module == mod_name or node.module.startswith(mod_name + ".")
                ):
                    fan_in[target_fp] += 1
                    break
    return {"br": br, "exc": exc, "broad": broad, "silent_swallows": silent_swallows}


def _analyze_prod_ast(
    repo_root: Path, prod_files: list[str]
) -> tuple[dict[str, dict[str, int]], dict[str, int], dict[str, str]]:
    mod_map = {fp[:-3].replace("/", "."): fp for fp in prod_files}
    ast_metrics: dict[str, dict[str, int]] = {}
    fan_in: dict[str, int] = defaultdict(int)

    for fp in prod_files:
        try:
            src = (repo_root / fp).read_text(encoding="utf-8", errors="replace")
            loc = sum(1 for l in src.splitlines() if l.strip() and not l.strip().startswith("#"))
            tree = ast.parse(src)
        except Exception:
            continue
        counts = _count_prod_node_metrics(tree, fp, mod_map, fan_in)
        ast_metrics[fp] = {"loc": loc, **counts}
    return ast_metrics, fan_in, mod_map


def _detect_unused_decoy_modules(tree: ast.AST, mod_map: dict[str, str]) -> set[str]:
    imported_by_symbol: dict[str, str] = {}
    used_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in mod_map:
                    sym = alias.asname or alias.name.split(".")[0]
                    imported_by_symbol[sym] = mod_map[alias.name]
        elif isinstance(node, ast.ImportFrom) and node.module in mod_map:
            for alias in node.names:
                sym = alias.asname or alias.name
                imported_by_symbol[sym] = mod_map[node.module]
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            used_names.add(node.attr)
    return {
        tfp for sym, tfp in imported_by_symbol.items() if sym not in used_names
    }


def _record_consensus_pillar_hits(rel_tf: str, src: str, pillars: dict[str, int]) -> None:
    lower = src.lower()
    if any(k in lower for k in ("0.199", "0.201", "19.9", "20.1", "gate1_alr")) or (
        "sanitizer" in lower and "key" in lower
    ):
        pillars["privacy_approval_guards"] += 1
    if any(k in lower for k in ("multiprocessing", "processpoolexecutor")) or (
        "crash" in lower and any(m in lower for m in ("release_state", "audio_capture_strategy"))
    ):
        pillars["multiproc_crash_recovery"] += 1
    if "itemized_assertion" in lower or ("operationalerror" in lower and "assert" in lower):
        pillars["silent_data_loss_checks"] += 1
    if any(k in lower for k in ("query_engine", "executive_brief", "llm_judge")):
        pillars["search_judge_fallbacks"] += 1
    if any(k in lower for k in ("15ms", "budget_checkpoint", "request_context", "semantic_cache")):
        pillars["budget_speed_guards"] += 1
    if "anti_spoofing" in rel_tf:
        pillars["anti_spoofing_scoring"] += 1


def _collect_test_references(
    repo_root: Path, prod_files: list[str], mod_map: dict[str, str]
) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    test_refs: dict[str, dict[str, int]] = defaultdict(
        lambda: {"t": 0, "e2e": 0, "adv": 0, "prop": 0, "asserts": 0}
    )
    zero_assertion_files: list[str] = []
    partial_decoy_files: list[str] = []
    pillars: dict[str, int] = {
        "privacy_approval_guards": 0,
        "multiproc_crash_recovery": 0,
        "silent_data_loss_checks": 0,
        "search_judge_fallbacks": 0,
        "budget_speed_guards": 0,
        "anti_spoofing_scoring": 0,
    }
    fp_by_stem: dict[str, list[str]] = defaultdict(list)
    for fp in prod_files:
        stem = Path(fp).stem
        if stem not in GENERIC_STEMS:
            fp_by_stem[stem].append(fp)

    tests_dir = repo_root / "tests"
    if not tests_dir.exists():
        return test_refs, {
            "zero_assertion_test_files": zero_assertion_files,
            "partial_decoy_test_files": partial_decoy_files,
            "consensus_6_pillar_counts": pillars,
        }

    for dirpath, _, filenames in os.walk(tests_dir):
        for fname in sorted(filenames):
            if not (fname.startswith("test_") and fname.endswith(".py")):
                continue
            tf_path = Path(dirpath) / fname
            rel_tf = tf_path.relative_to(repo_root).as_posix()
            try:
                src = tf_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
            except Exception:
                continue
            assert_count = sum(
                1
                for n in ast.walk(tree)
                if isinstance(n, ast.Assert)
                or (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and (n.func.attr.startswith("assert") or n.func.attr == "raises")
                )
            )
            if assert_count == 0:
                zero_assertion_files.append(rel_tf)
                continue
            unused_decoys = _detect_unused_decoy_modules(tree, mod_map)
            if unused_decoys:
                partial_decoy_files.append(rel_tf)
            _record_consensus_pillar_hits(rel_tf, src, pillars)
            matched = (
                {tfp for m, tfp in mod_map.items() if m in src}
                | {
                    fps[0]
                    for st, fps in fp_by_stem.items()
                    if len(fps) == 1 and re.search(r"\b" + re.escape(st) + r"\b", src)
                }
            ) - unused_decoys
            for tfp in matched:
                entry = test_refs[tfp]
                entry["t"] += 1
                entry["asserts"] += assert_count
                if "e2e" in rel_tf or "end_to_end" in rel_tf:
                    entry["e2e"] += 1
                if "adversarial" in rel_tf or "edge_case" in rel_tf or "chaos" in rel_tf:
                    entry["adv"] += 1
                if "property" in rel_tf or "hypothesis" in src:
                    entry["prop"] += 1
    return test_refs, {
        "zero_assertion_test_files": zero_assertion_files,
        "partial_decoy_test_files": partial_decoy_files,
        "consensus_6_pillar_counts": pillars,
    }


def scan_testing_gap_hotspots(
    repo_root: Path = ROOT,
    top_k: int = 20,
    git_commits: int = 400,
    prod_dirs: tuple[str, ...] = PROD_DIRS,
) -> dict[str, Any]:
    """Computes testing-gap hotspot scores across production Python modules."""
    prod_files = _discover_prod_files(repo_root, prod_dirs)
    churn, fix_churn = _collect_git_churn(repo_root, git_commits)
    ast_metrics, fan_in, mod_map = _analyze_prod_ast(repo_root, prod_files)
    test_refs, audit_telemetry = _collect_test_references(repo_root, prod_files, mod_map)

    ranked: list[dict[str, Any]] = []
    for fp, metrics in ast_metrics.items():
        tr = test_refs[fp]
        c_term = 1.0 + churn[fp] + 2.5 * fix_churn[fp]
        k_term = (
            (metrics["br"] / 15.0)
            + (metrics["exc"] * 0.8)
            + (metrics["broad"] * 1.2)
            + (metrics.get("silent_swallows", 0) * 1.5)
            + (metrics["loc"] / 100.0)
        )
        b_mult = 1.0 + 0.10 * min(fan_in[fp], 40)
        d_cov = (
            (1.5 if tr["t"] == 0 else 1.0 / (1.0 + 0.25 * tr["t"]))
            + (0.35 if tr["e2e"] == 0 else 0)
            + (0.35 if tr["adv"] == 0 else 0)
            + (0.20 if tr["prop"] == 0 else 0)
        )
        ranked.append(
            {
                "file": fp,
                "score": round(c_term * k_term * b_mult * d_cov, 2),
                "churn": churn[fp],
                "fix": fix_churn[fp],
                **metrics,
                "fan_in": fan_in[fp],
                **tr,
            }
        )

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return {
        "total_modules": len(ranked),
        "untested_modules": sum(1 for x in ranked if x["t"] == 0),
        "silent_swallow_modules": sum(1 for x in ranked if x.get("silent_swallows", 0) > 0),
        **audit_telemetry,
        "top_20_hotspots": ranked[:top_k],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Testing gaps AST and git-churn hotspot scanner.")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root path.")
    parser.add_argument("--top-k", type=int, default=20, help="Number of top hotspots to emit.")
    parser.add_argument("--git-commits", type=int, default=400, help="Recent git commit window.")
    args = parser.parse_args(argv)

    result = scan_testing_gap_hotspots(args.repo_root, top_k=args.top_k, git_commits=args.git_commits)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
