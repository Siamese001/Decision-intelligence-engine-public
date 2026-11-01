"""Adversarial edge-case tests for Wave 3: Deterministic Orphan Auto-Connector."""
from __future__ import annotations

import copy

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    _compute_degrees,
    heal_disconnected_document_entities,
)
from dual_pipeline.document_pipeline_core.executor import (
    _quarantine_unsubstantiated_integrations_dict,
    validate_attachment_assertion_provenance,
)


def test_boundary_empty_and_single_entity_projections() -> None:
    assert heal_disconnected_document_entities({}) == []
    assert heal_disconnected_document_entities({"graph_mutations": None}) == []
    single = {
        "graph_mutations": {
            "entity_upserts": [{"id": "system:only-one", "type": "system", "name": "Only One"}],
            "relationship_signals": [],
        }
    }
    assert heal_disconnected_document_entities(single) == []
    assert single["graph_mutations"]["relationship_signals"] == []


def test_idempotency_on_repeated_healing() -> None:
    proj = {
        "evidence_index": {
            "ev-1": {"evidence_id": "ev-1", "text": "FORGE lifecycle stages", "locator": "p1"},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:forge", "type": "system", "name": "FORGE", "evidence_ids": ["ev-1"]},
                {"id": "workstream:prospecting", "type": "workstream", "name": "Prospecting", "evidence_ids": ["ev-1"]},
                {"id": "metric:forge-roi", "type": "metric", "name": "FORGE ROI", "evidence_ids": ["ev-1"]},
                {"id": "policy:forge-auth", "type": "policy", "name": "FORGE Auth", "evidence_ids": ["ev-1"]},
                {"id": "deliverable:forge-mvp", "type": "deliverable", "name": "FORGE MVP", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [],
        },
    }
    c1 = heal_disconnected_document_entities(proj, {"slug": "forge"})
    assert len(c1) == 4
    snapshot = copy.deepcopy(proj["graph_mutations"]["relationship_signals"])
    c2 = heal_disconnected_document_entities(proj, {"slug": "forge"})
    assert c2 == []
    assert proj["graph_mutations"]["relationship_signals"] == snapshot


def test_synthesized_edges_survive_integration_quarantine_and_provenance_validation() -> None:
    proj = {
        "evidence_index": {
            "ev-1": {
                "evidence_id": "ev-1",
                "text": "Simple text without any integration keyword at all.",
                "exact_text": "Simple text without any integration keyword at all.",
                "locator": "p1",
                "source_file": "raw_doc.pdf",
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "project:alpha", "type": "project", "name": "Alpha", "evidence_ids": ["ev-1"]},
                {"id": "system:beta", "type": "system", "name": "Beta", "evidence_ids": ["ev-1"]},
                {"id": "person:jane-doe", "type": "person", "name": "Jane Doe", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [],
        },
    }
    heal_disconnected_document_entities(proj, {"project": "alpha"})
    quarantined = _quarantine_unsubstantiated_integrations_dict(proj)
    deg, _ = _compute_degrees(
        quarantined["graph_mutations"]["entity_upserts"],
        quarantined["graph_mutations"]["relationship_signals"],
    )
    assert all(v >= 1 for v in deg.values())
    validate_attachment_assertion_provenance(
        quarantined,
        {"source_manifest": {"sources": [{"filename": "raw_doc.pdf", "is_summary": False}]}},
    )
