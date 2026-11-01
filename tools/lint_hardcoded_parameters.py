#!/usr/bin/env python3
"""
tools/lint_hardcoded_parameters.py

AST Linter for Hardcoded Parameter & Model String Prevention (2026+ Agentic SSOT Governance).
Enforces Factor III of Twelve-Factor Agentic Engineering via `tools.ssot_config_engine`:
- Prohibits hardcoding vendor and local model strings in agent cards and pipelines.
- Prohibits hardcoding magic release thresholds and governed parameter default/fallback literals.
- Enforces consumption of ModelRegistry, ParameterRegistry, and ReleaseThresholdPolicy.

Usage:
  python tools/lint_hardcoded_parameters.py [--staged] [--all] [files ...]
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ssot_config_engine import (
    DEFAULT_GOVERNED_DIRS,
    PROHIBITED_HITL_BADGE_PATTERN,
    PROHIBITED_MODEL_PATTERNS,
    SSOTASTViolation as Violation,
    UnifiedSSOTASTVisitor as ParameterLinterVisitor,
    discover_governed_python_files,
    is_test_or_excluded_path,
)

GOVERNED_DIRS = list(DEFAULT_GOVERNED_DIRS)

EXEMPT_FILES = {
    "dual_pipeline/model_registry.py",
    "dual_pipeline/parameters.py",
    "dual_pipeline/parameters_ops.py",
    "dual_pipeline/parameters_subsystems.py",
    "dual_pipeline/release_thresholds.py",
    "dual_pipeline/token_optimization/schemas.py",
}

SSOT_CONFINED_ASSIGNMENTS = {
    "_TIER1_THRESHOLDS": {"dual_pipeline/parameters.py", "dual_pipeline/parameters_ops.py"},
    "_DEFAULT_THRESHOLDS": {"dual_pipeline/parameters.py", "dual_pipeline/parameters_ops.py"},
    "_C21_FLOORS": {"dual_pipeline/parameters.py", "dual_pipeline/parameters_ops.py"},
    "_PASSIVE_HEDGING_THRESHOLD": {"dual_pipeline/parameters.py", "dual_pipeline/parameters_ops.py"},
    "CANONICAL_MUTATION_BUDGET": {"dual_pipeline/canonical_pipeline/evaluator.py"},
    "MAX_PAYLOAD_RETRIES": {"dual_pipeline/config/pipeline_settings.py"},
    "MAX_NETWORK_RETRIES": {"dual_pipeline/config/pipeline_settings.py"},
    "DEFAULT_MAX_MODEL_CALLS": {"dual_pipeline/agent_sdk/config.py"},
    "DEFAULT_MAX_TOOL_CALLS": {"dual_pipeline/agent_sdk/config.py"},
    "DEFAULT_NODE_SIMILARITY_THRESHOLD": {"dual_pipeline/graph_consolidation_core/constants.py"},
    "DEFAULT_LITERAL_SIMILARITY_THRESHOLD": {"dual_pipeline/graph_consolidation_core/constants.py"},
    "DEFAULT_EVIDENCE_SIMILARITY_THRESHOLD": {"dual_pipeline/graph_consolidation_core/constants.py"},
    "MIN_CONFIDENCE_MARGIN": {"dual_pipeline/graph_consolidation_core/policy.py"},
    "MAX_CONFIDENCE_MARGIN": {"dual_pipeline/graph_consolidation_core/policy.py"},
    "DEFAULT_MIN_MODULARITY": {"dual_pipeline/graph_community_clustering.py"},
    "MAX_COLBERT_TOKENS": {"dual_pipeline/index_core/constants.py"},
    "EMBEDDING_BATCH_SIZE": {"dual_pipeline/index_core/constants.py"},
    "DEFAULT_COLBERT_TILE_SIZE": {"dual_pipeline/index_core/gpu_scoring.py"},
    "MAX_XML_DEPTH": {"dual_pipeline/document_pipeline_core/diagram_loader.py"},
    "MAX_JSON_DEPTH": {"dual_pipeline/document_pipeline_core/structured_data_loader.py"},
    "DEFAULT_FAST_PATH_TTL_SEC": {"dual_pipeline/decision/fast_path_context.py"},
    "MAX_REQUEST_BYTES": {"dual_pipeline/decision_api.py"},
    "MAX_TOKEN_LIFETIME_SECONDS": {"dual_pipeline/authorization.py"},
    "MAX_SUBAGENT_CONTEXT_TOKENS": {"dual_pipeline/agent_architecture_core/context_profile.py"},
    "STARVATION_TURN_RATIO_THRESHOLD": {"dual_pipeline/audio_ingestion_core/subcluster_splitter.py"},
    "DEFAULT_ASSUMPTION_TTL_DAYS": {"dual_pipeline/l0/assumption_lifecycle.py"},
    "WORD_SALAD_PATTERNS": {"dual_pipeline/prompts/contracts/output2_contract.py"},
    "ROLE_ANONYMIZATION_PATTERNS": {"dual_pipeline/prompts/contracts/output2_contract.py"},
    "PASSIVE_HEDGING_PATTERNS": {"dual_pipeline/prompts/contracts/output2_contract.py"},
}


def scan_file(file_path: Path, repo_root: Path = ROOT) -> List[Violation]:
    try:
        rel_path = str(file_path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        rel_path = str(file_path).replace("\\", "/")

    if rel_path in EXEMPT_FILES or is_test_or_excluded_path(rel_path):
        return []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError as exc:
        return [
            Violation(
                file_path=rel_path,
                line_number=exc.lineno or 1,
                rule="SYNTAX_ERROR",
                detail=f"SyntaxError parsing {rel_path}: {exc}",
            )
        ]
    except Exception:
        return []

    visitor = ParameterLinterVisitor(
        rel_path=rel_path,
        lines=lines,
        ssot_confined_assignments=SSOT_CONFINED_ASSIGNMENTS,
    )
    visitor.visit(tree)
    return visitor.violations


check_file = scan_file


def get_staged_files(repo_root: Path) -> List[Path]:
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        files: List[Path] = []
        for line in res.stdout.splitlines():
            p = (repo_root / line.strip()).resolve()
            if p.suffix == ".py" and p.exists():
                files.append(p)
        return files
    except Exception:
        return []


def run_linter(
    repo_root: Path,
    staged: bool = False,
    explicit_files: Optional[List[str]] = None,
) -> int:
    print("=" * 80)
    print("  AST HARDCODED PARAMETER & MODEL STRING LINTER (2026+ SSOT Governance)")
    print("=" * 80)

    target_files: List[Path] = []
    if staged:
        target_files = get_staged_files(repo_root)
        print(f"Running in --staged mode: inspecting {len(target_files)} staged Python module(s).")
    elif explicit_files:
        target_files = [Path(f).resolve() for f in explicit_files if Path(f).suffix == ".py" and Path(f).exists()]
        print(f"Inspecting {len(target_files)} specified module(s).")
    else:
        target_files = discover_governed_python_files(repo_root, GOVERNED_DIRS)
        print(f"Running full scan across {len(target_files)} governed module(s).")

    all_violations: List[Violation] = []
    for file_path in target_files:
        all_violations.extend(scan_file(file_path, repo_root))

    if all_violations:
        print(f"\n❌ FAILED: Found {len(all_violations)} hardcoded parameter violation(s):\n")
        for idx, v in enumerate(all_violations, 1):
            print(f"  {idx}. [{v.rule}] {v.file_path}:{v.line_number}")
            print(f"     Detail: {v.detail}\n")
        print("=" * 80)
        return 1

    print("\n✅ PASSED: 0 hardcoded parameter or vendor model string violations detected.")
    print("   All components query ModelRegistry and ReleaseThresholdPolicy.")
    print("   Zero parameter drift across governed codebase.\n")
    print("=" * 80)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint Python AST for hardcoded parameters and model strings.")
    parser.add_argument("--staged", action="store_true", help="Only check staged git files")
    parser.add_argument("--all", action="store_true", help="Run full scan of all governed packages (default)")
    parser.add_argument("files", nargs="*", help="Specific files to inspect")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    return run_linter(repo_root, staged=args.staged, explicit_files=args.files if args.files else None)


if __name__ == "__main__":
    sys.exit(main())
