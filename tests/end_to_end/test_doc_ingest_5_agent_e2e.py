"""
tests/end_to_end/test_doc_ingest_5_agent_e2e.py

End-to-end verification of 5-Agent Document Ingestion Intelligence.
Validates multi-role decomposition, anti-primacy prior context propagation,
and end-to-end integration with DocumentPipeline in an isolated ephemeral sandbox.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    MultiAgentDocumentIntelligenceReport,
    build_intelligence_prior_context,
)
from dual_pipeline.graph_scout_core.pipeline_hook import ScoutHookResult


@pytest.fixture
def sample_agile_roles_doc(tmp_path: Path) -> Path:
    p = tmp_path / "agile_delivery_roles.md"
    content = (
        "# Agile Delivery Roles: Product Owner, Product Manager, Solution Owner\n\n"
        "## Product Owner\n"
        "A Product Owner determines the best way to build customer solutions within an agile team.\n"
        "Responsible for backlog prioritization, user stories, and acceptance.\n\n"
        "## Relationship to Other Roles\n"
        "- Product Manager: Focuses on strategic 1-5 year roadmap and Epic requirements.\n"
        "- Solution Owner: Blends Scrum Master and Agile Coach skills in a PEM environment.\n\n"
        "## Core Skills\n"
        "- Agile Methodologies\n"
        "- Hypothesis-driven Research\n"
    )
    p.write_text(content, encoding="utf-8")
    return p


def test_document_pipeline_5_agent_integration(
    sample_agile_roles_doc: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_SANDBOX_DIR", str(tmp_path / "sandbox"))
    pipeline = DocumentPipeline(provider_type="offline")

    mock_report = MultiAgentDocumentIntelligenceReport(
        document_title="Agile Delivery Roles",
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
        arbiter_notes="Decomposed all 3 roles without primacy bias.",
    )

    out_dir = tmp_path / "output"
    metadata = {
        "project": "nea",
        "title": "Agile Delivery Roles",
        "date": "10/25/2026",
        "author": "Practice Lead",
        "enforce_worktree": False,
        "allow_duplicate": True,
        "test_fast": True,
    }

    captured_prior_contexts: list[str] = []

    def _spy_build_prior_context(report: MultiAgentDocumentIntelligenceReport) -> str:
        ctx = build_intelligence_prior_context(report)
        captured_prior_contexts.append(ctx)
        return ctx

    with patch("dual_pipeline.canonical_pipeline.pipeline.ROOT", tmp_path), patch(
        "dual_pipeline.canonical_pipeline.concurrency.ROOT", tmp_path
    ), patch("dual_pipeline.canonical_pipeline.pipeline.DeadLetterQueueManager"), patch(
        "dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook",
        return_value=ScoutHookResult(skipped_reason="test isolation"),
    ), patch(
        "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence.run_5_agent_document_intelligence",
        return_value=mock_report,
    ), patch(
        "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence.build_intelligence_prior_context",
        side_effect=_spy_build_prior_context,
    ):
        result = pipeline.run(
            input_path=sample_agile_roles_doc,
            metadata=metadata,
            output_dir=str(out_dir),
            mutate_graph=False,
            test_fast=True,
        )

    assert result["status"] in ("COMPLETE", "PASS")
    assert len(captured_prior_contexts) == 1
    prior_ctx = captured_prior_contexts[0]
    assert "5-AGENT MULTI-ENTITY DECOMPOSITION MANIFEST" in prior_ctx
    assert "role:product_owner" in prior_ctx
    assert "role:product_manager" in prior_ctx
    assert "role:solution_owner" in prior_ctx

    out_03_path = Path(result["output_03"])
    assert out_03_path.is_file()
    out_03_data = json.loads(out_03_path.read_text(encoding="utf-8"))
    assert out_03_data.get("schema_version") == "5.7"
    meta_file = out_03_path.parent / "metadata.json"
    assert meta_file.is_file()
    saved_meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert saved_meta.get("5_agent_intelligence_report", {}).get("total_entities") == 3
    assert saved_meta.get("5_agent_intelligence_report", {}).get("coverage_score") == 1.0
    assert saved_meta.get("5_agent_intelligence_report", {}).get("primacy_bias_detected") is False
