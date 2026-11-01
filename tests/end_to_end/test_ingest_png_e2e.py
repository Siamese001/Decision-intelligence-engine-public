"""
tests/end_to_end/test_ingest_png_e2e.py

End-to-End, Caching, and Boundary Property Tests for Image/PNG Document Ingestion.
Verifies that:
1. PNGs ingest cleanly as first-class documents without requiring metadata flags.
2. OCR execution is cached by SHA-256 and executes at most once per distinct image.
3. Empty, corrupt, or invalid image payloads fail fast before OCR or LLM invocation.
4. Hypothesis property tests fuzz boundary payloads and verify invariant adherence.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality, detect_modality
from dual_pipeline.cli.ingest import build_canonical_metadata, is_document_source
from dual_pipeline.document_pipeline_core.content_loader import (
    clear_image_ocr_cache,
    extract_document_text_snippet,
    load_document_content,
)
from dual_pipeline.document_pipeline_core.metadata_helper import scaffold_document_metadata
from dual_pipeline.exceptions import SourceIntegrityError

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "nea_org_chart_screenshot_fixture.png"


@pytest.fixture(autouse=True)
def _reset_ocr_cache():
    clear_image_ocr_cache()
    yield
    clear_image_ocr_cache()


def test_png_modality_detection_without_metadata():
    """Verify detect_modality classifies a PNG as CanonicalModality.DOCUMENT."""
    assert FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}"
    modality = detect_modality(FIXTURE_PATH)
    assert modality == CanonicalModality.DOCUMENT
    assert is_document_source(FIXTURE_PATH) is True


def test_png_loader_with_cli_scaffolded_metadata():
    """Verify load_document_content loads a PNG cleanly when using CLI-derived metadata (no metadata file supplied)."""
    assert FIXTURE_PATH.is_file()
    args = argparse.Namespace(
        command="ingest-document",
        input=str(FIXTURE_PATH),
        project="nea",
    )
    meta = build_canonical_metadata(args)
    text, sha, fmt = load_document_content(FIXTURE_PATH, metadata=meta)
    assert fmt == "png"
    assert sha == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert len(text.strip()) > 0


def test_png_ocr_sha256_caching_and_single_execution():
    """Verify OCR runs exactly once for the same image and reuses the in-memory SHA-256 cache."""
    assert FIXTURE_PATH.is_file()
    call_count = 0

    from dual_pipeline.attachment_core import image_extractor

    orig_extract = image_extractor.extract_image_visual

    def counting_extractor(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return orig_extract(*args, **kwargs)

    meta = {"allow_visual_image": True}
    with patch("dual_pipeline.attachment_core.image_extractor.extract_image_visual", side_effect=counting_extractor):
        text1, sha1, fmt1 = load_document_content(FIXTURE_PATH, metadata=meta)
        assert call_count == 1

        text2, sha2, fmt2 = load_document_content(FIXTURE_PATH, metadata=meta)
        # Should NOT invoke extract_image_visual a second time
        assert call_count == 1
        assert text1 == text2
        assert sha1 == sha2


def test_cli_ingest_metadata_scaffolding_for_png():
    """Verify build_canonical_metadata sets visual flags and DOCUMENT modality automatically for PNG."""
    args = argparse.Namespace(
        command="ingest-document",
        input=str(FIXTURE_PATH),
        source=None,
        project="nea",
        document=False,
        notes=False,
        output3_only=False,
        copilot_notes=False,
        bypass_speaker_hitl=False,
    )
    meta = build_canonical_metadata(args)
    assert meta.get("allow_visual_image") is True
    assert meta.get("enable_visual_ocr") is True
    assert meta.get("is_visual_document") is True
    assert meta.get("is_document") is True
    assert meta.get("canonical_modality") == CanonicalModality.DOCUMENT.value


def test_scaffold_document_metadata_extracts_snippet_for_png():
    """Verify scaffold_document_metadata handles image input without raising ValueError."""
    meta = scaffold_document_metadata(
        input_path=FIXTURE_PATH,
        project="nea",
    )
    assert meta.get("project") == "nea"
    assert meta.get("is_document") is True
    assert "nea" in meta.get("title", "").lower() or "org" in meta.get("title", "").lower() or len(meta.get("title", "")) > 0


def test_empty_png_fails_fast_with_source_integrity_error(tmp_path: Path):
    """Verify 0-byte image file raises SourceIntegrityError before any OCR / LLM call."""
    empty_file = tmp_path / "empty.png"
    empty_file.write_bytes(b"")

    with patch("dual_pipeline.attachment_core.image_extractor.extract_image_visual") as mock_ocr:
        with pytest.raises(SourceIntegrityError, match="Input source file is empty"):
            load_document_content(empty_file, metadata={"allow_visual_image": True})
        mock_ocr.assert_not_called()


def test_corrupt_png_fails_fast_without_ocr_or_llm(tmp_path: Path):
    """Verify corrupt non-image bytes raise ValueError before running OCR or LLM vision."""
    corrupt_file = tmp_path / "corrupt.png"
    corrupt_file.write_bytes(b"NOT_A_VALID_PNG_HEADER_CORRUPT_BYTES_DATA")

    with patch("dual_pipeline.attachment_core.image_extractor.extract_image_visual") as mock_ocr:
        with pytest.raises(ValueError, match="Corrupt or invalid image file"):
            load_document_content(corrupt_file, metadata={"allow_visual_image": True})
        mock_ocr.assert_not_called()


def test_oversized_document_fails_fast(tmp_path: Path):
    """Verify oversized image exceeds max document limit before OCR or LLM."""
    big_file = tmp_path / "oversized.png"
    big_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)

    # Patch stat to simulate > 50MB file
    with patch.object(Path, "stat") as mock_stat:
        mock_res = mock_stat.return_value
        mock_res.st_size = 51 * 1024 * 1024
        with patch("dual_pipeline.attachment_core.image_extractor.extract_image_visual") as mock_ocr:
            with pytest.raises(ValueError, match="exceeds 52428800 bytes limit"):
                load_document_content(big_file, metadata={"allow_visual_image": True})
            mock_ocr.assert_not_called()


@settings(max_examples=25, deadline=5000)
@given(
    payload=st.binary(min_size=1, max_size=1024).filter(
        lambda b: not b.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF", b"BM"))
    )
)
def test_hypothesis_corrupt_image_payloads_fail_closed(payload: bytes, tmp_path_factory):
    """Hypothesis property test: all arbitrary non-image byte streams fail fast closed before OCR/LLM."""
    tpath = tmp_path_factory.mktemp("hypo_img") / "fuzzed.png"
    tpath.write_bytes(payload)

    with patch("dual_pipeline.attachment_core.image_extractor.extract_image_visual") as mock_ocr:
        with pytest.raises(ValueError, match="Corrupt or invalid image file"):
            load_document_content(tpath, metadata={"allow_visual_image": True})
        mock_ocr.assert_not_called()
