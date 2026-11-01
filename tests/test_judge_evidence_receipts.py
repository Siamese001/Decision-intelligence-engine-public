"""Unit and evidence reconstruction tests for JARVIS judge hardening Wave 1.

Verifies:
1. Reconstructed archived failure artifacts match SHA-256 and byte counts from evidence_manifest.json.
2. Archived judge scores (100/84/90) and denial of release (status FAIL).
3. Contract inventory covers all required fields, criteria, consumers, and conflict dispositions.
4. Scope adapter functions: environment validation, parent receipt chaining, and acceptance criteria.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FORENSICS_DIR = REPO_ROOT / "reports/jarvis_judge_forensics"

from tools.run_judge_hardening_wave import (
    compute_file_digest,
    compute_tree_digest,
    evaluate_acceptance,
    seal_receipt,
    validate_environment,
    validate_parent_receipt,
)


def _compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class TestArchivedEvidenceReconstruction:
    """Verifies that preserved incident artifacts match historical hashes and scores."""

    def test_evidence_manifest_integrity(self) -> None:
        manifest_path = FORENSICS_DIR / "evidence_manifest.json"
        assert manifest_path.is_file(), f"Manifest missing at {manifest_path}"

        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest_data.get("files", [])
        assert len(files) == 10, f"Expected 10 preserved files, found {len(files)}"

        for entry in files:
            snapshot_rel = entry["snapshot"]
            snapshot_path = FORENSICS_DIR / snapshot_rel
            assert snapshot_path.is_file(), f"Snapshot file missing: {snapshot_path}"

            computed_sha = _compute_sha256(snapshot_path)
            expected_sha = entry["sha256"]
            assert (
                computed_sha == expected_sha
            ), f"Hash mismatch for {snapshot_rel}: got {computed_sha}, expected {expected_sha}"

            computed_bytes = snapshot_path.stat().st_size
            expected_bytes = entry["bytes"]
            assert (
                computed_bytes == expected_bytes
            ), f"Byte size mismatch for {snapshot_rel}: got {computed_bytes}, expected {expected_bytes}"

    def test_archived_judge_scores_and_verdicts(self) -> None:
        eval_path = FORENSICS_DIR / "evidence/llm_judge_evaluation.json"
        assert eval_path.is_file(), "Judge evaluation evidence missing"

        eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
        assert eval_data.get("status") == "FAIL"
        assert eval_data.get("overall_verdict") == "FAIL"

        out1 = eval_data.get("output_1_evaluation", {})
        assert out1.get("composite_score") == 100
        assert out1.get("verdict") == "PASS"

        out2 = eval_data.get("output_2_evaluation", {})
        assert out2.get("composite_score") == 84
        assert out2.get("verdict") == "FAIL"

        sec_scores = out2.get("section_scores", {})
        assert sec_scores.get("header_and_overview") == 8
        assert sec_scores.get("key_updates_technical_substance") == 29
        assert sec_scores.get("conditional_extensions") == 20
        assert sec_scores.get("appendix_stakeholder_perspectives") == 27
        assert sum(sec_scores.values()) == 84

        dim_scores = [
            out2.get("dimension_1_executive_altitude"),
            out2.get("dimension_2_technical_substance"),
            out2.get("dimension_3_strategic_synthesis"),
            out2.get("dimension_4_scoping_and_modality"),
        ]
        assert sum(dim_scores) == 85

        out3 = eval_data.get("output_3_evaluation", {})
        assert out3.get("composite_score") == 90
        assert out3.get("verdict") == "PASS"

    def test_archived_final_release_decision_denial(self) -> None:
        decision_path = FORENSICS_DIR / "evidence/final_release_decision.json"
        assert decision_path.is_file(), "Final release decision evidence missing"

        decision_data = json.loads(decision_path.read_text(encoding="utf-8"))
        assert decision_data.get("output_1_pass") is True
        assert decision_data.get("output_2_pass") is False
        assert decision_data.get("output_3_pass") is True
        assert decision_data.get("final_pass") is False
        assert decision_data.get("authoritative_write_authorized") is False
        assert decision_data.get("status") == "FAIL"

    def test_unknown_manifest_digests_preserved_honestly(self) -> None:
        for ckpt in ("output_1.ckpt.md.manifest.json", "output_2.ckpt.md.manifest.json", "output_3.ckpt.json.manifest.json"):
            manifest_path = FORENSICS_DIR / "evidence" / ckpt
            assert manifest_path.is_file()
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert data.get("prompt_digest") is None
            assert data.get("model_digest") is None


class TestContractInventory:
    """Verifies that contract_inventory.json conforms to governance requirements."""

    @pytest.fixture
    def inventory_data(self) -> Dict[str, Any]:
        inv_path = FORENSICS_DIR / "contract_inventory.json"
        assert inv_path.is_file(), f"contract_inventory.json missing at {inv_path}"
        return json.loads(inv_path.read_text(encoding="utf-8"))

    def test_inventory_structure_and_coverage(self, inventory_data: Dict[str, Any]) -> None:
        assert "criteria" in inventory_data
        assert "consumers_registry" in inventory_data
        assert "archived_failure_binding" in inventory_data

        criteria = inventory_data["criteria"]
        assert len(criteria) >= 15, f"Expected >= 15 criteria, found {len(criteria)}"

        required_keys = {
            "criterion_id",
            "title",
            "authority",
            "applicability",
            "dependencies",
            "consumers",
            "score_effect",
            "repair_target",
            "reachability",
            "conflict_disposition",
        }
        valid_reachabilities = {"REACHABLE", "UNREACHABLE", "CONDITIONALLY_REACHABLE", "UNRESOLVED"}

        crit_ids = set()
        for c in criteria:
            missing = required_keys - set(c.keys())
            assert not missing, f"Criterion {c.get('criterion_id')} missing keys: {missing}"
            assert c["reachability"] in valid_reachabilities
            assert len(c["dependencies"]) > 0
            assert len(c["consumers"]) > 0
            assert len(c["conflict_disposition"]) > 10
            crit_ids.add(c["criterion_id"])

        assert len(crit_ids) == len(criteria), "Duplicate criterion_id detected"

    def test_inventory_consumers_mapped(self, inventory_data: Dict[str, Any]) -> None:
        consumers = inventory_data["consumers_registry"]
        assert len(consumers) >= 12

        for cons in consumers:
            assert "consumer_path" in cons
            assert "role" in cons
            assert "reachability" in cons
            assert "mapped_criteria" in cons
            assert len(cons["mapped_criteria"]) > 0

    def test_inventory_specific_conflicts_disposed(self, inventory_data: Dict[str, Any]) -> None:
        criteria_map = {c["criterion_id"]: c for c in inventory_data["criteria"]}

        assert "CRIT-OUT1-COLLAPSE-02" in criteria_map
        assert "85%" in criteria_map["CRIT-OUT1-COLLAPSE-02"]["conflict_disposition"]
        assert "65%" in criteria_map["CRIT-OUT1-COLLAPSE-02"]["conflict_disposition"]

        assert "CRIT-OUT2-HEADER-01" in criteria_map
        assert "ET" in criteria_map["CRIT-OUT2-HEADER-01"]["conflict_disposition"]

        assert "CRIT-OUT2-ARITHMETIC-05" in criteria_map
        assert "84" in criteria_map["CRIT-OUT2-ARITHMETIC-05"]["conflict_disposition"]
        assert "85" in criteria_map["CRIT-OUT2-ARITHMETIC-05"]["conflict_disposition"]

        assert "CRIT-OUT3-RUBRIC-DIMENSIONS-07" in criteria_map
        assert "five" in criteria_map["CRIT-OUT3-RUBRIC-DIMENSIONS-07"]["conflict_disposition"].lower()
        assert "six" in criteria_map["CRIT-OUT3-RUBRIC-DIMENSIONS-07"]["conflict_disposition"].lower()


class TestScopeAdapterUnit:
    """Verifies unit behaviors of run_judge_hardening_wave adapter."""

    def test_environment_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime_root = tmp_root / "runtime"
            receipt_root = tmp_root / "receipts"

            ok, msg = validate_environment(runtime_root, receipt_root, 1, REPO_ROOT)
            assert ok is True

            # Reject runtime_root == repo_root
            bad_ok, bad_msg = validate_environment(REPO_ROOT, receipt_root, 1, REPO_ROOT)
            assert bad_ok is False
            assert "primary repository root" in bad_msg

            # Reject runtime_root in output/
            bad_output = REPO_ROOT / "output" / "subfolder"
            bad_ok2, bad_msg2 = validate_environment(bad_output, receipt_root, 1, REPO_ROOT)
            assert bad_ok2 is False
            assert "production output" in bad_msg2

    def test_parent_receipt_chaining(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_root = Path(tmpdir)

            # Wave 1 requires no parent
            ok1, dig1, msg1 = validate_parent_receipt(receipt_root, 1)
            assert ok1 is True
            assert dig1 is None

            # Wave 2 requires wave 1 receipt
            ok2, dig2, msg2 = validate_parent_receipt(receipt_root, 2)
            assert ok2 is False
            assert "Parent receipt missing" in msg2

            # Create mock wave 1 receipt
            parent_receipt = receipt_root / "wave-1.json"
            seal_receipt({"wave": 1, "terminal_result": "COMPLETE"}, parent_receipt)

            ok2_after, dig2_after, msg2_after = validate_parent_receipt(receipt_root, 2)
            assert ok2_after is True
            assert dig2_after is not None
            assert len(dig2_after) == 64

    def test_acceptance_criteria_evaluation(self) -> None:
        # All passed
        assert evaluate_acceptance(0, 10, 10, 0, 0, 0) == []

        # Zero collected
        d_zero = evaluate_acceptance(0, 0, 0, 0, 0, 0)
        assert any("Zero tests collected" in d for d in d_zero)

        # Non-zero exit code
        d_exit = evaluate_acceptance(1, 10, 8, 2, 0, 0)
        assert any("non-zero exit code" in d for d in d_exit)
        assert any("failed" in d for d in d_exit)

        # Skips not permitted
        d_skip = evaluate_acceptance(0, 10, 9, 0, 0, 1)
        assert any("mandate 0 skips" in d for d in d_skip)
