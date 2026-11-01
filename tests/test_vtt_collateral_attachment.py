"""
tests/test_vtt_collateral_attachment.py

Wave 3 Acceptance Suite: Document Collateral Transcript Adapter.
Verifies that .vtt and .srt files can be safely ingested as documentary collateral
(e.g., call recordings attached to investigation dossiers) for Output 03 knowledge
extraction without claiming forensic meeting authority (Outputs 01/02).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline_core.attachment_preflight import preflight_attachment
from dual_pipeline.document_pipeline_core.content_loader import (
    _load_collateral_vtt,
    load_document_content,
)
from dual_pipeline.source_classification.capability_registry import (
    get_capability_by_ext,
)

SAMPLE_VTT_CONTENT = """WEBVTT - Interview Recording Exhibit

1
00:00:01.000 --> 00:00:04.500
<v Dr. Aris Thorne>We confirmed the anomaly in subsystem four during the test run.</v>

2
00:00:05.000 --> 00:00:08.200
<v Elena Vance>All telemetry feeds showed nominal pressure until timestamp mark delta.</v>

3
00:00:09.000 --> 00:00:12.000
The secondary sensor array recorded a forty millisecond spike.
"""

SAMPLE_SRT_CONTENT = """1
00:00:01,000 --> 00:00:04,500
Telemetry baseline confirmed.

2
00:00:05,000 --> 00:00:08,200
Pressure threshold exceeded.
"""


class TestVttCollateralAttachment(unittest.TestCase):
    """Test suite for collateral transcript intake within document envelopes."""

    def test_load_collateral_vtt_clean_text_extraction(self) -> None:
        """Verify _load_collateral_vtt cleans cues, headers, and speaker tags."""
        raw_bytes = SAMPLE_VTT_CONTENT.encode("utf-8")
        metadata: dict = {}
        clean_text = _load_collateral_vtt(Path("exhibit.vtt"), raw_bytes, metadata)

        # Ensure headers and timestamps are removed
        self.assertNotIn("WEBVTT", clean_text)
        self.assertNotIn("-->", clean_text)
        self.assertNotIn("<v", clean_text)
        self.assertNotIn("</v>", clean_text)

        # Ensure core spoken text is preserved
        self.assertIn("We confirmed the anomaly in subsystem four", clean_text)
        self.assertIn("All telemetry feeds showed nominal pressure", clean_text)
        self.assertIn("The secondary sensor array recorded a forty millisecond spike", clean_text)

        # Verify metadata accounting for Output 03
        self.assertTrue(metadata.get("is_collateral_attachment"))
        coverage = metadata.get("_source_coverage", {})
        self.assertTrue(coverage.get("is_collateral_transcript"))
        self.assertEqual(coverage.get("total_records"), 3)
        self.assertGreaterEqual(len(metadata.get("source_units", [])), 3)

    def test_load_collateral_srt_clean_text_extraction(self) -> None:
        """Verify _load_collateral_vtt handles SRT format cleanly."""
        raw_bytes = SAMPLE_SRT_CONTENT.encode("utf-8")
        metadata: dict = {}
        clean_text = _load_collateral_vtt(Path("evidence.srt"), raw_bytes, metadata)

        self.assertNotIn("-->", clean_text)
        self.assertIn("Telemetry baseline confirmed.", clean_text)
        self.assertIn("Pressure threshold exceeded.", clean_text)
        self.assertTrue(metadata.get("is_collateral_attachment"))

    def test_preflight_attachment_rejects_standalone_vtt(self) -> None:
        """Standalone VTT without is_collateral=True must be rejected with prescriptive error."""
        raw_bytes = SAMPLE_VTT_CONTENT.encode("utf-8")
        result = preflight_attachment(raw_bytes, "interview.vtt", is_collateral=False)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, "AUDIO_TRANSCRIPT_ROUTE_EXCLUDED")
        self.assertIn("nee ingest-meeting --input", result.remediation_command)

    def test_preflight_attachment_allows_collateral_vtt(self) -> None:
        """Collateral VTT with is_collateral=True passes preflight."""
        raw_bytes = SAMPLE_VTT_CONTENT.encode("utf-8")
        result = preflight_attachment(raw_bytes, "interview.vtt", is_collateral=True)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.format_id, "VTT_SRT")

    def test_preflight_attachment_rejects_binary_collateral_vtt(self) -> None:
        """Corrupt or binary payload with .vtt extension fails closed even with is_collateral=True."""
        binary_bytes = b"\x00\x01\x02\xff\xfe\x00\x00corrupt"
        result = preflight_attachment(binary_bytes, "interview.vtt", is_collateral=True)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, "MALFORMED_VTT_SRT_PAYLOAD")

    def test_load_document_content_with_collateral_metadata(self) -> None:
        """load_document_content succeeds when is_collateral flag is passed in metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vtt_path = Path(tmpdir) / "call_transcript.vtt"
            vtt_path.write_text(SAMPLE_VTT_CONTENT, encoding="utf-8")

            metadata = {"is_collateral": True}
            text, source_hash, fmt = load_document_content(vtt_path, metadata=metadata)

            self.assertEqual(fmt, "vtt")
            self.assertIn("We confirmed the anomaly", text)
            self.assertTrue(metadata.get("is_collateral_attachment"))
            self.assertIn("source_units", metadata)

    def test_load_document_content_without_collateral_flag_raises(self) -> None:
        """load_document_content fails when no collateral flag is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vtt_path = Path(tmpdir) / "call_transcript.vtt"
            vtt_path.write_text(SAMPLE_VTT_CONTENT, encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                load_document_content(vtt_path, metadata={})
            self.assertIn("AUDIO_TRANSCRIPT_ROUTE_EXCLUDED", str(ctx.exception))
            self.assertIn("nee ingest-meeting --input", str(ctx.exception))

    def test_capability_registry_collateral_declarations(self) -> None:
        """Verify capability registry declares collateral support for VTT_SRT."""
        cap = get_capability_by_ext(".vtt")
        self.assertIsNotNone(cap)
        self.assertFalse(cap.is_attachment_supported)
        self.assertTrue(cap.is_collateral_supported)
        self.assertIn("collateral_transcript", cap.capabilities)
        self.assertIn("_load_collateral_vtt", cap.attachment_adapter)


if __name__ == "__main__":
    unittest.main()
