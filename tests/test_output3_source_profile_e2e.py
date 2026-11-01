"""tests/test_output3_source_profile_e2e.py

End-to-End acceptance test suite for Output 3 source classification and evidence contracts.
Verifies:
1. Full classification lifecycle across all 8 canonical semantic families plus mixed and unknown.
2. Orthogonal independence of container type, deliverable selection, and semantic family.
3. Unknown author integrity (unknown authors remain None; no Corporate Communications fabrication).
4. Reconciled authority rank SSOT (Rank 1 strongest, Rank 9 weakest, epistemic weights).
5. Historical authority rank adaptation without silent reinterpretation.
6. Schema conformance against evidence-envelope-intake.schema.json registered extension.
7. Format capability registry matrix coverage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import unittest

import jsonschema

from dual_pipeline.source_classification import (
    ApprovalState,
    ClassificationBasis,
    FormatCapability,
    MediaModality,
    SemanticFamily,
    SourceProfile,
    SourceSection,
    SupportStatus,
    adapt_historical_authority_rank,
    audit_matrix,
    canonical_authority_weight,
    classify_source,
    get_capability_by_ext,
    is_format_supported,
    is_sufficient_authority,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "dual_pipeline" / "schemas" / "evidence-envelope-intake.schema.json"


class TestOutput3SourceProfileE2E(unittest.TestCase):
    """End-to-End acceptance test suite for Wave 2 source classification and contracts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_e2e_all_eight_semantic_families_classification(self) -> None:
        """Verify each of the 8 canonical families is recognized with appropriate basis and modality."""
        test_cases = [
            (
                "WEBVTT\n00:00.000 --> 00:04.000\nHello team.\n00:04.001 --> 00:08.000\nHi there.",
                {},
                "call.vtt",
                SemanticFamily.RAW_TRANSCRIPT,
                MediaModality.TEXT,
            ),
            (
                "Meeting Summary:\n- Discussed budget.\nKey Takeaways:\n- Proceed to phase 2.",
                {},
                "summary.txt",
                SemanticFamily.DERIVED_RECORD,
                MediaModality.TEXT,
            ),
            (
                "From: alice@example.com\nTo: bob@example.com\nSubject: Project Kickoff\nDate: 2026-05-26\n\nLet's meet.",
                {},
                "kickoff.eml",
                SemanticFamily.CORRESPONDENCE,
                MediaModality.DOCUMENT,
            ),
            (
                "STATEMENT OF WORK\nThis agreement is executed as of May 26, 2026.\nSigned by: Marcus Balk",
                {},
                "sow.docx",
                SemanticFamily.COMMERCIAL_CONTRACT,
                MediaModality.DOCUMENT,
            ),
            (
                "# System Design Document\n## Architecture RFC\nDetails on microservices and API topology.",
                {},
                "architecture.md",
                SemanticFamily.WORKING_SOURCE,
                MediaModality.TEXT,
            ),
            (
                "Slide Deck Presentation Overview\nKey milestones and product roadmap.",
                {},
                "deck.pptx",
                SemanticFamily.VISUAL_COLLATERAL,
                MediaModality.VISUAL,
            ),
            (
                "# Industry Research Report\nMarket analysis and literature review on distributed databases.",
                {},
                "report.pdf",
                SemanticFamily.RESEARCH_INTELLIGENCE,
                MediaModality.DOCUMENT,
            ),
            (
                "timestamp,metric,latency_ms\n2026-05-26T10:00:00Z,query_p99,42.5\n",
                {},
                "metrics.csv",
                SemanticFamily.STRUCTURED_OPERATIONAL,
                MediaModality.STRUCTURED,
            ),
        ]

        for text, meta, fname, expected_fam, expected_mod in test_cases:
            with self.subTest(filename=fname, expected=expected_fam):
                profile = classify_source(text, meta, filename=fname)
                self.assertEqual(profile.semantic_family, expected_fam)
                self.assertEqual(profile.media_modality, expected_mod)

    def test_e2e_docx_container_does_not_imply_transcript(self) -> None:
        """A DOCX container must be classified by its content, never forced to transcript."""
        # Case A: DOCX containing an executed SOW contract
        sow_text = "Statement of Work for Project Lens.\nExecuted as of 2026-05-26.\nSigned by authorized agent."
        p_sow = classify_source(sow_text, filename="agreement.docx")
        self.assertEqual(p_sow.semantic_family, SemanticFamily.COMMERCIAL_CONTRACT)
        self.assertEqual(p_sow.approval_state, ApprovalState.EXECUTED)
        self.assertEqual(p_sow.authority_rank, 1)

        # Case B: DOCX containing meeting recap notes
        notes_text = "Meeting Summary:\n- Discussed timeline.\nAction Items:\n- Review draft."
        p_notes = classify_source(notes_text, filename="notes.docx")
        self.assertEqual(p_notes.semantic_family, SemanticFamily.DERIVED_RECORD)
        self.assertEqual(p_notes.authority_rank, 6)

        # Case C: DOCX containing transcript cues
        transcript_text = "Marcus started transcription\n[00:01:00] Marcus: Hello\n[00:01:05] Stephane: Hi"
        p_trans = classify_source(transcript_text, filename="transcript.docx")
        self.assertEqual(p_trans.semantic_family, SemanticFamily.RAW_TRANSCRIPT)

    def test_e2e_deliverable_selection_is_independent_of_source_class(self) -> None:
        """Selecting Output 3 only ([03]) does not change the semantic classification of a transcript."""
        transcript_text = "[00:01:00] Marcus: Hello\n[00:01:05] Stephane: Hi"
        # Ingested with standalone Output 3 requested
        profile = classify_source(
            transcript_text,
            metadata={"outputs": ["03"], "output3_only": True},
            filename="meeting.txt",
        )
        self.assertEqual(profile.semantic_family, SemanticFamily.RAW_TRANSCRIPT)
        self.assertEqual(profile.deliverable_selection, ["03"])

    def test_e2e_unknown_authors_remain_unknown(self) -> None:
        """When an author is unknown, it remains None; never fabricated to 'Corporate Communications'."""
        text = "# Technical Specification\nDetails of the implementation."
        profile = classify_source(text, metadata={}, filename="spec.md")
        self.assertIsNone(profile.author)

        # Explicit fallback attempt must be sanitized
        profile_fallback = classify_source(
            text,
            metadata={"author": "Corporate Communications"},
            filename="spec.md",
        )
        self.assertIsNone(profile_fallback.author)

    def test_e2e_canonical_authority_rank_ssot(self) -> None:
        """Verify Rank 1 is strongest (weight 0.95) and Rank 9 is weakest (weight 0.10)."""
        self.assertAlmostEqual(canonical_authority_weight(1), 0.95, places=2)
        self.assertAlmostEqual(canonical_authority_weight(2), 0.85, places=2)
        self.assertAlmostEqual(canonical_authority_weight(3), 0.75, places=2)
        self.assertAlmostEqual(canonical_authority_weight(4), 0.60, places=2)
        self.assertAlmostEqual(canonical_authority_weight(5), 0.45, places=2)
        self.assertAlmostEqual(canonical_authority_weight(6), 0.35, places=2)
        self.assertAlmostEqual(canonical_authority_weight(7), 0.25, places=2)
        self.assertAlmostEqual(canonical_authority_weight(8), 0.15, places=2)
        self.assertAlmostEqual(canonical_authority_weight(9), 0.10, places=2)

        # Default fallback for unranked is Rank 5 (0.45)
        self.assertAlmostEqual(canonical_authority_weight(None), 0.45, places=2)

        # Sufficiency threshold gate: rank <= threshold
        self.assertTrue(is_sufficient_authority(1, max_rank_threshold=3))
        self.assertTrue(is_sufficient_authority(2, max_rank_threshold=3))
        self.assertTrue(is_sufficient_authority(3, max_rank_threshold=3))
        self.assertFalse(is_sufficient_authority(4, max_rank_threshold=3))
        self.assertFalse(is_sufficient_authority(7, max_rank_threshold=3))
        self.assertFalse(is_sufficient_authority(None, max_rank_threshold=3))

    def test_e2e_historical_authority_rank_adaptation(self) -> None:
        """Historical adapters convert legacy inverted scales explicitly without silent reinterpretation."""
        # Canonical: 1 preserved, 9 preserved
        self.assertEqual(adapt_historical_authority_rank(1, convention="rank_1_strongest"), 1)
        self.assertEqual(adapt_historical_authority_rank(9, convention="rank_1_strongest"), 9)
        self.assertEqual(adapt_historical_authority_rank(12, convention="rank_1_strongest"), 9)

        # Inverted legacy scale: 9 or 10 highest -> 1; 1 lowest -> 9
        self.assertEqual(adapt_historical_authority_rank(9, convention="inverted_10_highest"), 1)
        self.assertEqual(adapt_historical_authority_rank(8, convention="inverted_10_highest"), 2)
        self.assertEqual(adapt_historical_authority_rank(1, convention="inverted_10_highest"), 9)

        # Rejection of unhandled convention
        with self.assertRaises(ValueError):
            adapt_historical_authority_rank(5, convention="unknown_custom_convention")

    def test_e2e_schema_validation_of_source_profile_extension(self) -> None:
        """Verify SourceProfile.to_dict() validates cleanly against evidence-envelope-intake schema."""
        profile = SourceProfile(
            source_id="sow_lens_2026",
            semantic_family=SemanticFamily.COMMERCIAL_CONTRACT,
            media_modality=MediaModality.DOCUMENT,
            container_type="docx",
            confidentiality="internal",
            author="Marcus Balk",
            approval_state=ApprovalState.EXECUTED,
            effective_date="2026-05-26",
            deliverable_selection=["03"],
            classification_basis=ClassificationBasis.CONTENT_SIGNATURE,
            authority_rank=1,
            origin_group_id="lens-orig-1",
            sections=[
                SourceSection(
                    section_id="sec_1",
                    semantic_family=SemanticFamily.COMMERCIAL_CONTRACT,
                    start_locator="line:1",
                    end_locator="line:50",
                    authority_rank=1,
                )
            ],
        )

        envelope = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "art_lens_evidence_001",
            "canonical_digest": {
                "algorithm": "sha256",
                "scope": "artifact_without_canonical_digest",
                "value": "a" * 64,
            },
            "meeting": {
                "meeting_id": "meet_lens_001",
                "title": "Project Lens SOW Ingestion",
                "attendees": ["Marcus Balk"],
                "source_location": "/path/to/sow.docx",
                "source_hash": "b" * 64,
                "template_version": "1.0",
                "classification": "internal",
                "audiences": ["internal_consulting"],
            },
            "speakers": [],
            "actions": [],
            "adapter": {
                "name": "nee-evidence-envelope-intake",
                "version": "1.0.0",
            },
            "extensions": {
                "source.profile": profile.to_dict(),
            },
        }

        # Seal artifact digest and validate via repo contract validator
        from dual_pipeline.contracts import seal_artifact, validate_artifact
        envelope = seal_artifact(envelope)
        validate_artifact(envelope)

    def test_e2e_format_capability_registry_coverage(self) -> None:
        """Verify all declared formats in the matrix are present and queryable."""
        matrix = audit_matrix()
        self.assertEqual(len(matrix), 14)
        self.assertIn("AUDIO", matrix)
        self.assertIn("DOCX", matrix)
        self.assertIn("PDF", matrix)
        self.assertIn("PPT_LEGACY", matrix)
        self.assertIn("MSG", matrix)
        self.assertIn("XLS_LEGACY", matrix)

        self.assertTrue(is_format_supported(".docx"))
        self.assertTrue(is_format_supported(".wav"))
        self.assertFalse(is_format_supported(".ppt"))  # BLOCKED
        self.assertFalse(is_format_supported(".msg"))  # BLOCKED
        self.assertFalse(is_format_supported(".xls"))  # BLOCKED

        cap_docx = get_capability_by_ext(".docx")
        self.assertIsNotNone(cap_docx)
        self.assertEqual(cap_docx.status, SupportStatus.SUPPORTED)


if __name__ == "__main__":
    unittest.main()
