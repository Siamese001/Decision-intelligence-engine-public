#!/usr/bin/env python3
"""tools/lint_graph_write_paths.py

Enforces Mandatory Physical Proof for Graph Mutations:
Direct Graph Write Path & Ungoverned Mutation Linter (Gate 32).

Invariants:
1. Prohibits direct SQL modifications (INSERT, UPDATE, DELETE, REPLACE) against
   knowledge graph database tables outside the GovernedGraphWriter or UniversalWriteGateway.
2. Strictly bans all direct database modification scripts in `scripts/`.
3. Ensures all graph mutations execute through governed writers that produce
   mandatory physical proof receipts.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]

# Canonical knowledge graph tables subject to mandatory governed write gating
GRAPH_TABLES: Set[str] = {
    "node",
    "assertion",
    "edge",
    "evidence_item",
    "claim",
    "graph_metadata",
    "node_fts",
    "claim_fts",
    "canonical_route",
    "node_card_map",
    "node_state",
    "canonical_alias",
    "write_control",
}

# Governed core writer components
GOVERNED_WRITER_MODULES: Sequence[str] = (
    "dual_pipeline/graph_writer_core/",
    "dual_pipeline/graph_writer.py",
    "dual_pipeline/uwg_core/",
    "dual_pipeline/graph_mutation_proof.py",
    "dual_pipeline/canonical_pipeline/checkpoint_memoizer.py",
    "storage/connection.py",
    "storage/database.py",
    "storage/schema/",
    "storage/migrations/",
    "dual_pipeline/migrations/",
    "tools/graph_mutation_proof_core/",
    "tools/prove_graph_mutation.py",
    "tools/lint_graph_write_paths.py",
)

# Governed maintenance and dedup tools
GOVERNED_MAINTENANCE_MODULES: Sequence[str] = (
    "tools/graph_gc.py",
    "tools/archive_stale_candidates.py",
    "tools/backfill_tri_vector_corpus.py",
    "tools/cards_refactor.py",
    "tools/reconcile_temporal_supersessions.py",
    "tools/remediate_entity_duplicates.py",
    "tools/remediate_future_facts.py",
    "tools/resync_fts_indexes.py",
    "tools/tier2_dedup_core/",
    "tools/scheduled_task_core/",
    "tools/stress_test_semantic_cache_l0.py",
    "tools/reconcile_antigravity_registrations.py",
    "tools/prepare_entity_attribution_patch.py",
    "tools/remediate_action_ledger_locators_and_owners.py",
    "tools/backfill_edge_assertion_mappings.py",
    "tools/apply_graph_hygiene_remediation.py",
    "tools/benchmark_bge_m3_production_graph.py",
    "tools/benchmark_bge_m3_ingest_replay.py",
    "evals/evaluate_full_mesh_a_to_d_live.py",
)

# Grandfathered legacy pipeline and closeout modules
GRANDFATHERED_LEGACY_MODULES: Sequence[str] = (
    "dual_pipeline/candidate_cleanup_core/executor.py",
    "dual_pipeline/card_refactor_release_core/apply.py",
    "dual_pipeline/graph_consolidation_core/apply.py",
    "dual_pipeline/graph_consolidation_core/repoint.py",
    "dual_pipeline/graph_freshness_core/agents/judge.py",
    "dual_pipeline/graph_maintenance_core/candidate_replay.py",
    "dual_pipeline/graph_maintenance_core/dedup.py",
    "dual_pipeline/graph_signal_core/mutex_invalidation.py",
    "dual_pipeline/project_status_reclassification.py",
    "dual_pipeline/release_core/helpers.py",
    "dual_pipeline/source_retirement_core/apply.py",
    "dual_pipeline/staleness_core/cascade_resolver.py",
    "dual_pipeline/staleness_core/ttl_sweeper.py",
    "dual_pipeline/transcript_pipeline_core/stakeholder_validator.py",
    "dual_pipeline/validation_closeout_core/apply.py",
    "dual_pipeline/vector_indexing/bge_m3_org_indexer.py",
)

APPROVED_WRITE_MODULE_PATTERNS: Sequence[str] = (
    *GOVERNED_WRITER_MODULES,
    *GOVERNED_MAINTENANCE_MODULES,
    *GRANDFATHERED_LEGACY_MODULES,
)

SQL_WRITE_RE = re.compile(
    r"\b(INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+([a-zA-Z0-9_]+)",
    re.IGNORECASE,
)


def is_approved_module(rel_path: str) -> bool:
    """Checks whether the file path belongs to an approved write module."""
    normalized = rel_path.replace("\\", "/")
    for pattern in APPROVED_WRITE_MODULE_PATTERNS:
        if pattern.endswith("/"):
            if normalized.startswith(pattern) or f"/{pattern}" in f"/{normalized}":
                return True
        else:
            if normalized == pattern or normalized.endswith(f"/{pattern}"):
                return True
    return False


def get_staged_python_files(repo_root: Path) -> List[Path]:
    """Retrieve staged Python files from git index."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        files = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.endswith(".py"):
                p = repo_root / line
                if p.is_file():
                    files.append(p)
        return files
    except Exception:
        return []


def discover_scanned_files(repo_root: Path) -> List[Path]:
    """Discovers all candidate Python files in scripts/, tools/, and dual_pipeline/."""
    candidates: List[Path] = []
    for folder in ("scripts", "tools", "dual_pipeline"):
        dir_path = repo_root / folder
        if dir_path.is_dir():
            candidates.extend(sorted(dir_path.rglob("*.py")))
    # Add top-level python scripts
    for top_py in sorted(repo_root.glob("*.py")):
        candidates.append(top_py)
    return candidates


def inspect_file_for_direct_graph_writes(path: Path, repo_root: Path) -> List[str]:
    """Inspects a Python file AST and string literals for ungoverned graph writes."""
    rel_path = str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")

    # Exclude approved modules and test directories
    if is_approved_module(rel_path):
        return []
    if rel_path.startswith("tests/") or "/tests/" in f"/{rel_path}":
        return []

    try:
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(path))
    except Exception as e:
        return [f"{rel_path}: Syntax or encoding error during parse: {e}"]

    violations: List[str] = []
    is_in_scripts = rel_path.startswith("scripts/")

    # 1. Inspect all string literals across AST for SQL write statements
    for node in ast.walk(tree):
        string_val = ""
        lineno = getattr(node, "lineno", 0)

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_val = node.value
        elif isinstance(node, ast.JoinedStr):
            # Format strings: concatenate string constant parts
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    parts.append(part.value)
            string_val = " ".join(parts)

        if not string_val:
            continue

        matches = SQL_WRITE_RE.findall(string_val)
        for op, table_name in matches:
            t_lower = table_name.lower()
            if is_in_scripts:
                # Any SQL write in scripts/ is strictly banned
                violations.append(
                    f"{rel_path}:{lineno}: Prohibited direct SQL write `{op} {table_name}` in scripts/. "
                    "Scripts must never execute direct database writes; route through governed CLI."
                )
            elif t_lower in GRAPH_TABLES:
                violations.append(
                    f"{rel_path}:{lineno}: Prohibited direct graph write `{op} {table_name}` outside governed writer. "
                    f"Table `{table_name}` is a canonical graph table. Route writes through GovernedGraphWriter.apply "
                    "or UniversalWriteGateway with mandatory physical proof receipts."
                )

    return violations


def lint_graph_write_paths(
    target_files: Optional[Sequence[Path]] = None,
    repo_root: Optional[Path] = None,
    verbose: bool = False,
) -> Tuple[int, List[str]]:
    """Runs the direct graph write path linter. Returns (exit_code, violations)."""
    root = (repo_root or ROOT).resolve()
    files_to_scan = list(target_files) if target_files is not None else discover_scanned_files(root)

    all_violations: List[str] = []
    scanned_count = 0

    for p in files_to_scan:
        if not p.is_file() or not p.name.endswith(".py"):
            continue
        scanned_count += 1
        violations = inspect_file_for_direct_graph_writes(p, root)
        if violations:
            all_violations.extend(violations)

    if verbose:
        print(f"Scanned {scanned_count} Python files for direct graph writes.")

    return (1 if all_violations else 0), all_violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate 32: Direct Graph Write Path & Ungoverned Mutation Linter."
    )
    parser.add_argument("--repo-root", default=str(ROOT), help="Repository root path.")
    parser.add_argument("--check", action="store_true", help="Run check and exit non-zero on violations.")
    parser.add_argument("--staged", action="store_true", help="Only scan staged Python files.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output.")
    parser.add_argument("files", nargs="*", help="Optional specific files to scan.")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()

    if args.files:
        targets = [Path(f).resolve() for f in args.files]
    elif args.staged:
        targets = get_staged_python_files(root)
    else:
        targets = discover_scanned_files(root)

    exit_code, violations = lint_graph_write_paths(targets, repo_root=root, verbose=args.verbose)

    if exit_code != 0:
        print("=" * 80, file=sys.stderr)
        print("❌ DIRECT GRAPH WRITE PATH LINTER FAILED (GATE 32)", file=sys.stderr)
        print(f"Found {len(violations)} direct graph write violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        print(
            "Remediation: Direct SQL mutations on graph tables are strictly barred. "
            "Route mutations through GovernedGraphWriter or UniversalWriteGateway "
            "to guarantee physical proof receipts.",
            file=sys.stderr,
        )
        return 1

    print(f"✅ PASSED: Direct Graph Write Path Linter (Gate 32): 0 violations across {len(targets)} scanned files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
