"""Test suite asserting SSOT configuration integrity and release threshold policies."""
from __future__ import annotations

from dual_pipeline.release_thresholds import (
    ADVISORY_SCORE_FLOOR,
    CANONICAL_RELEASE_POLICY,
    OUTPUT1_PRODUCTION_PASS_THRESHOLD,
    OUTPUT2_PRODUCTION_PASS_THRESHOLD,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
    ReleaseVerdict,
)
from tools.scan_ssot_configuration_violations import (
    build_configuration_inventory,
    check_parameter_file_parity,
    check_release_policy_runtime,
    scan_source_code_violations,
)


def test_ssot_parameters_file_parity():
    """Verify parameters.json has exact canonical thresholds."""
    violations = check_parameter_file_parity()
    assert not violations, f"Parameter file violations: {violations}"


def test_ssot_release_policy_runtime_semantics():
    """Verify release policy enforces strict pass thresholds and rejects advisory for Outputs 2 & 3."""
    violations = check_release_policy_runtime()
    assert not violations, f"Release policy violations: {violations}"


def test_ssot_no_codebase_violations():
    """Verify codebase contains 0 hardcoded threshold bypasses or write-authorization leaks."""
    violations = scan_source_code_violations()
    assert not violations, f"Codebase SSOT violations detected: {violations}"


def test_output3_threshold_exact_90():
    """Verify Output 3 threshold is strictly 90 with no advisory permitted."""
    assert OUTPUT3_PRODUCTION_PASS_THRESHOLD == 90
    assert CANONICAL_RELEASE_POLICY.output3_pass_threshold == 90
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 90) == ReleaseVerdict.PASS
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 89) == ReleaseVerdict.FAIL
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 86) == ReleaseVerdict.FAIL
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, 80) == ReleaseVerdict.FAIL


def test_output2_threshold_exact_90():
    """Verify Output 2 threshold is strictly 90 with no advisory permitted."""
    assert OUTPUT2_PRODUCTION_PASS_THRESHOLD == 90
    assert CANONICAL_RELEASE_POLICY.output2_pass_threshold == 90
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 90) == ReleaseVerdict.PASS
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 89) == ReleaseVerdict.FAIL
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 80) == ReleaseVerdict.FAIL


def test_output1_threshold_allows_advisory():
    """Verify Output 1 threshold allows PASS_ADVISORY for 80-89 range."""
    assert OUTPUT1_PRODUCTION_PASS_THRESHOLD == 90
    assert ADVISORY_SCORE_FLOOR == 80
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 90) == ReleaseVerdict.PASS
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 89) == ReleaseVerdict.PASS_ADVISORY
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 80) == ReleaseVerdict.PASS_ADVISORY
    assert CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, 79) == ReleaseVerdict.FAIL


def test_configuration_baseline_inventory_schema():
    """Verify baseline configuration inventory schema contains all 8 RCA records."""
    inv = build_configuration_inventory()
    assert inv["schema_version"] == "1.0.0"
    assert "canonical_sources" in inv
    assert "rca_inventory" in inv
    rca = inv["rca_inventory"]
    assert len(rca) == 8
    for i in range(1, 9):
        key = f"RCA_{i:02d}"
        matching = [k for k in rca if k.startswith(key)]
        assert len(matching) == 1, f"Missing {key} in baseline inventory"
        entry = rca[matching[0]]
        assert "intended_source" in entry
        assert "status" in entry
        assert "target_remediation_wave" in entry
