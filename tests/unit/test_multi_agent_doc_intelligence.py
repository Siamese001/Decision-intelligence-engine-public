"""
tests/unit/test_multi_agent_doc_intelligence.py

Unit and mock verification for the 5-Agent Document Intelligence Panel.
Verifies anti-primacy decomposition, role typing, and coverage auditing.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    DecomposedEntity,
    MultiAgentDocumentIntelligenceReport,
    _is_meeting_summary_doc,
    build_intelligence_prior_context,
    run_5_agent_document_intelligence,
)


def test_decomposed_entity_dataclass():
    ent = DecomposedEntity(
        entity_id="role:product_owner",
        canonical_name="Product Owner",
        entity_type="role",
        description="Defines and prioritizes backlog.",
        strategic_horizon="tactical",
    )
    assert ent.entity_id == "role:product_owner"
    assert ent.strategic_horizon == "tactical"


def test_build_intelligence_prior_context():
    report = MultiAgentDocumentIntelligenceReport(
        document_title="Agile Roles",
        total_entities_detected=3,
        entities=[
            {"id": "role:product_owner", "type": "role", "name": "Product Owner", "description": "Tactical backlog lead."},
            {"id": "role:product_manager", "type": "role", "name": "Product Manager", "description": "Strategic roadmap lead."},
            {"id": "role:solution_owner", "type": "role", "name": "Solution Owner", "description": "PEM coaching and delivery."},
        ],
        relationships=[
            {"from_id": "role:product_owner", "predicate": "COLLABORATES_WITH", "to_id": "role:product_manager", "relationship_context": "Strategic to tactical handoff."},
        ],
        coverage_score=1.0,
        primacy_bias_detected=False,
        arbiter_verdict="PASS",
        arbiter_notes="Full coverage confirmed.",
    )
    context = build_intelligence_prior_context(report)
    assert "role:product_owner" in context
    assert "role:product_manager" in context
    assert "role:solution_owner" in context
    assert "- **Anti-Primacy Certification:** CERTIFIED" in context
    assert "Do not drop secondary roles" in context


@patch("dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence._invoke_agent_llm")
def test_run_5_agent_document_intelligence(mock_llm):
    # Mock responses for Agent 1, Agent 2, Agent 3, Agent 5
    mock_llm.side_effect = [
        # Agent 1
        {
            "entities": [
                {"canonical_name": "Product Owner", "entity_type": "role", "description": "Backlog owner", "strategic_horizon": "tactical"},
                {"canonical_name": "Product Manager", "entity_type": "role", "description": "Strategy lead", "strategic_horizon": "strategic"},
                {"canonical_name": "Solution Owner", "entity_type": "role", "description": "PEM coach", "strategic_horizon": "hybrid"},
            ]
        },
        # Agent 2
        {
            "normalized_entities": [
                {"id": "role:product_owner", "type": "role", "name": "Product Owner", "description": "Backlog owner"},
                {"id": "role:product_manager", "type": "role", "name": "Product Manager", "description": "Strategy lead"},
                {"id": "role:solution_owner", "type": "role", "name": "Solution Owner", "description": "PEM coach"},
            ]
        },
        # Agent 3
        {
            "relationships": [
                {
                    "from_id": "role:product_owner",
                    "predicate": "COLLABORATES_WITH",
                    "to_id": "role:product_manager",
                    "relationship_context": "Handoff between Epics and Stories",
                    "evidence_text": "Product Managers define Epics while Product Owners build Stories",
                }
            ]
        },
        # Agent 5
        {
            "coverage_score": 1.0,
            "primacy_bias_detected": False,
            "arbiter_verdict": "PASS",
            "notes": "All 3 roles represented cleanly.",
        },
    ]

    doc_text = "Role Description: Product Owner... Relationship to Other Roles: Product Manager, Solution Owner..."
    report = run_5_agent_document_intelligence(doc_text, "Agile Roles")

    assert report.total_entities_detected == 3
    assert report.coverage_score == 1.0
    assert not report.primacy_bias_detected
    assert report.arbiter_verdict == "PASS"
    assert len(report.entities) == 3
    assert len(report.relationships) == 1


def test_is_meeting_summary_doc_generic_metadata():
    # Explicit enable overrides everything
    assert _is_meeting_summary_doc("Some text", {"enable_5_agent_intelligence": True, "is_meeting_summary": True}) is False

    # Explicit disable
    assert _is_meeting_summary_doc("Some text", {"enable_5_agent_intelligence": False}) is True

    # Generic metadata flags
    assert _is_meeting_summary_doc("Some text", {"is_meeting_summary": True}) is True
    assert _is_meeting_summary_doc("Some text", {"is_meeting_notes": True}) is True
    assert _is_meeting_summary_doc("Some text", {"document_modality": "meeting_summary"}) is True
    assert _is_meeting_summary_doc("Some text", {"source_type": "meeting_notes"}) is True

    # Standard doc with no flags
    assert _is_meeting_summary_doc("Some standard specification", {}) is False

