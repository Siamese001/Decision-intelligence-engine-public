"""Adversarial anti-spoofing tests for Wave 5 Unmasked Audit Engine Hardening.

Verifies:
1. tools/lint_e2e_runtime_authenticity.py rejects unused decoy production imports (`import storage`)
   and unused `import subprocess` when reading `reports/*.json` or `.py` source files.
2. tools/lint_e2e_runtime_authenticity.py flags mocking of newly protected CORE_ENTRY_POINTS
   (`CanonicalAgenticPipeline`, `PreSynthesisEntityResolver`, `AssertionEligibilityAuditor`, `GroundedDeliveryGuard`).
3. tools/audit_testing_gaps.py separates comment-only keyword spoofing (`# delta sqlite`) from
   real executable AST assertions (`detect_comment_only_or_unused_import_spoofing`) and exposes
   Dimension 9 (`tooling_git_hooks`) in `EXTENDED_DIMENSION_DEFINITIONS`.
4. tools/mutation_score_engine.py suppresses arid AST logging/print mutation sites while retaining
   productive business logic mutation sites.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.audit_testing_gaps import (
    EXTENDED_DIMENSION_DEFINITIONS,
    detect_comment_only_or_unused_import_spoofing,
)
from tools.lint_e2e_runtime_authenticity import E2ERuntimeAuthenticityAuditor
from tools.mutation_score_engine import count_mutation_sites, is_arid_call_node


def test_e2e_auditor_rejects_unused_decoy_production_import_on_receipt_read() -> None:
    spoofed_e2e_code = """
import json
from pathlib import Path
import storage  # Decoy unused import attempting to bypass ERR_E2E_RECEIPT_ONLY

def test_fake_e2e_receipt_check():
    data = json.loads(Path("reports/some_receipt.json").read_text())
    assert data["status"] == "PASS"
"""
    tree = ast.parse(spoofed_e2e_code)
    auditor = E2ERuntimeAuthenticityAuditor(
        "tests/end_to_end/test_spoofed_e2e.py", spoofed_e2e_code, tree
    )
    violations = auditor.audit()
    codes = {v[1] for v in violations}
    assert "ERR_E2E_RECEIPT_ONLY" in codes


def test_e2e_auditor_rejects_unused_subprocess_import_on_source_grep() -> None:
    spoofed_grep_code = """
import subprocess  # Unused subprocess import
from pathlib import Path

def test_fake_source_grep_e2e():
    src = Path("dual_pipeline/some_module.py").read_text()
    assert "def run" in src
"""
    tree = ast.parse(spoofed_grep_code)
    auditor = E2ERuntimeAuthenticityAuditor(
        "tests/end_to_end/test_spoofed_grep_e2e.py", spoofed_grep_code, tree
    )
    violations = auditor.audit()
    codes = {v[1] for v in violations}
    assert "ERR_E2E_SOURCE_GREP_ONLY" in codes


def test_e2e_auditor_flags_mocking_expanded_core_entrypoints() -> None:
    mocked_core_code = """
from unittest.mock import patch
from dual_pipeline.grounding.delivery_boundary import GroundedDeliveryGuard

def test_mocked_delivery_guard_e2e():
    with patch("dual_pipeline.grounding.delivery_boundary.GroundedDeliveryGuard") as mock_guard:
        assert mock_guard is not None
"""
    tree = ast.parse(mocked_core_code)
    auditor = E2ERuntimeAuthenticityAuditor(
        "tests/end_to_end/test_mocked_core_e2e.py", mocked_core_code, tree
    )
    violations = auditor.audit()
    codes = {v[1] for v in violations}
    assert "ERR_E2E_CORE_STUBBED_MOCK" in codes


def test_audit_testing_gaps_detects_comment_only_spoofing_and_includes_d9() -> None:
    assert "tooling_git_hooks" in EXTENDED_DIMENSION_DEFINITIONS
    assert EXTENDED_DIMENSION_DEFINITIONS["tooling_git_hooks"]["num"] == 9

    comment_spoof_code = """
# delta sqlite node evidence_item assertion dense_vector
import dual_pipeline.query_core.lineage_db as lineage_db

def test_empty_comment_spoof():
    assert 1 == 1
"""
    res = detect_comment_only_or_unused_import_spoofing(comment_spoof_code)
    assert res["has_unused_prod_import"] is True
    assert res["has_executable_14_table_assertion"] is False

    authentic_code = """
import dual_pipeline.query_core.lineage_db as lineage_db

def test_real_table_and_call():
    badge = lineage_db.humanize_token("node_person")
    assert "node" in badge.lower() or "assertion" in "assertion_table"
"""
    res_auth = detect_comment_only_or_unused_import_spoofing(authentic_code)
    assert res_auth["has_unused_prod_import"] is False
    assert res_auth["has_partial_decoy_import"] is False
    assert res_auth["is_zero_assertion_test"] is False
    assert res_auth["has_executable_14_table_assertion"] is True


def test_audit_testing_gaps_detects_partial_decoy_import_and_zero_assertion_test() -> None:
    mixed_decoy_code = """
import dual_pipeline.query_core.lineage_db as lineage_db
import dual_pipeline.sanitizer as decoy_sanitizer

def test_mixed_decoy_spoof():
    badge = lineage_db.humanize_token("node_person")
    assert "node" in badge.lower()
"""
    res_mixed = detect_comment_only_or_unused_import_spoofing(mixed_decoy_code)
    assert res_mixed["has_unused_prod_import"] is False
    assert res_mixed["has_partial_decoy_import"] is True
    assert "decoy_sanitizer" in res_mixed["unused_prod_symbols"]
    assert res_mixed["is_zero_assertion_test"] is False

    zero_assert_code = """
import dual_pipeline.query_core.lineage_db as lineage_db

def test_no_assertions_at_all():
    lineage_db.humanize_token("node_person")
"""
    res_zero = detect_comment_only_or_unused_import_spoofing(zero_assert_code)
    assert res_zero["is_zero_assertion_test"] is True
    assert res_zero["assert_count"] == 0


def test_mutation_score_engine_filters_arid_logging_and_print_nodes() -> None:
    sample_module = """
import logging
logger = logging.getLogger(__name__)

def compute_margin(top: float, second: float) -> bool:
    logger.info("Evaluating margin: %s vs %s (debug=%s)", top + 1, second - 1, True)
    print(top == second, False)
    return (top - second) >= 0.15
"""
    # Without arid filtering: logger.info has top+1, second-1, True (3); print has top==second, False (2); return has top-second, >= (2) => 7
    raw_sites = count_mutation_sites(sample_module, filter_arid=False)
    filtered_sites = count_mutation_sites(sample_module, filter_arid=True)
    assert raw_sites == 7
    assert filtered_sites == 2
