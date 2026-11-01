"""End-to-End Tests for Output 3 Fail-Closed Boundaries at Extraction and Promotion.

Verifies:
1. Document promotion failure fails closed without falling back to un-gated raw synthesis.
2. Visual provider exhaustion yields typed failed outcomes with zero graph facts.
3. Empty but valid extractions remain distinct from failures and pass gating cleanly.
4. Downstream Gate 3 and judging remain preserved on successful document extraction.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.attachment_core.image_extractor import extract_image_visual
from dual_pipeline.document_pipeline_core.executor import (
    DocumentGraphExtractorExecutor,
    _post_process_document_projection,
)
from dual_pipeline.transcript_stages_core.models import L2SynthesisProvider


class DummyMockProvider(L2SynthesisProvider):
    """Mock L2 provider returning configurable graph responses."""

    def __init__(self, response_text: str = "") -> None:
        self.response_text = response_text

    def synthesize_recap(self, prompt: str, user_content: str) -> str:
        return ""

    def synthesize_graph(self, prompt: str, user_content: str) -> str:
        return self.response_text


def test_document_promotion_failure_fails_closed_no_raw_fallback() -> None:
    """Verify malformed or invalid candidate projection raises ValueError and does NOT fall back."""
    malformed_raw_json = "NOT_VALID_JSON_CANNOT_PARSE"
    metadata = {"project": "project_lens", "date": "10-06-2026"}
    document_text = "Statement of work content with requirements."

    # Must raise ValueError, NOT catch and fall back to raw synthesis
    with pytest.raises(ValueError, match="valid JSON object|invalid raw projection JSON"):
        _post_process_document_projection(
            raw_graph_json=malformed_raw_json,
            metadata=metadata,
            document_text=document_text,
        )


def test_document_executor_fails_closed_on_synthesis_promotion_error() -> None:
    """Verify DocumentGraphExtractorExecutor.execute fails closed when promotion fails."""
    provider = DummyMockProvider(response_text="INVALID_SYNTHESIS_PAYLOAD_NOT_JSON")
    executor = DocumentGraphExtractorExecutor(provider=provider)

    pa_context = {"metadata": {"project": "project_lens"}}
    document_text = "Contract deliverables for LENS visual assessment."

    with pytest.raises(ValueError):
        executor.execute(
            document_text=document_text,
            pa_context=pa_context,
        )


def test_visual_provider_exhaustion_yields_typed_failed_outcome(tmp_path: Path) -> None:
    """Verify when all OCR/vision providers fail in production, typed failed outcome is returned."""
    dummy_img = tmp_path / "system_architecture_slide.png"
    dummy_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummy_corrupt")

    # In production (mock_mode=False) with no credentials/providers succeeding
    with patch("dual_pipeline.attachment_core.image_extractor._extract_via_openai_vision", return_value=None), \
         patch("dual_pipeline.attachment_core.image_extractor._extract_via_apple_vision", return_value=None), \
         patch("dual_pipeline.attachment_core.image_extractor._extract_via_gemini_vision", return_value=None):
        res = extract_image_visual(dummy_img, mock_mode=False)

    assert isinstance(res, dict)
    assert res.get("status") == "FAILED"
    assert res.get("extraction_status") == "FAILED"
    assert res.get("error_type") == "VISUAL_EXTRACTION_EXHAUSTED"
    assert res.get("components") == []
    assert res.get("connections") == []
    assert "exhausted" in res.get("summary", "").lower()


def test_empty_valid_extraction_distinct_from_failure() -> None:
    """Verify empty but valid signal projection passes gating cleanly without error."""
    valid_empty_raw = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "doc-empty-valid",
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "evidence_index": {},
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
        "evidence_only_statement_ids": [],
    })
    metadata = {"project": "project_lens", "date": "10-06-2026"}
    document_text = "Clean document with no actionable enterprise architecture mutations."

    result_json = _post_process_document_projection(
        raw_graph_json=valid_empty_raw,
        metadata=metadata,
        document_text=document_text,
    )
    assert isinstance(result_json, str)
    data = json.loads(result_json)
    assert data["schema_version"] == "5.7"
    assert data["graph_mutations"]["entity_upserts"] == []
    assert data["graph_mutations"]["decision_updates"] == []


def test_successful_document_extraction_preserves_downstream_checks() -> None:
    """Verify valid signals are promoted and validated through post processing."""
    valid_raw = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "doc-success",
        "classification": "confidential",
        "audiences": ["nee-engineering"],
        "evidence_index": {
            "ev-1": {
                "id": "ev-1",
                "exact_text": "We are exploring Max Core endpoint delivery for asset hierarchy migration.",
                "kind": "proposal",
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:maximo",
                    "name": "IBM Maximo",
                    "type": "enterprise_system",
                }
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
        "evidence_only_statement_ids": [],
    })
    metadata = {"project": "project_lens", "date": "10-06-2026"}
    document_text = "We are exploring Max Core endpoint delivery for asset hierarchy migration."

    result_json = _post_process_document_projection(
        raw_graph_json=valid_raw,
        metadata=metadata,
        document_text=document_text,
    )
    assert isinstance(result_json, str)
    data = json.loads(result_json)
    assert len(data["graph_mutations"]["entity_upserts"]) == 1
    assert data["graph_mutations"]["entity_upserts"][0]["id"] in ("system:maximo", "system:max-core")
