"""
tests/unit/test_pregate_surgical_remediation.py

Unit test suite verifying pre-gate in-memory deterministic surgical remediation:
- Zero-inference healing of Gate 3.14 evidence citations lacking token overlap.
- Proposal ID and id synchronization.
- Conversational filler ('you know', 'um', 'uh') and double comma (',,') sanitization in < 1ms.
- High-velocity deterministic QA gate evaluation (< 1.0s) across all 3 outputs.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_qa_core.evaluator import evaluate_all_qa_gates
from dual_pipeline.transcript_qa_core.gate3_evidence import check_gate_3_14
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
from dual_pipeline.transcript_qa_core.graph_ontology_proposals import (
    _sanitize_filler_and_punct,
    heal_proposals_and_risks,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer


@pytest.fixture
def mock_output3_with_stray_citation() -> Dict[str, Any]:
    """Returns a mock Output 03 payload with ungrounded ev-03 and grounded ev-02, ev-08."""
    return {
        "metadata": {
            "title": "Infrastructure Automation Orals",
            "date": "10/02/2026",
            "meeting_type": "technical_review",
        },
        "evidence_index": {
            "ev-02": {
                "statement_id": "ev-02",
                "speaker": "person:daniel-brankin",
                "text": "We will deploy golden dataset validation to verify data pipelines.",
                "exact_text": "golden dataset validation for pipeline correctness",
                "locator": {"kind": "timestamp", "value": "00:05:12"},
            },
            "ev-03": {
                "statement_id": "ev-03",
                "speaker": "person:unrelated",
                "text": "The catering menu was delivered for yesterday lunch break.",
                "exact_text": "catering lunch break menu",
                "locator": {"kind": "timestamp", "value": "00:20:00"},
            },
            "ev-08": {
                "statement_id": "ev-08",
                "speaker": "person:daniel-brankin",
                "text": "Model extraction accuracy is measured against baseline golden records.",
                "exact_text": "model extraction accuracy measured against baseline golden",
                "locator": {"kind": "timestamp", "value": "00:35:40"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:golden-dataset",
                    "name": "Golden Dataset",
                    "type": "system",
                    "evidence_ids": ["ev-02"],
                }
            ],
            "proposal_updates": [
                {
                    "id": "proposal:golden-dataset-validation",
                    "name": "Golden Dataset Validation",
                    "description": "Expand golden dataset validation to verify data pipelines and model extraction accuracy.",
                    "evidence_ids": ["ev-02", "ev-03", "ev-08"],
                }
            ],
        },
    }


def test_gate_3_14_prunes_stray_citations_in_memory(mock_output3_with_stray_citation: Dict[str, Any]) -> None:
    """Verifies that stray citations lacking token overlap are pruned in memory before Gate 3.14."""
    data = mock_output3_with_stray_citation
    changes: list[str] = []

    # Run deterministic graph healing
    healed_data = heal_graph_projection(data, changes)
    props = healed_data["graph_mutations"]["proposal_updates"]
    assert len(props) >= 1

    target_prop = next(p for p in props if "golden-dataset" in str(p.get("id", "")))
    # Independent verification: ev-03 is pruned, ev-02 and ev-08 remain
    assert "ev-03" not in target_prop["evidence_ids"], "Stray evidence citation 'ev-03' was not pruned"
    assert "ev-02" in target_prop["evidence_ids"], "Valid evidence 'ev-02' was incorrectly pruned"
    assert "ev-08" in target_prop["evidence_ids"], "Valid evidence 'ev-08' was incorrectly pruned"

    # Verify Gate 3.14 evaluates clean
    passed, failures, details = check_gate_3_14(healed_data)
    assert passed is True, f"Gate 3.14 failed after healing: {failures}"
    assert details["topical_overlap"] is True
    assert not any("ev-03" in f for f in failures)


def test_proposal_id_synchronization(mock_output3_with_stray_citation: Dict[str, Any]) -> None:
    """Verifies that proposal_id and id are synchronized across proposal entries."""
    data = mock_output3_with_stray_citation
    # Create proposal with missing proposal_id
    prop = data["graph_mutations"]["proposal_updates"][0]
    prop.pop("proposal_id", None)
    prop["id"] = "proposal:golden-dataset-validation"

    changes: list[str] = []
    heal_proposals_and_risks(data, data["graph_mutations"], changes)

    assert prop.get("proposal_id") == "proposal:golden-dataset-validation"
    assert prop.get("id") == "proposal:golden-dataset-validation"


def test_conversational_filler_and_double_comma_sanitization() -> None:
    """Verifies that conversational filler ('you know', 'um') and double commas sanitize in < 1ms."""
    raw_desc = "We will, you know,, deploy the automated verification pipeline,, um across environments."
    raw_name = "Automation Pipeline,, you know"

    t0 = time.perf_counter()
    clean_desc = _sanitize_filler_and_punct(raw_desc)
    clean_name = _sanitize_filler_and_punct(raw_name)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 1.0, f"Sanitization took {elapsed_ms:.3f}ms, expected < 1.0ms"
    assert "you know" not in clean_desc.lower()
    assert ",," not in clean_desc
    assert clean_desc == "We will, deploy the automated verification pipeline, across environments."
    assert clean_name == "Automation Pipeline"


def test_all_29_deterministic_qa_gates_execution_speed(mock_output3_with_stray_citation: Dict[str, Any]) -> None:
    """Verifies all deterministic QA gates across 3 outputs execute in < 1.0s."""
    f1_text = (
        "**Date:** 10/02/2026\n"
        "**Attendees:** Daniel Brankin, Amit Ayer\n\n"
        "[00:00:10] **Daniel Brankin:** Welcome everyone to the infrastructure automation review.\n"
        "[00:05:12] **Daniel Brankin:** We will deploy golden dataset validation to verify data pipelines.\n"
        "[00:35:40] **Daniel Brankin:** Model extraction accuracy is measured against baseline golden records.\n"
    )
    f2_text = (
        "# Executive Meeting Recap: Infrastructure Automation Review\n\n"
        "**Meeting Date:** 10/02/2026\n"
        "**Attendees:** Daniel Brankin, Amit Ayer\n\n"
        "## Executive Summary\n"
        "The team conducted a technical review of the infrastructure automation platform, focusing on golden "
        "dataset deployment and validation across pipeline services.\n\n"
        "## Key Decisions\n"
        "- Deploy golden dataset validation as the primary gate.\n\n"
        "## Strategic Context\n"
        "The initiative ensures data pipeline correctness and model extraction fidelity.\n"
    )
    f3_text = json.dumps(mock_output3_with_stray_citation, indent=2)

    meta = {
        "meeting_date": "10/02/2026",
        "date": "10/02/2026",
        "attendees": ["Daniel Brankin", "Amit Ayer"],
        "meeting_title": "Infrastructure Automation Review",
        "auto_heal": True,
    }

    t0 = time.perf_counter()
    report = evaluate_all_qa_gates(
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=f3_text,
        metadata=meta,
        auto_heal=True,
    )
    elapsed_s = time.perf_counter() - t0

    assert elapsed_s < 1.0, f"QA evaluation took {elapsed_s:.3f}s (budget: < 1.0s)"
    assert "deterministic_healing" in report
    assert "healed_artifacts" in report


def test_deterministic_healer_mesh_output3_heals_cleanly(mock_output3_with_stray_citation: Dict[str, Any]) -> None:
    """Verifies DeterministicHealer.heal_output_3 prunes ungrounded citations and records audit logs."""
    raw_json = json.dumps(mock_output3_with_stray_citation, indent=2)
    meta = {"meeting_date": "10/02/2026"}

    healed_json, changes = DeterministicHealer.heal_output_3(raw_json, metadata=meta)
    healed_dict = json.loads(healed_json)

    target_prop = healed_dict["graph_mutations"]["proposal_updates"][0]
    assert "ev-03" not in target_prop["evidence_ids"]
    assert "ev-02" in target_prop["evidence_ids"]
    assert any("Pruned unrelated evidence citation 'ev-03'" in c for c in changes)
