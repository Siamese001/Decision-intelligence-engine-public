"""Unit tests for Wave 4: HAS_ROLE predicate conformance and StakeholderResolutionValidator HITL awareness."""

from dual_pipeline.transcript_pipeline_core.stakeholder_validator import (
    StakeholderResolutionValidator,
)
from dual_pipeline.transcript_qa_core.semantic_linters import lint_predicate_domain


def test_has_role_predicate_conforms():
    signals = [
        {
            "signal_id": "rel-db-dir--HAS_ROLE",
            "relationship": "HAS_ROLE",
            "from_id": "person:daniel-brankin",
            "to_id": "role:director-digital-technology",
        }
    ]
    passed, violations, details = lint_predicate_domain(signals)
    assert passed
    assert len(violations) == 0


def test_stakeholder_validator_flags_divergence_without_hitl(tmp_path):
    validator = StakeholderResolutionValidator(repo_root=tmp_path)
    o1_divergent = "Attendees:\n- Amit Ayer (Product Marketing Director)"
    o2_divergent = "Attendees:\n- Amit Ayer (Product Marketing Director)"
    o3_divergent = {"entity_upserts": [{"id": "person:amit_ayer", "name": "Amit Ayer", "role": "Product Marketing Director"}]}

    report = validator.validate_deliverables(
        output_1_text=o1_divergent,
        output_2_text=o2_divergent,
        output_3_payload=o3_divergent,
        fail_closed=False,
    )
    # Should flag GRAPHDB_SSOT_DIVERGENCE because Amit Ayer's SSOT role is Lead Enterprise Architect
    assert not report.is_consistent
    assert any(d["discrepancy_type"] == "GRAPHDB_SSOT_DIVERGENCE" for d in report.discrepancies)


def test_stakeholder_validator_bypasses_divergence_with_hitl_receipt(tmp_path):
    validator = StakeholderResolutionValidator(repo_root=tmp_path)
    o1_divergent = "Attendees:\n- Amit Ayer (Product Marketing Director)"
    o2_divergent = "Attendees:\n- Amit Ayer (Product Marketing Director)"
    o3_divergent = {"entity_upserts": [{"id": "person:amit_ayer", "name": "Amit Ayer", "role": "Product Marketing Director"}]}

    metadata_with_hitl = {
        "hitl_role_receipts": {
            "Amit Ayer": {
                "decision_type": "ROLE_CLARIFICATION",
                "resolved_role": "Product Marketing Director",
                "hitl_receipt_bound": True,
            }
        }
    }

    report = validator.validate_deliverables(
        output_1_text=o1_divergent,
        output_2_text=o2_divergent,
        output_3_payload=o3_divergent,
        fail_closed=False,
        metadata=metadata_with_hitl,
    )
    # Bypassed: should be consistent because C0.5 HITL authorized the role update
    assert report.is_consistent
    assert len(report.discrepancies) == 0
