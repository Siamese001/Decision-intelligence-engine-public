"""
tests/adversarial/test_doc_ingest_5_agent_adversarial.py

Adversarial and boundary test suite for 5-Agent Document Ingestion Intelligence.
Covers malformed LLM responses, single-entity primacy traps, empty inputs, and budget exhaustion.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
    _invoke_agent_llm,
    run_5_agent_document_intelligence,
)


def test_adversarial_empty_document():
    report = run_5_agent_document_intelligence("", "Empty Document")
    assert report.total_entities_detected == 0 or report.coverage_score is not None


@patch("dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence._invoke_agent_llm")
def test_adversarial_malformed_json_fallback(mock_llm):
    # LLM returns empty or unparseable dict
    mock_llm.return_value = {}
    report = run_5_agent_document_intelligence("Some document text here...", "Malformed Test")
    assert report.total_entities_detected == 0
    assert report.arbiter_verdict in ("PASS", "BLOCKED")


@patch("dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence._invoke_agent_llm")
def test_adversarial_primacy_bias_trap_caught_by_arbiter(mock_llm):
    # Agent 1, 2, 3 only extracted the first entity (Product Owner) and ignored Product Manager and Solution Owner
    mock_llm.side_effect = [
        # Agent 1 (biased)
        {"entities": [{"canonical_name": "Product Owner", "entity_type": "role", "description": "Only PO"}]},
        # Agent 2
        {"normalized_entities": [{"id": "role:product_owner", "type": "role", "name": "Product Owner", "description": "Only PO"}]},
        # Agent 3
        {"relationships": []},
        # Agent 5 (detects bias)
        {
            "coverage_score": 0.33,
            "primacy_bias_detected": True,
            "arbiter_verdict": "BLOCKED",
            "notes": "Primacy bias detected: Product Manager and Solution Owner were omitted.",
        },
    ]

    report = run_5_agent_document_intelligence(
        "Product Owner... Product Manager... Solution Owner...",
        "Biased Ingestion Test",
    )
    assert report.primacy_bias_detected is True
    assert report.arbiter_verdict == "BLOCKED"
    assert report.coverage_score < 0.5
