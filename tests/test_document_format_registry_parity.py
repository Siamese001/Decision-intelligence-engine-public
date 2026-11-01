"""
tests/test_document_format_registry_parity.py

Wave 1 Acceptance Suite: Single SSOT Format Contract Parity Verification.
Asserts that capability_registry, format_source_units, conversion_cache,
modalities, detector, attachment_preflight, and content_loader never diverge.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.attachment_core.detector import (
    ALL_ATTACHMENT_EXTENSIONS,
    DOCUMENT_EXTENSIONS as DETECTOR_DOC_EXTS,
    IMAGE_EXTENSIONS as DETECTOR_IMG_EXTS,
    PRESENTATION_EXTENSIONS as DETECTOR_PRES_EXTS,
    SPREADSHEET_EXTENSIONS as DETECTOR_SHEET_EXTS,
)
from dual_pipeline.canonical_pipeline.modalities import (
    AUDIO_EXTENSIONS as MODALITIES_AUDIO_EXTS,
    DOCUMENT_EXTENSIONS as MODALITIES_DOC_EXTS,
    IMAGE_EXTENSIONS as MODALITIES_IMG_EXTS,
    TRANSCRIPT_EXTENSIONS as MODALITIES_TRANS_EXTS,
)
from dual_pipeline.document_pipeline_core.attachment_preflight import (
    _AUDIO_EXTENSIONS as PREFLIGHT_AUDIO_EXTS,
    _LEGACY_BLOCKED as PREFLIGHT_BLOCKED_EXTS,
    _TRANSCRIPT_CUES as PREFLIGHT_CUE_EXTS,
    preflight_attachment,
)
from dual_pipeline.document_pipeline_core.content_loader import _DOC_EXT_HANDLERS
from dual_pipeline.document_pipeline_core.conversion_cache import CONVERTER_VERSIONS
from dual_pipeline.document_pipeline_core.format_source_units import (
    FORMAT_ADAPTERS,
    FailureDisposition,
    VisualEscalationPolicy,
    get_format_adapter,
)
from dual_pipeline.source_classification.capability_registry import (
    SupportStatus,
    _REGISTRY,
    audit_matrix,
    get_all_registered_extensions,
    get_audio_extensions,
    get_blocked_extensions,
    get_capability_by_ext,
    get_capability_by_format_id,
    get_converter_versions,
    get_cue_extensions,
    get_document_extensions,
    get_image_extensions,
    get_presentation_extensions,
    get_spreadsheet_extensions,
    is_format_supported,
)


class TestDocumentFormatRegistryParity(unittest.TestCase):
    """Verifies that format registrations and adapter contracts remain 100% in parity."""

    def test_capability_registry_format_adapters_parity(self) -> None:
        """Every entry in _REGISTRY must map cleanly into FORMAT_ADAPTERS."""
        for fmt_id, cap in _REGISTRY.items():
            self.assertIn(fmt_id, FORMAT_ADAPTERS, f"Format {fmt_id} missing from FORMAT_ADAPTERS")
            adapter = FORMAT_ADAPTERS[fmt_id]
            self.assertEqual(adapter.format_id, cap.format_id)
            self.assertEqual(adapter.name, cap.name)
            self.assertEqual(adapter.extensions, tuple(cap.extensions))
            self.assertEqual(adapter.mime_types, tuple(cap.mime_types))
            self.assertEqual(adapter.locator_grammar, cap.locator_grammar)
            self.assertEqual(adapter.visual_policy.value, cap.visual_escalation_rule)
            self.assertEqual(adapter.failure_disposition.value, cap.failure_disposition)
            self.assertEqual(adapter.unit_types, tuple(cap.unit_types))
            self.assertEqual(adapter.is_attachment_supported, cap.is_attachment_supported)

    def test_conversion_cache_versions_parity(self) -> None:
        """CONVERTER_VERSIONS in conversion_cache must match capability_registry."""
        expected = get_converter_versions()
        self.assertEqual(CONVERTER_VERSIONS, expected)
        for ext, ver in CONVERTER_VERSIONS.items():
            self.assertTrue(ver.endswith("-v1.0.0"), f"Converter version format invalid: {ver}")
            cap = get_capability_by_ext(f".{ext}")
            self.assertIsNotNone(cap, f"Extension .{ext} has converter version but no registered capability")

    def test_doc_ext_handlers_registered_in_capability_registry(self) -> None:
        """All file extensions handled by content_loader must exist in capability_registry."""
        for ext in _DOC_EXT_HANDLERS:
            cap = get_capability_by_ext(ext)
            self.assertIsNotNone(cap, f"content_loader handler for {ext} not found in capability_registry")
            self.assertIn(ext, cap.extensions)

    def test_modalities_satellite_sets_parity(self) -> None:
        """Extension sets in modalities.py must strictly match capability registry queries."""
        self.assertEqual(MODALITIES_AUDIO_EXTS, get_audio_extensions())
        self.assertEqual(MODALITIES_DOC_EXTS, get_document_extensions())
        self.assertEqual(MODALITIES_IMG_EXTS, get_image_extensions())
        for cue in get_cue_extensions():
            self.assertIn(cue, MODALITIES_TRANS_EXTS)

    def test_detector_satellite_sets_parity(self) -> None:
        """Extension categories in attachment detector must align with capability registry."""
        self.assertEqual(DETECTOR_IMG_EXTS, set(get_image_extensions()))
        self.assertEqual(DETECTOR_PRES_EXTS, set(get_presentation_extensions()))
        self.assertEqual(DETECTOR_SHEET_EXTS, set(get_spreadsheet_extensions()))
        for ext in DETECTOR_DOC_EXTS:
            self.assertIn(ext, ALL_ATTACHMENT_EXTENSIONS)

    def test_preflight_satellite_sets_parity(self) -> None:
        """Preflight extension guards must match capability registry queries."""
        self.assertEqual(PREFLIGHT_AUDIO_EXTS, get_audio_extensions())
        self.assertEqual(PREFLIGHT_CUE_EXTS, get_cue_extensions())
        self.assertEqual(PREFLIGHT_BLOCKED_EXTS, get_blocked_extensions())

    def test_legacy_blocked_formats_fail_preflight(self) -> None:
        """Legacy binary Office formats (.ppt, .xls, .msg) must fail preflight with BLOCKED disposition."""
        blocked_exts = get_blocked_extensions()
        self.assertIn(".ppt", blocked_exts)
        self.assertIn(".xls", blocked_exts)
        self.assertIn(".msg", blocked_exts)

        ole_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
        for ext in blocked_exts:
            res = preflight_attachment(ole_header, f"legacy_sample{ext}")
            self.assertFalse(res.is_valid)
            self.assertEqual(res.disposition, FailureDisposition.BLOCKED)
            self.assertTrue(res.is_legacy)
            self.assertFalse(is_format_supported(ext))

    def test_unknown_and_malformed_queries(self) -> None:
        """Adversarial / boundary queries must handle gracefully without exceptions."""
        self.assertIsNone(get_capability_by_ext(""))
        self.assertIsNone(get_capability_by_ext("."))
        self.assertIsNone(get_capability_by_ext(".unknown_ext_xyz"))
        self.assertIsNone(get_capability_by_format_id(""))
        self.assertIsNone(get_capability_by_format_id("NONEXISTENT_FMT"))
        self.assertFalse(is_format_supported(".unknown_ext_xyz"))
        self.assertFalse(is_format_supported("NONEXISTENT_FMT"))

        # Case insensitivity
        cap_docx = get_capability_by_ext(".DOCX")
        self.assertIsNotNone(cap_docx)
        self.assertEqual(cap_docx.format_id, "DOCX")

        adapter_docx = get_format_adapter("docx")
        self.assertIsNotNone(adapter_docx)
        self.assertEqual(adapter_docx.format_id, "DOCX")

        adapter_msg = get_format_adapter("msg")
        self.assertIsNotNone(adapter_msg)
        self.assertEqual(adapter_msg.format_id, "MSG_LEGACY")

    def test_all_registered_extensions_coverage(self) -> None:
        """get_all_registered_extensions must return comprehensive set across all formats."""
        all_exts = get_all_registered_extensions()
        self.assertGreater(len(all_exts), 25)
        for ext in [".docx", ".pdf", ".xlsx", ".pptx", ".csv", ".tsv", ".html", ".eml", ".txt", ".md", ".json"]:
            self.assertIn(ext, all_exts)
        for ext in [".wav", ".mp3", ".vtt", ".srt"]:
            self.assertIn(ext, all_exts)
