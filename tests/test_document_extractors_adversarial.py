"""Adversarial edge-case tests for Wave 4 document extractors and pre-intake classifier."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from dual_pipeline.docx_source import extract_docx_markdown
from dual_pipeline.normalization_core.meeting_entities import _build_auxiliary_signal_candidates
from dual_pipeline.pre_intake_classifier import _extract_source_text
from tools.prepare_evidence_envelope_intake import _validate_semantic_yield


def test_pre_intake_classifier_decompresses_docx_zip_payload() -> None:
    """Invariant bypass attempt: Compressed DOCX bytes must be decompressed before signal scanning."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>HIGHLY CONFIDENTIAL BOARD MERGER MEMORANDUM</w:t></w:r></w:p></w:body>"
            "</w:document>"
        )
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))
    docx_bytes = buf.getvalue()
    extracted = _extract_source_text(docx_bytes, {"source": {"title": "Memo"}})
    assert "HIGHLY CONFIDENTIAL BOARD MERGER MEMORANDUM" in extracted


def test_docx_extractor_rejects_malformed_and_empty_inputs() -> None:
    """Boundary payload & malformed input: Empty or corrupt zip bytes fail closed with ValueError."""
    with pytest.raises(ValueError, match="must not be empty"):
        extract_docx_markdown(b"")

    with pytest.raises(ValueError, match="not a valid DOCX zip package"):
        extract_docx_markdown(b"not-a-zip-stream")


def test_auxiliary_signal_candidates_match_mentioned_entity_instead_of_first_profile() -> None:
    """Exact production regression: Auxiliary risk/decision signals must bind to the entity mentioned in text."""
    profile_index = {
        "person:first_unrelated": ("person", "Alice Unrelated", {"stable_identifier": "person:first_unrelated"}),
        "system:target_platform": ("system", "Target Platform", {"stable_identifier": "system:target_platform"}),
    }
    entity_intent_profiles: dict = {}
    evidence_by_id = {"ev-01": {"evidence_id": "ev-01", "locator": {"kind": "section", "value": "## Risks"}}}
    aux = _build_auxiliary_signal_candidates(
        mutations={
            "risk_updates": [
                {
                    "risk_id": "risk-01",
                    "risk_text": "Target Platform migration faces schema cutover delay in Q4.",
                }
            ]
        },
        run_id="run-01",
        meeting_title="Architecture Review",
        classification="internal",
        audiences=["internal_team"],
        first_ev_id="ev-01",
        first_loc={"kind": "section", "value": "## Risks"},
        evidence_by_id=evidence_by_id,
        evidence_by_identifier={},
        profile_index=profile_index,
        entity_intent_profiles=entity_intent_profiles,
    )
    assert len(aux) == 1
    assert aux[0]["entity_mentions"][0]["stable_identifier"] == "system:target_platform"
    assert aux[0]["entity_mentions"][0]["reported_text"] == "Target Platform"
