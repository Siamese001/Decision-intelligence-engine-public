#!/usr/bin/env python3
"""Mechanical Scanner and Linter for SSOT Configuration & Release Threshold Violations.

Audits:
1. Release threshold alignment between config/runtime/parameters.json and dual_pipeline/release_thresholds.py.
2. Evaluator verdict rules: Output 2 and Output 3 strict PASS (>=90), no PASS_ADVISORY below 90.
3. Authoritative write authorization fail-closed invariants: PASS_ADVISORY must never grant write authorization.
4. AST / Code scan for hardcoded parameters (7500ms latency, 0.25 margin, obsolete 95 thresholds).
5. Dynamic validation of RCA_01 through RCA_08 SSOT invariant checks.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def check_parameter_file_parity(repo_root: Optional[Path] = None) -> List[str]:
    """Verify parameters.json has canonical thresholds."""
    from tools.audit_governance_harmonization_core.common import load_json_with_dup_check

    violations: List[str] = []
    root = repo_root or REPO_ROOT
    param_path = root / "config" / "runtime" / "parameters.json"
    if not param_path.exists():
        return [f"Missing parameter file: {param_path}"]

    try:
        data, _ = load_json_with_dup_check(param_path)
    except Exception as exc:
        return [f"Failed parsing parameters.json: {exc}"]

    params = data.get("parameters", {})
    rt = params.get("release_thresholds", {})
    if rt.get("output1_production_pass_threshold") != 90:
        violations.append(
            f"parameters.json: output1_production_pass_threshold is {rt.get('output1_production_pass_threshold')}, expected 90"
        )
    if rt.get("output2_production_pass_threshold") != 90:
        violations.append(
            f"parameters.json: output2_production_pass_threshold is {rt.get('output2_production_pass_threshold')}, expected 90"
        )
    if rt.get("output3_production_pass_threshold") != 90:
        violations.append(
            f"parameters.json: output3_production_pass_threshold is {rt.get('output3_production_pass_threshold')}, expected 90"
        )
    for required_domain in (
        "output1", "output2", "output3", "judge", "copilot",
        "audio_ingestion", "grounding", "pipeline_settings", "agent_sdk", "local_mlx_models",
        "graph_consolidation", "graph_retrieval", "graph_signal", "index_core",
        "document_security", "decision_service", "authorization", "agent_architecture", "l0",
        "engineering_output", "planning",
    ):
        if required_domain not in params or not isinstance(params.get(required_domain), dict):
            violations.append(f"parameters.json: missing required centralized domain '{required_domain}'")
    eng = params.get("engineering_output", {})
    for k, v in (("max_words", 300), ("target_words", 120), ("max_chars", 3500), ("progress_max_words", 20)):
        if eng.get(k) != v:
            violations.append(f"parameters.json: engineering_output.{k} is {eng.get(k)}, expected {v}")
    plan = params.get("planning", {})
    if plan.get("max_plan_words") != 1000:
        violations.append(f"parameters.json: planning.max_plan_words is {plan.get('max_plan_words')}, expected 1000")
    o2 = params.get("output2", {})
    for subkey in ("word_limits", "section_density_floors", "c21_speaker_tier_floors", "passive_hedging_threshold"):
        if subkey not in o2:
            violations.append(f"parameters.json: output2 missing required SSOT key '{subkey}'")
    from dual_pipeline.config_hasher import compute_active_config_snapshot
    snap = compute_active_config_snapshot()
    for dkey in ("parameters_digest", "release_policy_digest", "model_registry_digest", "asr_decode_digest"):
        if not snap.get(dkey):
            violations.append(f"config_hasher.py: missing required snapshot digest '{dkey}'")
    return violations


def check_release_policy_runtime() -> List[str]:
    """Verify CANONICAL_RELEASE_POLICY behavior at runtime."""
    violations: List[str] = []
    try:
        from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY, ReleaseVerdict
    except Exception as exc:
        return [f"Failed importing CANONICAL_RELEASE_POLICY: {exc}"]

    if CANONICAL_RELEASE_POLICY.output1_pass_threshold != 90:
        violations.append(
            f"CANONICAL_RELEASE_POLICY: output1_pass_threshold is {CANONICAL_RELEASE_POLICY.output1_pass_threshold}, expected 90"
        )
    if CANONICAL_RELEASE_POLICY.output2_pass_threshold != 90:
        violations.append(
            f"CANONICAL_RELEASE_POLICY: output2_pass_threshold is {CANONICAL_RELEASE_POLICY.output2_pass_threshold}, expected 90"
        )
    if CANONICAL_RELEASE_POLICY.output3_pass_threshold != 90:
        violations.append(
            f"CANONICAL_RELEASE_POLICY: output3_pass_threshold is {CANONICAL_RELEASE_POLICY.output3_pass_threshold}, expected 90"
        )

    # Output 3
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 90) != ReleaseVerdict.PASS:
        violations.append("evaluate_output_verdict(3, 90) must be PASS")
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 89) != ReleaseVerdict.FAIL:
        violations.append("evaluate_output_verdict(3, 89) must be FAIL (no advisory permitted for Output 3)")
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 86) != ReleaseVerdict.FAIL:
        violations.append("evaluate_output_verdict(3, 86) must be FAIL")

    # Output 2
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 90) != ReleaseVerdict.PASS:
        violations.append("evaluate_output_verdict(2, 90) must be PASS")
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 89) != ReleaseVerdict.FAIL:
        violations.append("evaluate_output_verdict(2, 89) must be FAIL (no advisory permitted for Output 2)")

    # Output 1
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 90) != ReleaseVerdict.PASS:
        violations.append("evaluate_output_verdict(1, 90) must be PASS")
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 89) != ReleaseVerdict.PASS_ADVISORY:
        violations.append("evaluate_output_verdict(1, 89) must be PASS_ADVISORY")
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 85) != ReleaseVerdict.PASS_ADVISORY:
        violations.append("evaluate_output_verdict(1, 85) must be PASS_ADVISORY")
    if CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 79) != ReleaseVerdict.FAIL:
        violations.append("evaluate_output_verdict(1, 79) must be FAIL")

    return violations


def _validate_tier_spec(tier_name: str, spec: Dict[str, Any]) -> List[str]:
    from dual_pipeline.model_registry import get_approved_models

    violations: List[str] = []
    provider = spec.get("provider")
    if provider == "openai":
        approved = get_approved_models("openai")
    elif provider == "google":
        approved = get_approved_models("google")
    else:
        return violations

    def_m = spec.get("default_model") or spec.get("model_id")
    if def_m not in approved:
        violations.append(f"Tier {tier_name} default_model '{def_m}' not in approved {provider} models {approved}")
    for fb in spec.get("allowed_fallbacks", []):
        if fb not in approved:
            violations.append(f"Tier {tier_name} fallback '{fb}' not in approved {provider} models {approved}")
    return violations


def check_model_registry_approved_models_parity(repo_root: Optional[Path] = None) -> List[str]:
    """Verify config/runtime/model_registry.json contains only approved models."""
    from tools.audit_governance_harmonization_core.common import load_json_with_dup_check

    violations: List[str] = []
    root = repo_root or REPO_ROOT
    reg_path = root / "config" / "runtime" / "model_registry.json"
    if not reg_path.exists():
        return [f"Missing model registry file: {reg_path}"]

    try:
        data, _ = load_json_with_dup_check(reg_path)
    except Exception as exc:
        return [f"Failed parsing model_registry.json: {exc}"]

    from dual_pipeline.model_registry import get_approved_models

    approved_section = data.get("approved_models", {})
    openai_approved = set(approved_section.get("openai", []))
    reg_openai = get_approved_models("openai")
    if openai_approved != reg_openai:
        violations.append(
            f"model_registry.json approved_models.openai {openai_approved} does not match registry approved models {reg_openai}"
        )

    google_approved = set(approved_section.get("google", []))
    reg_google = get_approved_models("google")
    if google_approved != reg_google:
        violations.append(
            f"model_registry.json approved_models.google {google_approved} does not match registry approved models {reg_google}"
        )

    tiers = data.get("model_tiers") or data.get("tiers") or {}
    for tier_name, spec in tiers.items():
        violations.extend(_validate_tier_spec(tier_name, spec))

    return violations


def _find_target_python_files(files: Optional[Sequence[str]]) -> List[Path]:
    if files is not None:
        target_paths = []
        for f in files:
            p = REPO_ROOT / f if not Path(f).is_absolute() else Path(f)
            if p.is_file() and p.suffix == ".py" and p.resolve() != Path(__file__).resolve():
                target_paths.append(p)
        return target_paths

    target_paths = []
    for root, _dirs, fs in os.walk(REPO_ROOT / "dual_pipeline"):
        if any(skip in root for skip in ("__pycache__", ".pytest_cache")):
            continue
        for fname in fs:
            if fname.endswith(".py"):
                target_paths.append(Path(root) / fname)
    return target_paths


def _scan_single_file_for_violations(path: Path, patterns: Sequence[Tuple[re.Pattern, str]]) -> List[str]:
    from tools.audit_governance_harmonization_core.common import read_text_safe

    violations: List[str] = []
    content = read_text_safe(path)
    if not content:
        return violations

    try:
        rel_path = path.resolve().relative_to(REPO_ROOT.resolve())
        if str(rel_path).replace("\\", "/").startswith("tests/"):
            return violations
    except ValueError:
        rel_path = path

    for pat, desc in patterns:
        match = pat.search(content)
        if match:
            violations.append(f"{rel_path}: {desc} (match: '{match.group(0)}')")
    return violations


def scan_source_code_violations(files: Optional[Sequence[str]] = None) -> List[str]:
    """Scan source code for obsolete patterns, bypasses, and hardcoded literals."""
    violations: List[str] = []
    prohibited_patterns: List[Tuple[re.Pattern, str]] = [
        (
            re.compile(r"write_authorized\s*=\s*bool\(.*or.*status.*in.*PASS_ADVISORY"),
            "Authoritative write authorization bypass on PASS_ADVISORY",
        ),
        (
            re.compile(r"max_latency_ms[\"']?\s*>\s*7500(?!\.)"),
            "Hardcoded 7500ms latency comparison (must consume ParameterRegistry timeouts.retrieval_steady_state_latency_ms)",
        ),
        (
            re.compile(r"max_query_latency_ms\s*>\s*7500\.0"),
            "Hardcoded 7500.0ms latency comparison (must consume ParameterRegistry)",
        ),
        (
            re.compile(r"target_score" + r":\s*int\s*=\s*95"),
            "Obsolete hardcoded target_score: int = 95 in MonotonicGuard (must consume CANONICAL_RELEASE_POLICY)",
        ),
        (
            re.compile(r"^(?:MAX_SPEAKER_SUMMARY_WORDS|MAX_SUPPORTING_DETAILS_WORDS|MAX_TOTAL_CONTENT_WORDS)\s*=\s*\d+", re.MULTILINE),
            "Hardcoded copilot speech word-count literal (must consume dual_pipeline.parameters.get_copilot_speech_limits())",
        ),
        (
            re.compile(r"^(?:DEFAULT_MIN_WORDS_NARRATIVE|DEFAULT_MIN_WORDS_PERSPECTIVES)\s*=\s*\d+", re.MULTILINE),
            "Hardcoded modular DAG word floor literal (must consume dual_pipeline.parameters SSOT)",
        ),
    ]

    target_paths = _find_target_python_files(files)
    for path in target_paths:
        violations.extend(_scan_single_file_for_violations(path, prohibited_patterns))

    return violations


from tools.scan_ssot_configuration_violations_core.rca_checks import check_rca_ssot_violations


def _resolve_scoped_governance_files(
    discovered: List[Path],
    target_files: Optional[Sequence[str]],
) -> List[Path]:
    if not target_files:
        return discovered
    root_parity_names = {
        "parameters.json",
        "policy_registry.yaml",
        "pyproject.toml",
        "AGENTS.md",
        "scan_ssot_configuration_violations.py",
    }
    resolved_targets: set[Path] = set()
    for f in target_files:
        p = (REPO_ROOT / f if not Path(f).is_absolute() else Path(f)).resolve()
        if p.name in root_parity_names or "audit_governance_harmonization" in str(p):
            return discovered
        resolved_targets.add(p)
    discovered_set = {p.resolve(): p for p in discovered}
    return [discovered_set[p] for p in resolved_targets if p in discovered_set]


def check_governance_harmonization_parity(
    files: Optional[Sequence[str]] = None,
) -> List[str]:
    """Run cross-surface governance harmonization scanners and fail on CRITICAL or WARNING drift."""
    violations: List[str] = []
    try:
        from datetime import datetime, timezone
        from tools.audit_governance_harmonization_core import (
            HarmonizationReport,
            discover_governance_files,
            scan_deprecated_tool_names,
            scan_duplication,
            scan_literal_margins,
            scan_policy_registry_sync,
            scan_replaced_decision_terms,
            scan_taxonomy_alignment,
            scan_threshold_drift,
            scan_unenumerated_receipts,
        )

        all_discovered = discover_governance_files()
        target_gov_files = _resolve_scoped_governance_files(all_discovered, files)
        if not target_gov_files:
            return []

        report = HarmonizationReport(
            scan_timestamp=datetime.now(timezone.utc).isoformat(),
            repo_root=str(REPO_ROOT),
            total_files_scanned=len(target_gov_files),
        )
        scan_threshold_drift(target_gov_files, report)
        scan_duplication(target_gov_files, report, include_pairwise_rules=False)
        for scanner in (
            scan_taxonomy_alignment,
            scan_unenumerated_receipts,
            scan_deprecated_tool_names,
            scan_replaced_decision_terms,
            scan_literal_margins,
            scan_policy_registry_sync,
        ):
            scanner(target_gov_files, report)
        report.finalize()
        for f in report.findings:
            if f.severity in ("CRITICAL", "WARNING"):
                violations.append(f"[{f.severity}] {f.title}: {f.description}")
    except Exception as exc:
        violations.append(f"Failed executing governance harmonization parity check: {exc}")
    return violations


def build_configuration_inventory(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Compile comprehensive inventory of configuration sources, parameters, and drift status."""
    root = repo_root or REPO_ROOT
    param_path = root / "config" / "runtime" / "parameters.json"
    model_json_path = root / "config" / "runtime" / "model_registry.json"
    project_reg_path = root / "config" / "governance" / "project_registry.json"

    rca_violations = check_rca_ssot_violations(root)
    rca_by_id: Dict[str, List[Dict[str, str]]] = {}
    for rv in rca_violations:
        rca_by_id.setdefault(rv["rca_id"], []).append(rv)

    rca_names = {
        "RCA_01": "RCA_01_duplicate_json_keys",
        "RCA_02": "RCA_02_duplicate_shadow_model_registry_files",
        "RCA_03": "RCA_03_hitl_ambiguity_margin_alignment",
        "RCA_04": "RCA_04_storage_wal_busy_timeout",
        "RCA_05": "RCA_05_parameter_default_parity",
        "RCA_06": "RCA_06_project_taxonomy_registry",
        "RCA_07": "RCA_07_machine_absolute_paths",
        "RCA_08": "RCA_08_ci_gate_count_parity",
    }

    inventory_map: Dict[str, Any] = {}
    for code, full_name in rca_names.items():
        errs = rca_by_id.get(code, [])
        inventory_map[full_name] = {
            "status": "VIOLATION_DETECTED" if errs else "VERIFIED_UNIFIED",
            "violations": errs,
        }

    return {
        "timestamp_utc": "2026-10-18T07:00:00Z",
        "schema_version": "2.0.0",
        "canonical_sources": {
            "parameters": str(param_path.relative_to(root)) if param_path.exists() else None,
            "model_registry": str(model_json_path.relative_to(root)) if model_json_path.exists() else None,
            "project_registry": str(project_reg_path.relative_to(root)) if project_reg_path.exists() else None,
        },
        "rca_inventory": inventory_map,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="SSOT Configuration & Release Threshold Scanner")
    parser.add_argument("--inventory", type=str, default="", help="Write baseline configuration inventory JSON to path")
    parser.add_argument("--staged", action="store_true", help="Scan only staged files in git")
    parser.add_argument("files", nargs="*", default=None, help="Specific files to scan")
    args = parser.parse_args()

    target_files = [f for f in args.files if f != "--staged"] if args.files else None
    if args.staged and not target_files:
        try:
            import subprocess
            res = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            target_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        except Exception:
            target_files = None

    print("================================================================================")
    print("  SSOT CONFIGURATION & RELEASE THRESHOLD SCANNER")
    print("================================================================================")

    if args.inventory:
        inv = build_configuration_inventory()
        out_path = Path(args.inventory)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(inv, indent=2), encoding="utf-8")
        print(f"  ✅ Generated configuration inventory manifest: {out_path.relative_to(REPO_ROOT)}")

    all_violations: List[str] = []

    print("[1/6] Auditing parameters.json parity...")
    param_violations = check_parameter_file_parity()
    all_violations.extend(param_violations)
    if param_violations:
        for v in param_violations:
            print(f"  ❌ {v}")
    else:
        print("  ✅ parameters.json has canonical thresholds (output1=90, output2=90, output3=90)")

    print("[2/6] Auditing CANONICAL_RELEASE_POLICY runtime semantics...")
    policy_violations = check_release_policy_runtime()
    all_violations.extend(policy_violations)
    if policy_violations:
        for v in policy_violations:
            print(f"  ❌ {v}")
    else:
        print("  ✅ Release policy enforces strict PASS (>=90) and no PASS_ADVISORY for Output 2 & 3")

    print("[3/6] Auditing model_registry.json approved models SSOT...")
    model_violations = check_model_registry_approved_models_parity()
    all_violations.extend(model_violations)
    if model_violations:
        for v in model_violations:
            print(f"  ❌ {v}")
    else:
        print("  ✅ model_registry.json declares only approved models for OpenAI provider")

    print("[4/6] Scanning codebase for hardcoded literals & write-bypass patterns...")
    code_violations = scan_source_code_violations(files=target_files)
    all_violations.extend(code_violations)
    if code_violations:
        for v in code_violations:
            print(f"  ❌ {v}")
    else:
        print("  ✅ No hardcoded threshold bypasses or write authorization leaks detected")

    print("[5/6] Auditing RCA_01-RCA_08 SSOT invariant checks...")
    rca_violations = check_rca_ssot_violations()
    for rv in rca_violations:
        all_violations.append(f"{rv['rca_id']} ({rv['target']}): {rv['detail']}")
    if rca_violations:
        for rv in rca_violations:
            print(f"  ❌ [{rv['rca_id']}] {rv['target']}: {rv['detail']}")
    else:
        print("  ✅ All RCA_01-RCA_08 SSOT invariant checks passed (0 drift detected)")

    print("[6/6] Auditing cross-surface governance harmonization...")
    harm_violations = check_governance_harmonization_parity(files=target_files)
    all_violations.extend(harm_violations)
    if harm_violations:
        for v in harm_violations:
            print(f"  ❌ {v}")
    else:
        print("  ✅ Cross-surface governance harmonization passed (0 CRITICAL, 0 WARNING)")

    print("================================================================================")
    if all_violations:
        print(f"FAILED: Found {len(all_violations)} SSOT configuration violation(s).")
        return 1

    print("PASSED: 100% SSOT configuration and release threshold checks satisfied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
