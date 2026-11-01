"""
Core category implementations for full structural pre-commit / pre-merge validation.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]


def run_command(cmd: List[str]) -> Tuple[bool, str, float]:
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=120.0)
        dur = round(time.time() - start, 3)
        out = proc.stdout if proc.returncode == 0 else (proc.stdout + "\n" + proc.stderr).strip()
        return (proc.returncode == 0, out, dur)
    except Exception as e:
        return (False, str(e), round(time.time() - start, 3))


def run_architectural_governance(py: str, staged_args: List[str], file_args: List[str]) -> Tuple[bool, str, float]:
    start = time.time()
    sub_checks = [
        ("Module File Budgets", [py, "-B", "tools/lint_file_budgets.py"] + (staged_args or file_args)),
        ("Architectural Layer Imports", [py, "-B", "tools/lint_layer_imports.py"] + (staged_args or file_args)),
        ("Model-Neutral Naming", [py, "-B", "tools/lint_model_neutral_naming.py"] + (staged_args or file_args)),
        ("Retired Model Deprecation", [py, "-B", "tools/lint_retired_models.py", "--check"]),
        ("Mock Boundaries", [py, "-B", "tools/lint_mock_boundaries.py"] + (staged_args or file_args)),
        ("Epistemic Boundaries", [py, "-B", "tools/lint_epistemic_boundaries.py"]),
        ("AST Invariants", [py, "-B", "tools/lint_ast_invariants.py", "--check"] + (staged_args or file_args)),
        ("Blast Radius", [py, "-B", "tools/lint_blast_radius.py", "--check"] + (staged_args or file_args)),
    ]
    outputs: List[str] = []
    for name, cmd in sub_checks:
        ok, out, _ = run_command(cmd)
        if not ok:
            return False, f"[{name} FAILED]\n{out}", round(time.time() - start, 3)
        outputs.append(f"{name}: PASS")
    return True, " | ".join(outputs), round(time.time() - start, 3)


def run_production_purity(py: str, staged_args: List[str], file_args: List[str]) -> Tuple[bool, str, float]:
    start = time.time()
    sub_checks = [
        ("Production Directory Purity", [py, "-B", "tools/lint_production_purity.py"] + (staged_args or file_args)),
        ("Anti-Custom Ingest Scripts", [py, "-B", "tools/lint_no_custom_ingest_scripts.py"] + (staged_args or file_args)),
        ("Anti-Transcript Overfitting", [py, "-B", "tools/lint_no_transcript_overfitting.py"] + (staged_args or file_args)),
        ("Anti-Knowledge Popping", [py, "-B", "tools/lint_no_knowledge_popping.py"] + (staged_args or file_args)),
        ("Plain-English Recap", [py, "-B", "tools/lint_plain_english_executive_recap.py"] + (staged_args or file_args)),
        ("Test Assertion Purity", [py, "-B", "tools/lint_test_assertion_purity.py"] + (staged_args or file_args)),
    ]
    outputs: List[str] = []
    for name, cmd in sub_checks:
        ok, out, _ = run_command(cmd)
        if not ok:
            return False, f"[{name} FAILED]\n{out}", round(time.time() - start, 3)
        outputs.append(f"{name}: PASS")
    return True, " | ".join(outputs), round(time.time() - start, 3)


def run_code_quality(py: str, staged_args: List[str], file_args: List[str], target_files: Sequence[str]) -> Tuple[bool, str, float]:
    start = time.time()
    cog_cmd = [py, "-B", "tools/lint_cognitive_complexity.py", "--check", "--ratchet"] + (list(target_files) if target_files else (staged_args or file_args))
    strict_type_cmd = [py, "-B", "tools/run_strict_type_gate.py", "--check"] + (staged_args or file_args)
    hardcoded_cmd = [py, "-B", "tools/lint_hardcoded_parameters.py"] + (staged_args or file_args)
    param_ref_cmd = [py, "-B", "tools/lint_parameter_references.py"] + (staged_args or file_args)

    sub_checks = [
        ("Cognitive Complexity", cog_cmd),
        ("Strict Type Annotations", strict_type_cmd),
        ("Hardcoded Parameters", hardcoded_cmd),
        ("Parameter References", param_ref_cmd),
    ]
    outputs: List[str] = []
    for name, cmd in sub_checks:
        ok, out, _ = run_command(cmd)
        if not ok:
            return False, f"[{name} FAILED]\n{out}", round(time.time() - start, 3)
        outputs.append(f"{name}: PASS")
    return True, " | ".join(outputs), round(time.time() - start, 3)


def run_contract_validation(py: str, staged: bool = True) -> Tuple[bool, str, float]:
    start = time.time()
    sub_checks = [
        ("Dual Pipeline Contracts", [py, "-B", "tools/validate_dual_pipeline_contracts.py"]),
        ("Implementation Plan Schema", [py, "-B", "tools/validate_implementation_plan.py"] + (["--staged"] if staged else [])),
        ("Output Hierarchy Gate", [py, "-B", "tools/validate_output_hierarchy.py", "--check"] + (["--staged"] if staged else [])),
        ("Worktree & Filesystem Hygiene", [py, "-B", "tools/audit_worktree_hygiene.py", "--check"]),
    ]
    outputs: List[str] = []
    for name, cmd in sub_checks:
        ok, out, _ = run_command(cmd)
        if not ok:
            return False, f"[{name} FAILED]\n{out}", round(time.time() - start, 3)
        outputs.append(f"{name}: PASS")
    return True, " | ".join(outputs), round(time.time() - start, 3)


def run_system_budgets(py: str) -> Tuple[bool, str, float]:
    start = time.time()
    sub_checks = [
        ("MCP Schema Overhead", [py, "-B", "tools/audit_mcp_schema_overhead.py", "--check"]),
        ("Skills Budget Gate", [py, "-B", "tools/audit_skills_budget.py", "--check"]),
        ("Rules Budget & Deduplication Gate", [py, "-B", "tools/audit_rules_budget.py", "--check"]),
    ]
    outputs: List[str] = []
    for name, cmd in sub_checks:
        ok, out, _ = run_command(cmd)
        if not ok:
            return False, f"[{name} FAILED]\n{out}", round(time.time() - start, 3)
        outputs.append(f"{name}: PASS")
    return True, " | ".join(outputs), round(time.time() - start, 3)


def run_security_and_runtime(py: str, staged_args: List[str], file_args: List[str]) -> Tuple[bool, str, float]:
    start = time.time()
    sub_checks = [
        ("Credential & Career Firewall", [py, "-B", "tools/scan_credential_leaks.py"] + (staged_args or file_args)),
        ("No Synthetic Embeddings", [py, "-B", "tools/validate_no_synthetic_embeddings.py", "--repository-root", str(ROOT)]),
        ("Runtime Evidence Controls", [py, "-B", "-m", "dual_pipeline.runtime_evidence", "enforce-controls", "--repository-root", str(ROOT)]),
    ]
    outputs: List[str] = []
    for name, cmd in sub_checks:
        ok, out, _ = run_command(cmd)
        if not ok:
            return False, f"[{name} FAILED]\n{out}", round(time.time() - start, 3)
        outputs.append(f"{name}: PASS")
    return True, " | ".join(outputs), round(time.time() - start, 3)
