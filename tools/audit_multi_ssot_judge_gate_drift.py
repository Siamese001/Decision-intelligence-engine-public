#!/usr/bin/env python3
"""tools/audit_multi_ssot_judge_gate_drift.py

Unified 5-Vector Multi-SSOT Drift Auditor covering:
  Vector 1: Synthesis Stage Prompts vs LLM Judge Prompts
  Vector 2: LLM Judge Prompts vs Declarative Code Contracts & Pydantic Scorecards
  Vector 3: Deterministic Code Gates (X1-X3, QA Gates, Release Policy) vs Judge Criteria
  Vector 4: Runtime & Historical Judge Defect Baselines (F1-F6) & Config Parity
  Vector 5: Cross-Surface Governance Harmonization & Prompt SSOT Integrity

Exit codes:
  0 — Zero drift violations across all 5 vectors
  1 — One or more drift violations detected
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _finding(vector: str, code: str, target: str, detail: str) -> Dict[str, str]:
    return {"vector": vector, "code": code, "target": target, "detail": detail}


def _check_v1_files(repo_root: Path) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    stages = repo_root / "config" / "prompts" / "synthesis" / "stages"
    gov = repo_root / "governance" / "decision_intelligence"
    spec = {
        "STAGE_01": ("config/prompts/synthesis/stages/01_forensic_normalizer.md", stages / "01_forensic_normalizer.md"),
        "STAGE_02": ("config/prompts/synthesis/stages/02_executive_recap.md", stages / "02_executive_recap.md"),
        "STAGE_03": ("config/prompts/synthesis/stages/03_graph_extractor.md", stages / "03_graph_extractor.md"),
        "JUDGE_01": ("governance/decision_intelligence/FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md", gov / "FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md"),
        "JUDGE_02": ("governance/decision_intelligence/EXECUTIVE_RECAP_JUDGE_PROMPT.md", gov / "EXECUTIVE_RECAP_JUDGE_PROMPT.md"),
        "JUDGE_03": ("governance/decision_intelligence/GRAPH_EXTRACTION_JUDGE_PROMPT.md", gov / "GRAPH_EXTRACTION_JUDGE_PROMPT.md"),
    }
    texts: Dict[str, str] = {}
    findings: List[Dict[str, str]] = []
    for key, (rel, path) in spec.items():
        txt = _read_text(path)
        texts[key] = txt
        if not txt:
            findings.append(_finding("V1_PROMPT_TO_JUDGE", f"MISSING_{key}", rel, f"Required prompt '{rel}' missing or empty."))
    return texts, findings


def _check_v1_tokens(texts: Dict[str, str]) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    s1, s2, s3 = texts["STAGE_01"], texts["STAGE_02"], texts["STAGE_03"]
    j1, j2, j3 = texts["JUDGE_01"], texts["JUDGE_02"], texts["JUDGE_03"]

    pair_rules = [
        (s1, j1, "Ambiguity Lock Register", "O1_ALR_DRIFT", "01_forensic_normalizer.md <-> FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md"),
        (s2, j2, "Active Technical Spikes & Experimental Inquiries", "O2_FORBIDDEN_SECTION_DRIFT", "02_executive_recap.md <-> EXECUTIVE_RECAP_JUDGE_PROMPT.md"),
        (s2, j2, "Action & Governance Ledger", "O2_ACTION_LEDGER_DRIFT", "02_executive_recap.md <-> EXECUTIVE_RECAP_JUDGE_PROMPT.md"),
        (s2, j2, "Unassigned (Pending Owner Assignment)", "O2_C25_PENDING_OWNER_DRIFT", "02_executive_recap.md <-> EXECUTIVE_RECAP_JUDGE_PROMPT.md"),
        (s3, j3, "action_updates", "O3_ACTION_EXCLUSION_DRIFT", "03_graph_extractor.md <-> GRAPH_EXTRACTION_JUDGE_PROMPT.md"),
    ]
    for a, b, token, code, target in pair_rules:
        if token not in a or token not in b:
            findings.append(_finding("V1_PROMPT_TO_JUDGE", code, target, f"Token '{token}' missing across stage and judge prompts."))

    from dual_pipeline.parameters import get_float_parameter

    collapse_pct = int(round(get_float_parameter("output1.single_speaker_collapse_threshold", 0.85) * 100))
    generic_pct = int(round(get_float_parameter("output1.generic_speaker_ceiling_percent", 15.0)))
    if f"{collapse_pct}%" not in j1 or f"{generic_pct}%" not in j1:
        findings.append(_finding("V1_PROMPT_TO_JUDGE", "O1_ATTRIBUTION_THRESHOLD_DRIFT", "FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md", f"Missing {collapse_pct}% or {generic_pct}% speaker thresholds."))
    if "HAS_ROLE" not in j3:
        findings.append(_finding("V1_PROMPT_TO_JUDGE", "O3_PREDICATE_DRIFT", "GRAPH_EXTRACTION_JUDGE_PROMPT.md", "Missing canonical HAS_ROLE predicate."))

    for arch in ("ARCHITECTURE_DEEP_DIVE", "STRATEGIC_DISCOVERY", "TACTICAL_ALIGNMENT", "GOVERNANCE_REVIEW", "LESSONS_LEARNED"):
        if arch not in s3 or arch not in j3:
            findings.append(_finding("V1_PROMPT_TO_JUDGE", "O3_ARCHETYPE_ENUM_DRIFT", "03_graph_extractor.md <-> JUDGE_03", f"Archetype '{arch}' missing."))

    for prefix in ("person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:"):
        if prefix not in s3 or prefix not in j3:
            findings.append(_finding("V1_PROMPT_TO_JUDGE", "O3_ENTITY_PREFIX_DRIFT", "03_graph_extractor.md <-> JUDGE_03", f"Entity prefix '{prefix}' missing."))

    return findings


def check_vector_1_prompt_to_judge_parity(repo_root: Path) -> List[Dict[str, str]]:
    """Vector 1: Verify synthesis stage prompts align with LLM judge prompts."""
    texts, missing = _check_v1_files(repo_root)
    if missing:
        return missing
    return _check_v1_tokens(texts)


def _check_v2_scorecard_keys(o1_c: Any, o3_c: Any, s1_cls: Any, s3_cls: Any, j1: str, j3: str) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    for dim in o1_c.rubric_dimensions:
        if dim.key not in s1_cls.model_fields or (j1 and dim.key not in j1):
            findings.append(_finding("V2_JUDGE_TO_CONTRACT", "O1_SCORECARD_KEY_DRIFT", f"Output1ScorecardV2.{dim.key}", f"Missing '{dim.key}'."))
    for dim in o3_c.rubric_dimensions:
        if dim.key not in s3_cls.model_fields or (j3 and dim.key not in j3):
            findings.append(_finding("V2_JUDGE_TO_CONTRACT", "O3_SCORECARD_KEY_DRIFT", f"Output3ScorecardV2.{dim.key}", f"Missing '{dim.key}'."))
    return findings


def check_vector_2_judge_to_code_contracts(repo_root: Path) -> List[Dict[str, str]]:
    """Vector 2: Verify Judge Prompts match Declarative Code Contracts & Pydantic Scorecards."""
    gov = repo_root / "governance" / "decision_intelligence"
    j1 = _read_text(gov / "FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md")
    j2 = _read_text(gov / "EXECUTIVE_RECAP_JUDGE_PROMPT.md")
    j3 = _read_text(gov / "GRAPH_EXTRACTION_JUDGE_PROMPT.md")

    from dual_pipeline.prompts.contracts import OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT
    from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
        Output1ScorecardV2,
        Output2SectionScoresV2,
        Output3ScorecardV2,
    )
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

    findings: List[Dict[str, str]] = []
    for c in (OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT):
        pts = sum(d.max_points for d in c.rubric_dimensions)
        if pts != 100:
            findings.append(_finding("V2_JUDGE_TO_CONTRACT", f"O{c.output_id}_RUBRIC_SUM_DRIFT", f"OUTPUT{c.output_id}_CONTRACT", f"Sum {pts} != 100."))

    if any(flr != 14.0 for flr in OUTPUT1_CONTRACT.get_dimension_floors().values()) or (j1 and "14/25" not in j1):
        findings.append(_finding("V2_JUDGE_TO_CONTRACT", "O1_FLOOR_DRIFT", "OUTPUT1_CONTRACT", "Output 1 floors diverged from 14/25."))

    from dual_pipeline.parameters import (
        get_float_parameter,
        get_int_parameter,
        get_output2_section_score_specs,
    )

    exp_collapse = get_float_parameter("output1.single_speaker_collapse_threshold", 0.85)
    if getattr(OUTPUT1_CONTRACT, "single_speaker_collapse_threshold", None) != exp_collapse:
        findings.append(_finding("V2_JUDGE_TO_CONTRACT", "O1_COLLAPSE_CONTRACT_DRIFT", "OUTPUT1_CONTRACT", f"Collapse threshold != {exp_collapse}."))

    expected_o2 = {k: f"0-{mx} points" for k, mx, _ in get_output2_section_score_specs()}
    for f_key, p_str in expected_o2.items():
        if f_key not in Output2SectionScoresV2.model_fields or (j2 and p_str not in j2):
            findings.append(_finding("V2_JUDGE_TO_CONTRACT", "O2_SECTION_WEIGHT_DRIFT", f"Output2SectionScoresV2.{f_key}", f"Drifted '{p_str}'."))

    exp_o3 = {
        "dimension_1_strategic_edge": 11.0,
        "dimension_2_knowledge_layer_density": 11.0,
        "dimension_3_semantic_atomicity": 14.0,
        "dimension_4_predicate_specificity": 11.0,
        "dimension_5_exclusion_of_action_items": 11.0,
        "dimension_6_evidence_grounding_and_schema": 14.0,
    }
    if OUTPUT3_CONTRACT.get_dimension_floors() != exp_o3 or (j3 and ("11/15" not in j3 or "14/20" not in j3)):
        findings.append(_finding("V2_JUDGE_TO_CONTRACT", "O3_FLOOR_DRIFT", "OUTPUT3_CONTRACT", "Output 3 floors diverged from 11/15 and 14/20."))

    findings.extend(_check_v2_scorecard_keys(OUTPUT1_CONTRACT, OUTPUT3_CONTRACT, Output1ScorecardV2, Output3ScorecardV2, j1, j3))

    pol = CANONICAL_RELEASE_POLICY
    exp_thresholds = (
        get_int_parameter("release_thresholds.output1_production_pass_threshold", 90),
        get_int_parameter("release_thresholds.output2_production_pass_threshold", 90),
        get_int_parameter("release_thresholds.output3_production_pass_threshold", 90),
    )
    if (pol.output1_pass_threshold, pol.output2_pass_threshold, pol.output3_pass_threshold) != exp_thresholds:
        findings.append(_finding("V2_JUDGE_TO_CONTRACT", "RELEASE_THRESHOLD_POLICY_DRIFT", "CANONICAL_RELEASE_POLICY", f"Pass thresholds must be {exp_thresholds}."))

    return findings


def _check_v3_x_gates() -> List[Dict[str, str]]:
    from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract

    findings: List[Dict[str, str]] = []
    recap_c25 = (
        "# 1. Meeting Objective\nContext.\n\n# 2. Action & Governance Ledger\n"
        "| # | Action | Accountable Owner | Status |\n|---|---|---|---|\n"
        "| 1 | Verify schema | Unassigned (Pending Owner Assignment) | Open |\n"
    )
    ok_x2, errs_x2 = MeetingXExitContract.validate_x2_recap_structure(recap_c25)
    if not ok_x2:
        findings.append(_finding("V3_GATE_VS_JUDGE", "X2_C25_PENDING_OWNER_REJECTION", "MeetingXExitContract", f"Rejected C-25 token: {errs_x2}"))

    x_gates_src = _read_text(ROOT / "dual_pipeline" / "transcript_stages_core" / "x_gates.py")
    if "Unassigned (Pending Owner Assignment)" not in x_gates_src:
        findings.append(_finding("V3_GATE_VS_JUDGE", "XGATES_C25_EXEMPTION_MISSING", "XExitGatesEvaluator", "Missing C-25 owner exemption."))

    ok_bad_x2, _ = MeetingXExitContract.validate_x2_recap_structure(recap_c25.replace("Unassigned (Pending Owner Assignment)", "Unknown"))
    if ok_bad_x2:
        findings.append(_finding("V3_GATE_VS_JUDGE", "X2_PLACEHOLDER_OWNER_BYPASS", "MeetingXExitContract", "Accepted 'Unknown' owner."))

    f3_bad = {
        "schema_version": "5.7",
        "evidence_index": {},
        "graph_mutations": {"entity_upserts": [], "relationship_signals": [{"from_id": "person:a", "relationship": "HAS_ROLE_IN", "to_id": "role:b"}]},
    }
    ok_x3, errs_x3 = MeetingXExitContract.validate_x3_cross_output_coherence("", "", f3_bad)
    if ok_x3 or not any("HAS_ROLE" in e for e in errs_x3):
        findings.append(_finding("V3_GATE_VS_JUDGE", "X3_DEPRECATED_PREDICATE_BYPASS", "MeetingXExitContract", "Failed to block HAS_ROLE_IN."))

    return findings


def check_vector_3_deterministic_gates_vs_judges(repo_root: Path) -> List[Dict[str, str]]:
    """Vector 3: Verify deterministic code gates (X1-X3, QA Gates, Release Policy) match Judge Criteria."""
    from dual_pipeline.prompts.contracts import FORBIDDEN_APPENDIX_DISCLAIMERS, WORD_SALAD_PATTERNS
    from dual_pipeline.prompts.contracts.versioned_judge_contracts import validate_versioned_judge_scorecard
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY, ReleaseVerdict

    findings = _check_v3_x_gates()
    if (
        CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 88) == ReleaseVerdict.PASS_ADVISORY
        or CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 88) == ReleaseVerdict.PASS_ADVISORY
    ):
        findings.append(_finding("V3_GATE_VS_JUDGE", "ADVISORY_VERDICT_LEAK_O2_O3", "CANONICAL_RELEASE_POLICY", "PASS_ADVISORY forbidden on O2/O3."))

    floor_breach_o3 = {
        "judge_model": "test-judge", "prompt_digest": "a" * 64, "rubric_version": "2.0",
        "dimension_1_strategic_edge": 15.0, "dimension_2_knowledge_layer_density": 15.0,
        "dimension_3_semantic_atomicity": 20.0, "dimension_4_predicate_specificity": 10.0,
        "dimension_5_exclusion_of_action_items": 15.0, "dimension_6_evidence_grounding_and_schema": 20.0,
        "composite_score": 95.0, "verdict": "PASS", "forensic_rationale": "Test.", "evaluated_span_coverage": 1.0,
    }
    if validate_versioned_judge_scorecard(floor_breach_o3, 3)[0] or validate_micro_judge_scorecard(floor_breach_o3, output_num=3)[0]:
        findings.append(_finding("V3_GATE_VS_JUDGE", "FLOOR_BREACH_PASS_ACCEPTED", "validate_versioned_judge_scorecard", "Accepted floor breach PASS."))

    doc_fake = {"status": "NOT_APPLICABLE", "reason": "NOT_APPLICABLE_DOCUMENT_MODE", "composite_score": 100.0, "verdict": "PASS"}
    if validate_versioned_judge_scorecard(doc_fake, 1, modality="DOCUMENT")[0] or validate_micro_judge_scorecard(doc_fake, output_num=1, pipeline_mode="DOCUMENT")[0]:
        findings.append(_finding("V3_GATE_VS_JUDGE", "DOC_MODE_SYNTHETIC_100_ACCEPTED", "validate_versioned_judge_scorecard", "Accepted synthetic 100/PASS."))

    j2 = _read_text(repo_root / "governance" / "decision_intelligence" / "EXECUTIVE_RECAP_JUDGE_PROMPT.md").lower()
    if j2 and any(p not in j2 for p in ("no reliable utterance mapping", "aspirational operating concept", "bounded execution wedge")):
        findings.append(_finding("V3_GATE_VS_JUDGE", "O2_ANTI_BOILERPLATE_PROMPT_DRIFT", "EXECUTIVE_RECAP_JUDGE_PROMPT.md", "Missing word-salad/boilerplate pattern."))
    if not FORBIDDEN_APPENDIX_DISCLAIMERS or not WORD_SALAD_PATTERNS:
        findings.append(_finding("V3_GATE_VS_JUDGE", "O2_CONTRACT_PATTERNS_EMPTY", "output2_contract.py", "Empty forbidden patterns tuple."))

    return findings


def check_vector_4_runtime_judge_and_config_ssot(repo_root: Path) -> List[Dict[str, str]]:
    """Vector 4: Verify Config SSOT Parity & Revalidated Judge Criteria Defect Baselines (F1-F6)."""
    from tools.audit_judge_criteria import run_audit
    from tools.scan_ssot_configuration_violations import (
        check_model_registry_approved_models_parity,
        check_parameter_file_parity,
        check_release_policy_runtime,
        scan_source_code_violations,
    )

    findings: List[Dict[str, str]] = []
    violations = (
        check_parameter_file_parity()
        + check_release_policy_runtime()
        + check_model_registry_approved_models_parity()
        + scan_source_code_violations()
    )
    for v in violations:
        findings.append(_finding("V4_CONFIG_AND_RUNTIME_JUDGE", "SSOT_CONFIG_VIOLATION", "config/runtime | dual_pipeline", v))

    if (repo_root / "output").is_dir():
        defs = run_audit(repo_root=repo_root, limit=30, revalidate=True).get("summary", {}).get("defects_summary", {})
        for key in ("F1_synthetic_doc_pass_count", "F2_floor_breach_count", "F5_tactical_strategic_inflation_count", "F6_verdict_contradiction_count"):
            if defs.get(key, 0) > 0:
                findings.append(_finding("V4_CONFIG_AND_RUNTIME_JUDGE", f"JUDGE_DEFECT_{key.upper()}", "output/", f"{defs[key]} unresolved {key}."))
    return findings


def check_vector_5_governance_and_prompt_ssot(repo_root: Path) -> List[Dict[str, str]]:
    """Vector 5: Verify Prompt SSOT Registry, Worktree Isolation Invariants, and Governance Harmonization."""
    from tools.audit_governance_harmonization import (
        HarmonizationReport,
        discover_governance_files,
        scan_deprecated_tool_names,
        scan_literal_margins,
        scan_replaced_decision_terms,
        scan_taxonomy_alignment,
        scan_threshold_drift,
    )
    from tools.lint_prompt_ssot_integrity import validate_contracts

    findings = [_finding("V5_GOVERNANCE_HARMONIZATION", "PROMPT_SSOT_INTEGRITY_DEFECT", "prompts", d) for d in validate_contracts()]
    parser_txt = _read_text(repo_root / "dual_pipeline/cli/parser.py")
    if '"--no-enforce-worktree"' not in parser_txt or ('"--enforce-worktree"' in parser_txt and "BooleanOptionalAction" in parser_txt.split('"--enforce-worktree"', 1)[1][:220]):
        findings.append(_finding("V5_GOVERNANCE_HARMONIZATION", "CLI_WORKTREE_BYPASS_TOGGLE_DRIFT", "dual_pipeline/cli/parser.py", "--no-enforce-worktree must be blocked and BooleanOptionalAction barred on --enforce-worktree."))
    lifecycle_txt = _read_text(repo_root / "dual_pipeline/canonical_pipeline/lifecycle.py")
    if "NEE_ENFORCE_WORKTREE" in lifecycle_txt or "enforce_wt = False" in lifecycle_txt:
        findings.append(_finding("V5_GOVERNANCE_HARMONIZATION", "RUNTIME_WORKTREE_BYPASS_DRIFT", "dual_pipeline/canonical_pipeline/lifecycle.py", "Unconditional worktree routing violated by NEE_ENFORCE_WORKTREE or enforce_wt=False bypass."))
    files = discover_governance_files()
    harm_rep = HarmonizationReport(scan_timestamp=datetime.now(timezone.utc).isoformat(), repo_root=str(repo_root), total_files_scanned=len(files))
    for scanner in (scan_threshold_drift, scan_taxonomy_alignment, scan_deprecated_tool_names, scan_replaced_decision_terms, scan_literal_margins):
        scanner(files, harm_rep)
    harm_rep.finalize()
    for cf in (f for f in harm_rep.findings if f.severity == "CRITICAL"):
        loc_files = [str(loc.get("path", loc.get("file", ""))) for loc in getattr(cf, "affected_files", []) if isinstance(loc, dict)]
        findings.append(_finding("V5_GOVERNANCE_HARMONIZATION", f"HARMONIZATION_{cf.category.upper()}", ", ".join(loc_files[:3]) or cf.ssot_file, f"{cf.title}: {cf.description}"))
    return findings


def run_multi_ssot_drift_audit(repo_root: Path | None = None) -> Tuple[bool, Dict[str, Any]]:
    """Execute all 5 drift vectors and return (passed, report_dict)."""
    root = (repo_root or ROOT).resolve()
    vec_map = {
        "V1_PROMPT_TO_JUDGE": check_vector_1_prompt_to_judge_parity(root),
        "V2_JUDGE_TO_CONTRACT": check_vector_2_judge_to_code_contracts(root),
        "V3_GATE_VS_JUDGE": check_vector_3_deterministic_gates_vs_judges(root),
        "V4_CONFIG_AND_RUNTIME_JUDGE": check_vector_4_runtime_judge_and_config_ssot(root),
        "V5_GOVERNANCE_HARMONIZATION": check_vector_5_governance_and_prompt_ssot(root),
    }
    all_findings = [item for sub in vec_map.values() for item in sub]
    vectors = {k: {"status": "PASS" if not v else "FAIL", "violations": len(v), "findings": v} for k, v in vec_map.items()}
    passed = len(all_findings) == 0
    return passed, {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(root),
        "overall_status": "PASS" if passed else "FAIL",
        "total_violations": len(all_findings),
        "vectors": vectors,
        "findings": all_findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-SSOT Prompt, Judge, Contract & Gate Drift Auditor")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root to scan")
    parser.add_argument("--json-out", type=Path, default=None, help="Path to write JSON report")
    args = parser.parse_args()

    passed, report = run_multi_ssot_drift_audit(args.repo_root)
    if args.json_out:
        out_p = args.json_out if args.json_out.is_absolute() else (args.repo_root / args.json_out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("================================================================================")
    print("  MULTI-SSOT PROMPT, JUDGE, CONTRACT & DETERMINISTIC GATE DRIFT AUDITOR")
    print("================================================================================")
    for v_name, v_data in report["vectors"].items():
        icon = "✅" if v_data["status"] == "PASS" else "❌"
        print(f"  {icon} {v_name}: {v_data['status']} ({v_data['violations']} violation(s))")
        for f in v_data["findings"]:
            print(f"      - [{f['code']}] {f['target']}: {f['detail']}")
    print("================================================================================")
    print(f"OVERALL: {report['overall_status']} ({report['total_violations']} total violation(s))")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
