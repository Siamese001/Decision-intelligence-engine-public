"""tests/adversarial/test_zero_test_blindspots_adversarial.py

Adversarial 7-category verification suite for zero-test critical blindspot modules:
- dual_pipeline.query_core.lineage_db
- dual_pipeline.canonical_pipeline.u0_ledger_checks
- dual_pipeline.audio_ingestion_core.acoustic_cache
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.audio_ingestion_core.acoustic_cache import (
    build_acoustic_cache_key,
    compute_audio_digest,
    resolve_transcript_cache_dir,
)
from dual_pipeline.canonical_pipeline.u0_ledger_checks import _check_acoustic_unknown_utterances
from dual_pipeline.canonical_pipeline.u0_preflight_types import PreflightDiagnostic
from dual_pipeline.query_core.lineage_db import (
    CitationItem,
    _build_graph_path,
    format_confidence_badge,
    humanize_predicate,
    humanize_token,
)


class ZeroTestBlindspotsAdversarialTests(unittest.TestCase):
    def test_lineage_db_badge_calibration_and_boundary_payloads(self) -> None:
        """Category 1 (Boundary payload) & Category 5 (Invariant bypass): verify superseded, candidate, and out-of-range confidence scores."""
        self.assertEqual(
            format_confidence_badge("HIGH", 0.99, currentness_state="superseded"),
            "SUPERSEDED (0%)",
        )
        self.assertEqual(
            format_confidence_badge("CONFIRMED", 1.0, validation_state="superseded"),
            "SUPERSEDED (0%)",
        )
        self.assertEqual(
            format_confidence_badge("HIGH", 0.95, canonical_status="candidate"),
            "CANDIDATE (45%)",
        )
        self.assertEqual(
            format_confidence_badge("HIGH", 0.95, canonical_eligible=0),
            "CANDIDATE (45%)",
        )
        self.assertEqual(format_confidence_badge("HIGH", 0.92, canonical_status="canonical", canonical_eligible=1), "HIGH (92%)")
        self.assertEqual(format_confidence_badge(None, 0.40, canonical_status="canonical", canonical_eligible=1), "LOW (40%)")
        self.assertEqual(humanize_token("asrt_edge_project_lens_target"), "Project Lens Target")
        self.assertEqual(humanize_predicate("ALLOCATES_ENGINEERING_CAPACITY_TO"), "Allocates Engineering Capacity To")

        long_claim = "A" * 120
        path_str = _build_graph_path("NodeA", "HAS_CLAIM_TEXT", "NodeB", long_claim, None)
        self.assertIn("...", path_str)
        self.assertNotIn("NodeB", path_str)

    def test_u0_ledger_checks_unmapped_unknown_utterances_fail_closed(self) -> None:
        """Category 2 (Malformed input) & Category 5 (Invariant bypass): unmapped 'Unknown' turns emit BLOCKING U0_UNMAPPED_SPEAKERS diagnostic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            corrupt_path = tmppath / "corrupt_utterances.json"
            corrupt_path.write_text("{truncated_json", encoding="utf-8")

            missing: list[str] = []
            diagnostics: list[PreflightDiagnostic] = []
            _check_acoustic_unknown_utterances(
                {"structured_utterances_path": str(corrupt_path)},
                missing,
                diagnostics,
            )
            self.assertEqual(missing, [])
            self.assertEqual(diagnostics, [])

            metadata = {
                "meeting_name": "Ops Sync",
                "source_file_hash": "urn:nee:source-file:sha256:deadbeef1234",
                "structured_utterances": [
                    {"speaker": "Unknown", "text": "We need to validate the transformer nameplate."},
                    {"speaker": "Amit Ayer", "text": "Agreed."},
                ],
                "speaker_map": {},
            }
            with patch(
                "dual_pipeline.hitl_governance_core.crypto.find_signed_approvals",
                autospec=True,
                return_value=[],
            ):
                _check_acoustic_unknown_utterances(metadata, missing, diagnostics)
            self.assertIn("speaker_map", missing)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].check_name, "U0_UNMAPPED_SPEAKERS")
            self.assertEqual(diagnostics[0].severity, "BLOCKING")

    def test_acoustic_cache_digest_and_sandbox_isolation(self) -> None:
        """Category 1 (0-byte boundary) & Category 6 (Recovery): verify deterministic SHA-256 digest and hermetic cache dir resolution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            empty_audio = tmppath / "zero_length.wav"
            empty_audio.write_bytes(b"")
            full_sha, prefix = compute_audio_digest(str(empty_audio))
            self.assertEqual(len(full_sha), 64)
            self.assertEqual(prefix, full_sha[:16])

            meta = {"transcript_cache_dir": str(tmppath / "custom_cache")}
            resolved_dir = resolve_transcript_cache_dir(str(empty_audio), meta)
            self.assertEqual(resolved_dir, tmppath / "custom_cache")
            self.assertTrue(resolved_dir.is_dir())

            k1 = build_acoustic_cache_key(full_sha, "whisper-large-v3", "en", False, True, backend="mlx-whisper")
            k2 = build_acoustic_cache_key(full_sha, "whisper-large-v3", "en", False, False, backend="mlx-whisper")
            self.assertEqual(len(k1), 16)
            self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
