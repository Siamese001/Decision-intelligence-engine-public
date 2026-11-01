"""End-to-End and Adversarial Tests for PR Review Runtime Acceleration (Wave 2).

Validates:
1. Deliverable path classification (output/**, documents/**) into Lane 1.
2. Proportional test module scoping for deliverables without full_suite fallback.
3. Inclusion of Gate 30 (Grounded Delivery & Claim Manifest) in Lane 1 required stages.
4. Scoped linter accuracy and fail-closed detection for purity, hierarchy, and mock boundaries.
5. Synthetic embedding scan exclusions for .local cache and qualification directories.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.change_lane_classifier import classify_changes, classify_file, load_policy
from tools.lint_mock_boundaries import audit_file
from tools.lint_production_purity import lint_output_directory_purity
from tools.readiness_planner import (
    SAFE_NON_CODE_PREFIXES,
    filter_impacted_test_modules,
    load_gate_manifest,
    plan_readiness,
)
from tools.validate_no_synthetic_embeddings import scan as scan_synthetic_embeddings
from tools.validate_output_hierarchy import validate_target_files_output_hierarchy


def test_deliverables_classified_as_lane_1_medium_risk() -> None:
    """Verifies output/** and documents/** classify into Lane 1, not escalating to Lane 2."""
    policy = load_policy(ROOT / "config" / "governance" / "risk_lanes_policy.json")

    deliverable_files = [
        "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master/01_transcript.md",
        "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master/02_brief.md",
        "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master/03_projections.md",
        "documents/partner_advisory_solutions_catalog_2026.pdf",
    ]

    for f in deliverable_files:
        lane, reason = classify_file(f, policy)
        assert lane == 1, f"Expected Lane 1 for '{f}', got Lane {lane} ({reason})"

    res = classify_changes(deliverable_files, policy_path=ROOT / "config" / "governance" / "risk_lanes_policy.json")
    assert res["effective_lane"] == 1
    assert res["effective_lane_name"] == "LANE_1_MEDIUM_RISK"
    assert not res["touches_lane_2"]


def test_gate_30_included_in_lane_1_required_stages() -> None:
    """Verifies Gate 30 (Grounded Delivery) is required in Lane 1 to protect deliverable integrity."""
    manifest = load_gate_manifest(repo_root=ROOT)
    lane_1_req = manifest["lane_profiles"]["1"]["required_stage_indices"]
    assert 30 in lane_1_req, "Gate 30 must be required in Lane 1"


def test_deliverables_impacted_test_filtering_avoids_full_suite() -> None:
    """Verifies that deliverables in output/ and documents/ select only smoke tests, not full suite."""
    deliverable_files = [
        "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master/01_transcript.md",
        "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master/metadata.json",
        "documents/partner_advisory_catalog.pdf",
    ]

    for prefix in ("output/", "documents/", "documentation/"):
        assert prefix in SAFE_NON_CODE_PREFIXES

    base_modules = [f"tests.test_module_{i}" for i in range(27)]
    selected = filter_impacted_test_modules(
        target_files=deliverable_files,
        test_mode="impacted_only",
        base_modules=base_modules,
    )
    # Should only select smoke modules (3 modules), NOT all 27 modules
    assert len(selected) == 3
    assert "tests.test_runtime_evidence_claims" in selected
    assert "tests.test_unified_agent_architecture" in selected
    assert "tests.test_credentials" in selected


def test_scoped_output_hierarchy_validation(tmp_path: Path) -> None:
    """Verifies target_files scoping in validate_output_hierarchy passes on valid and fails on malformed."""
    # 1. Non-output target files produce 0 output violations
    assert not validate_target_files_output_hierarchy(ROOT / "output", ["tools/readiness_planner.py"])

    # 2. Conforming production meeting container produces 0 violations
    prod_file = "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master/03_PartnerAdvisory_Solutions_Catalog_2026_Master_10-14-2026_0857AM_ET_canonical_signal_projection.json"
    if (ROOT / prod_file).is_file():
        violations = validate_target_files_output_hierarchy(ROOT / "output", [prod_file])
        assert not violations

    # 3. Malformed date or prohibited draft container fails closed
    out_dir = tmp_path / "output"
    bad_meeting = out_dir / "enterprise_account_planning" / "2026-10-14" / "Session"  # Wrong date format (YYYY-MM-DD instead of MM-DD-YYYY)
    bad_meeting.mkdir(parents=True)
    bad_file = bad_meeting / "01_transcript.md"
    bad_file.write_text("# Test", encoding="utf-8")

    bad_violations = validate_target_files_output_hierarchy(out_dir, [str(bad_file)])
    assert bad_violations
    assert any("INVALID_DATE_FORMAT" in v for v in bad_violations)


def test_scoped_mock_boundary_linter_fail_closed(tmp_path: Path) -> None:
    """Verifies mock boundary linter detects banned mock kwargs in target files."""
    clean_py = tmp_path / "clean_module.py"
    clean_py.write_text("def run(): return 42\n", encoding="utf-8")
    assert not audit_file(clean_py)

    bad_py = tmp_path / "bad_module.py"
    bad_py.write_text("def run(): return fetch(allow_mock=True)\n", encoding="utf-8")
    violations = audit_file(bad_py)
    assert violations
    assert any("allow_mock=True" in v for v in violations)


def test_synthetic_embeddings_scanner_excludes_local_caches() -> None:
    """Verifies synthetic embeddings scanner excludes .local/coverage_qualification and readiness_cache."""
    res = scan_synthetic_embeddings(ROOT, staged=False)
    assert res["status"] == "PASS"
    assert res["match_count"] == 0
