"""Deterministic regression and boundary qualification tests for ssot_drift_remediation."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from tools.audit_governance_harmonization import (
    HarmonizationReport,
    scan_deprecated_tool_names,
)
from tools.audit_multi_ssot_judge_gate_drift import (
    check_vector_1_prompt_to_judge_parity,
    check_vector_2_judge_to_code_contracts,
    check_vector_3_deterministic_gates_vs_judges,
    check_vector_4_runtime_judge_and_config_ssot,
    check_vector_5_governance_and_prompt_ssot,
)
from tools.file_budget_core import count_lines
from tools.file_budget_core.mmi import calculate_ast_metrics_from_source
from tools.file_budget_core.policy import evaluate_module_file
from tools.lint_parameter_references import load_declared_parameter_keys
from tools.run_governance_linters import CORE_LINTERS
from tools.scheduled_task_core.remediation_planner import generate_and_validate_remediation_plan
from tools.validate_hitl_ledger_integrity import validate_ledger_integrity
from dual_pipeline.transcript_qa_core.citation_utils import _is_trivial_citation

ROOT = Path(__file__).resolve().parent.parent


def test_unified_linter_runner_enforces_module_budget_ratchet() -> None:
    """Verify Module File Budgets linter in CORE_LINTERS passes --ratchet for baseline parity."""
    budget_cmds = [cmd for name, cmd in CORE_LINTERS if name == "Module File Budgets"]
    assert len(budget_cmds) == 1
    assert "--ratchet" in budget_cmds[0]


def test_automation_prompts_zero_deprecated_ask_question_references() -> None:
    """Verify automation prompts have zero deprecated ask_question tool references."""
    e2e_prompt = ROOT / "automations" / "prompts" / "e2e_critical_functionality_assurance_agent.md"
    testing_prompt = ROOT / "automations" / "prompts" / "testing_gaps_assurance_agent.md"
    report = HarmonizationReport(
        scan_timestamp="2026-10-25T22:28:00+00:00",
        repo_root=str(ROOT),
        total_files_scanned=2,
    )
    scan_deprecated_tool_names([e2e_prompt, testing_prompt], report)
    assert report.findings == [], f"Expected 0 deprecated tool name findings, got: {report.findings}"


def test_graduated_exclusions_and_burndown_baseline_ratcheted() -> None:
    """Verify graduated <= 400L modules are removed from exclusions and burndown baseline."""
    exclusions_path = ROOT / "config" / "governance" / "file_budget_exclusions.json"
    exclusions_data = json.loads(exclusions_path.read_text(encoding="utf-8"))
    excluded_paths = {item.get("path") for item in exclusions_data.get("exclusions", [])}
    assert "scripts/audit_and_judge_project_nexus.py" not in excluded_paths

    baseline_path = ROOT / "reports" / "file_budget_burndown_baseline.json"
    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    modules = baseline_data.get("modules", {})
    assert "tools/lint_cognitive_complexity.py" not in modules
    assert "dual_pipeline/intake_core/change_set_helpers.py" not in modules
    assert count_lines(ROOT / "tools" / "lint_cognitive_complexity.py") <= 400
    assert count_lines(ROOT / "dual_pipeline" / "intake_core" / "change_set_helpers.py") <= 400
    assert baseline_data.get("total_oversized_modules") == len(modules)


def test_multi_ssot_vectors_zero_drift() -> None:
    """Verify that all 5 Multi-SSOT vectors report zero drift violations."""
    v1 = check_vector_1_prompt_to_judge_parity(ROOT)
    v2 = check_vector_2_judge_to_code_contracts(ROOT)
    v3 = check_vector_3_deterministic_gates_vs_judges(ROOT)
    v4 = check_vector_4_runtime_judge_and_config_ssot(ROOT)
    v5 = check_vector_5_governance_and_prompt_ssot(ROOT)
    total_violations = len(v1) + len(v2) + len(v3) + len(v4) + len(v5)
    assert total_violations == 0, f"Expected 0 Multi-SSOT violations, got {total_violations}: {v1+v2+v3+v4+v5}"


def test_parameter_registry_completeness() -> None:
    """Verify parameters.json has declared keys and canonical release thresholds."""
    param_path = ROOT / "config" / "runtime" / "parameters.json"
    assert param_path.is_file()
    keys = load_declared_parameter_keys()
    assert len(keys) >= 180
    assert "release_thresholds.output1_production_pass_threshold" in keys
    assert "release_thresholds.output2_production_pass_threshold" in keys
    assert "release_thresholds.output3_production_pass_threshold" in keys


def test_hitl_decision_ledger_merkle_integrity() -> None:
    """Verify hitl_decision_ledger.json maintains valid Merkle cryptographic integrity."""
    result = validate_ledger_integrity(None)
    assert isinstance(result, dict)
    assert result["is_valid"] is True
    assert result["integrity_status"] in ("VALID", "GENESIS_EMPTY")
    assert result["errors"] == []
    assert result["record_count"] >= 0


def test_remediation_plan_generation_and_validation(tmp_path: Path) -> None:
    """Verify remediation_planner synthesizes governance-compliant wave plans."""
    findings = [
        {"file": "dual_pipeline/release_policy.py", "issue": "MONOLITHIC_MODULE", "line_count": 825},
        {"file": "dual_pipeline/in_memory_graph.py", "issue": "MONOLITHIC_MODULE", "line_count": 747},
    ]
    plan_path, plan_sha, content = generate_and_validate_remediation_plan(
        task_id="ssot_drift_remediation",
        run_id="test-run-001",
        findings=findings,
        run_dir=tmp_path,
        branch_slug="test_ssot_drift_plan",
    )
    assert plan_path.is_file()
    assert len(plan_sha) == 64
    assert "## Wave 1:" in content
    assert "## Wave 2:" in content


def test_trivial_citation_recognizes_role_verification_tokens() -> None:
    """Verify _is_trivial_citation recognizes space and underscore role verification receipts."""
    assert _is_trivial_citation("C0.6 role verification receipt") is True
    assert _is_trivial_citation("GraphDB verification receipt") is True
    assert _is_trivial_citation("role_verification") is True
    assert _is_trivial_citation("graphdb_role_verification") is True
    assert _is_trivial_citation("[00:12:34] Speaker said specific substantive claim.") is False


def test_margin_ssot_exempt_files_ratcheted_to_domain_owners() -> None:
    """Verify tools/lint_margin_ssot.py EXEMPT_FILES is ratcheted down to <= 6 files with zero violations."""
    from tools.lint_margin_ssot import EXEMPT_FILES, main as lint_margin_main

    assert len(EXEMPT_FILES) <= 6
    assert "dual_pipeline/agent_architecture_core/runtime_loop.py" not in EXEMPT_FILES
    assert "dual_pipeline/decision_engine_core/devil_advocate_agent.py" not in EXEMPT_FILES
    assert "dual_pipeline/graph_maintenance_core/hygiene_council.py" not in EXEMPT_FILES
    assert "dual_pipeline/healing_policy.py" not in EXEMPT_FILES
    assert "dual_pipeline/llm_judge_core/logprob_scorer.py" not in EXEMPT_FILES
    assert "dual_pipeline/token_optimization/schemas.py" not in EXEMPT_FILES
    assert lint_margin_main([]) == 0


def test_hitl_tool_name_linter_passes_across_repository() -> None:
    """Verify tools/lint_hitl_tool_name.py passes with 0 deprecated tool name violations."""
    from tools.lint_hitl_tool_name import lint_tool_names

    exit_code, findings = lint_tool_names(ROOT)
    assert exit_code == 0, f"Expected 0 deprecated tool name violations, got: {findings[:5]}"
    assert findings == []


def test_benchmark_live_capture_hermetic_execution_and_budget() -> None:
    """Verify benchmark_live_capture.py runs end-to-end in a sandbox and satisfies ratcheted line budget."""
    import tools.benchmark_live_capture as blc

    bench_path = ROOT / "tools" / "benchmark_live_capture.py"
    lines = count_lines(bench_path)
    assert lines <= 564, f"Expected benchmark_live_capture.py <= 564 lines, got {lines}"

    verdict = evaluate_module_file(bench_path, repo_root=ROOT)
    assert not verdict.is_blocking, f"Unexpected blocking budget verdict: {verdict}"

    with tempfile.TemporaryDirectory(prefix="test_bench_ssot_") as tmp:
        out_path = Path(tmp) / "baseline_metrics.json"
        old_argv = sys.argv[:]
        try:
            sys.argv = ["benchmark_live_capture.py", "--out", str(out_path)]
            rc = blc.main()
        finally:
            sys.argv = old_argv

        assert rc == 0
        assert out_path.is_file()
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert "latency_ms" in payload["metrics"]["M3_init_to_first_sample_latency_ms"]
        assert all(payload["acceptance_criteria_verification"].values())


def test_graduated_modules_removed_from_ratchet_baseline() -> None:
    """Verify graduated <=400L modules are removed from file_budget_burndown_baseline.json and cognitive exemptions."""
    baseline_path = ROOT / "reports" / "file_budget_burndown_baseline.json"
    exclusions_path = ROOT / "config" / "governance" / "file_budget_exclusions.json"

    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    exclusions_data = json.loads(exclusions_path.read_text(encoding="utf-8"))

    modules_map = baseline_data.get("modules", {})
    cc_exempt = exclusions_data.get("cognitive_complexity_exempt_prefixes", [])

    graduated = [
        "dual_pipeline/transcript_qa_core/graph_healer.py",
        "dual_pipeline/transcript_qa_core/recap_healer.py",
        "dual_pipeline/live_meeting_recorder_core/audio_capture_proof.py",
        "dual_pipeline/live_meeting_recorder_core/audio_master_stitcher.py",
    ]
    for mod in graduated:
        assert mod not in modules_map, f"Graduated module {mod} should be removed from burndown baseline"
        assert count_lines(ROOT / mod) <= 400

    signals_mod = "dual_pipeline/transcript_qa_core/graph_ontology_signals.py"
    assert signals_mod not in cc_exempt, f"{signals_mod} has CC <= 15 and <= 400L and should be graduated"
    metrics = calculate_ast_metrics_from_source(
        (ROOT / signals_mod).read_text(encoding="utf-8"), filename=signals_mod
    )
    assert metrics.max_cc <= 15
