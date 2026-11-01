"""
tests/test_ssot_multi_surface_scanner_e2e.py

End-to-End lifecycle tests for multi-surface SSOT drift scanners,
sidecar remediation adapter, RCA_01-RCA_08 checks, and parameter parity.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.scan_ssot_configuration_violations import (
    check_parameter_file_parity,
    check_release_policy_runtime,
    check_model_registry_approved_models_parity,
    check_rca_ssot_violations,
    build_configuration_inventory,
)
from tools.audit_multi_ssot_judge_gate_drift import (
    check_vector_1_prompt_to_judge_parity,
    check_vector_2_judge_to_code_contracts,
    check_vector_3_deterministic_gates_vs_judges,
    check_vector_4_runtime_judge_and_config_ssot,
    check_vector_5_governance_and_prompt_ssot,
)
from tools.scheduled_task_core.adapters_core.audits import run_ssot_drift_remediation
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_e2e_ssot_configuration_scanner_clean_repo():
    """Verify that all checks in scan_ssot_configuration_violations pass with 0 errors on clean repo."""
    param_errors = check_parameter_file_parity(REPO_ROOT)
    assert not param_errors, f"Parameter file parity errors: {param_errors}"

    policy_errors = check_release_policy_runtime()
    assert not policy_errors, f"Release policy runtime errors: {policy_errors}"

    model_errors = check_model_registry_approved_models_parity(REPO_ROOT)
    assert not model_errors, f"Model registry approved models errors: {model_errors}"

    rca_errors = check_rca_ssot_violations(REPO_ROOT)
    assert not rca_errors, f"RCA SSOT violations detected: {rca_errors}"

    inventory = build_configuration_inventory(REPO_ROOT)
    assert inventory["schema_version"] == "2.0.0"
    for rca_name, info in inventory["rca_inventory"].items():
        assert info["status"] == "VERIFIED_UNIFIED", f"{rca_name} failed: {info['violations']}"


def test_e2e_audit_multi_ssot_judge_gate_drift_all_vectors():
    """Verify that all 5 vectors of audit_multi_ssot_judge_gate_drift pass cleanly."""
    v1 = check_vector_1_prompt_to_judge_parity(REPO_ROOT)
    assert len(v1) == 0, f"Vector 1 violations: {v1}"

    v2 = check_vector_2_judge_to_code_contracts(REPO_ROOT)
    assert len(v2) == 0, f"Vector 2 violations: {v2}"

    v3 = check_vector_3_deterministic_gates_vs_judges(REPO_ROOT)
    assert len(v3) == 0, f"Vector 3 violations: {v3}"

    v4 = check_vector_4_runtime_judge_and_config_ssot(REPO_ROOT)
    assert len(v4) == 0, f"Vector 4 violations: {v4}"

    v5 = check_vector_5_governance_and_prompt_ssot(REPO_ROOT)
    assert len(v5) == 0, f"Vector 5 violations: {v5}"


def test_e2e_scheduled_task_ssot_remediation_adapter(tmp_path: Path):
    """Verify that run_ssot_drift_remediation executes all 5 vectors + multi-surface checks and passes."""
    context = AuthorityContext(
        task_id="scheduled_ssot_drift_remediation",
        run_id="test_run_e2e_001",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="system_scheduler",
        is_scheduled=True,
    )
    try:
        result = run_ssot_drift_remediation(
            context=context,
            fixture_root=REPO_ROOT,
            run_dir=tmp_path,
        )
        assert result.terminal_outcome == "COMPLETE", f"Expected COMPLETE outcome, got {result.terminal_outcome} with findings: {result.findings}"
        assert len(result.findings) == 0, f"Expected 0 findings, got: {result.findings}"
    finally:
        plan_cleanup = REPO_ROOT / "docs" / "plans" / f"refactor_ssot_drift_{context.run_id}.md"
        if plan_cleanup.exists():
            plan_cleanup.unlink()


def test_e2e_default_parameters_parity():
    """Verify dual_pipeline/parameters.py and release_thresholds.py match parameters.json."""
    param_path = REPO_ROOT / "config" / "runtime" / "parameters.json"
    with open(param_path, "r", encoding="utf-8") as f:
        pdata = json.load(f)["parameters"]

    expected_provenance = pdata["copilot"]["rigor"]["min_provenance_score"]
    assert expected_provenance == 0.15

    expected_output1_thresh = pdata["release_thresholds"]["output1_production_pass_threshold"]
    assert expected_output1_thresh == 90

    from dual_pipeline.parameters import get_copilot_rigor_min_provenance_score
    assert get_copilot_rigor_min_provenance_score() == 0.15

    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
    assert CANONICAL_RELEASE_POLICY.output1_pass_threshold == 90


def test_e2e_zero_shadow_model_registries():
    """Verify that no shadow model registry files exist."""
    assert not (REPO_ROOT / "config" / "runtime" / "model_registry.yaml").exists()
    assert not (REPO_ROOT / "dual_pipeline" / "config" / "runtime" / "model_registry.json").exists()
