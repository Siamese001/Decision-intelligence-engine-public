"""Adversarial Tests for Output 3 Fail-Closed Boundaries.

Covers mandatory categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: 0-byte document, 0-byte image, empty strings.
2. Malformed input: truncated JSON, invalid types, malformed SVG/DrawIO diagrams.
3. Invariant bypass attempt: injected filename attempting to synthesize system components/edges.
4. Rollback & zero graph mutation: verifying failures produce zero writes or mutations.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.attachment_core.image_extractor import (
    _extract_drawio_diagram,
    _extract_svg_diagram,
    extract_image_visual,
)
from dual_pipeline.document_pipeline_core.executor import (
    DocumentGraphExtractorExecutor,
    _post_process_document_projection,
)
from dual_pipeline.transcript_stages_core.models import L2SynthesisProvider


class FailingMockProvider(L2SynthesisProvider):
    """Provider returning adversarial payloads."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text

    def synthesize_recap(self, prompt: str, user_content: str) -> str:
        return ""

    def synthesize_graph(self, prompt: str, user_content: str) -> str:
        return self.response_text


# Category 1: Boundary Payloads
def test_boundary_zero_byte_document_fails_preflight() -> None:
    """Boundary Payload: Zero-length / empty document text fails preflight validation."""
    provider = FailingMockProvider(response_text="{}")
    executor = DocumentGraphExtractorExecutor(provider=provider)

    with pytest.raises(ValueError, match="visible text before synthesis"):
        executor.execute(
            document_text="",
            pa_context={"metadata": {"project": "project_lens"}},
        )


def test_boundary_zero_byte_image_file_handled_safely(tmp_path: Path) -> None:
    """Boundary Payload: Zero-byte image file yields typed failed outcome."""
    zero_file = tmp_path / "empty_visual.png"
    zero_file.write_bytes(b"")

    with patch("dual_pipeline.attachment_core.image_extractor._extract_via_openai_vision", return_value=None), \
         patch("dual_pipeline.attachment_core.image_extractor._extract_via_apple_vision", return_value=None), \
         patch("dual_pipeline.attachment_core.image_extractor._extract_via_gemini_vision", return_value=None):
        res = extract_image_visual(zero_file, mock_mode=False)

    assert isinstance(res, dict)
    assert res.get("status") == "FAILED"
    assert res.get("components") == []
    assert res.get("connections") == []


# Category 2: Malformed Inputs
def test_malformed_truncated_json_projection_fails_closed() -> None:
    """Malformed Input: Truncated JSON in candidate projection fails closed immediately."""
    truncated_json = '{"schema_version": "5.7", "graph_mutations": {"entity_upserts": [{"id": "system:max'
    metadata = {"project": "project_lens", "date": "10-06-2026"}
    document_text = "Some contract text."

    with pytest.raises(ValueError, match="valid JSON object|invalid raw projection JSON"):
        _post_process_document_projection(
            raw_graph_json=truncated_json,
            metadata=metadata,
            document_text=document_text,
        )


def test_malformed_svg_xml_fails_safe_without_exception() -> None:
    """Malformed Input: Corrupted / invalid XML in SVG attachment returns empty topology safely."""
    corrupt_xml = b"<svg><unclosed_tag><text>Broken SVG"
    res = _extract_svg_diagram(corrupt_xml)
    assert isinstance(res, dict)
    assert res["components"] == []
    assert res["connections"] == []
    assert res["text"] == ""


def test_malformed_drawio_xml_fails_safe_without_exception() -> None:
    """Malformed Input: Corrupted / invalid XML in DrawIO diagram returns empty topology safely."""
    corrupt_drawio = b"NOT_XML_GARBAGE_BYTES_12345678"
    res = _extract_drawio_diagram(corrupt_drawio)
    assert isinstance(res, dict)
    assert res["components"] == []
    assert res["connections"] == []
    assert res["text"] == ""


# Category 3: Invariant Bypass Attempts
def test_invariant_bypass_filename_injection_prevented_in_production(tmp_path: Path) -> None:
    """Invariant Bypass: Filename injection (e.g. SystemA_Calls_SystemB.png) does NOT produce topology."""
    injected_img = tmp_path / "System_Auth_Calls_Database_Service.png"
    injected_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRinjected")

    # In production (mock_mode=False) with exhausted visual providers
    with patch("dual_pipeline.attachment_core.image_extractor._extract_via_openai_vision", return_value=None), \
         patch("dual_pipeline.attachment_core.image_extractor._extract_via_apple_vision", return_value=None), \
         patch("dual_pipeline.attachment_core.image_extractor._extract_via_gemini_vision", return_value=None):
        res = extract_image_visual(injected_img, mock_mode=False)

    # Must NOT have synthesized components or connections from the filename
    assert res.get("status") == "FAILED"
    assert res.get("components") == []
    assert res.get("connections") == []
    assert not any("System" in str(c) for c in res.get("components", []))
    assert not any("Database" in str(c) for c in res.get("components", []))


# Category 4: Rollback & Zero Graph Mutation
def test_failed_document_extraction_yields_no_graph_mutations() -> None:
    """Rollback & Recovery: Failed extraction prevents graph publication/mutation completely."""
    provider = FailingMockProvider(response_text="INVALID_SYNTHESIS_NOT_PARSABLE")
    executor = DocumentGraphExtractorExecutor(provider=provider)

    pa_context = {"metadata": {"project": "project_lens"}}
    document_text = "Standard valid document text."

    # Verify that exception prevents any downstream mutation dictionary from being returned
    with pytest.raises(ValueError):
        executor.execute(
            document_text=document_text,
            pa_context=pa_context,
        )
