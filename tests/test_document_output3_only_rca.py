"""Tests for Project LENS SOW Document Ingestion & Output 03-Only Architecture.

Verifies:
1. is_document_content and is_document_source accurately classify SOWs and contracts.
2. validate_output_hierarchy enforces negative invariant [PROHIBITED_DOCUMENT_DELIVERABLE].
3. output/project_lens/05-26-2026/ strictly contains Deliverable 03 and receipts without 01 or 02.
4. system:maximo key_evidence_refs in system_directory.py resolve to existing files.
5. _ingest_meeting automatically routes SOW and written document content to DocumentPipeline.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.handlers import is_document_source
from dual_pipeline.system_directory import ENTERPRISE_SYSTEMS
from dual_pipeline.transcript_metadata_helper import is_document_content
from tools.validate_output_hierarchy import validate_meeting_directory


class TestDocumentOutput3OnlyRCA(unittest.TestCase):
    """Regression test suite for RCA and permanent fix of document-mode deliverables."""

    def test_is_document_content_detection(self) -> None:
        """Verify content classification across SOW, contracts, and transcripts."""
        sow_text = (
            "# Apex Global Analytics & PartnerAdvisory: Statement of Work -- LENS Visual Asset Intelligence System\n"
            "This Statement of Work is issued by and between Apex Global Analytics, LLC and PartnerAdvisory, LLC.\n"
            "Services under this SOW will be performed on a time-and-materials basis.\n"
        )
        self.assertTrue(is_document_content(sow_text, "sow_1.md"))
        self.assertTrue(is_document_content("Some text", "Statement_of_Work_Phase_1.docx"))
        self.assertTrue(is_document_content("Some text", "LENS_SOW_Amendment.pdf"))

        email_text = (
            "From: leader@company.com\n"
            "Date: September 16, 2026\n"
            "Subject: Org Announcement\n"
            "Team, today we announce changes.\n"
        )
        self.assertTrue(is_document_content(email_text, "memo.txt"))

        meeting_transcript = (
            "[00:00:01] Amit: Good morning everyone, thanks for joining.\n"
            "[00:00:15] Stefan: Morning Amit, let's review the sprint agenda.\n"
            "[00:01:00] Mandy: I have three updates from the platform workstream.\n"
        )
        self.assertFalse(is_document_content(meeting_transcript, "meeting_transcript.txt"))

    def test_is_document_source_detection(self) -> None:
        """Verify is_document_source correctly identifies document paths and metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            sow_file = tmp_path / "LENS_SOW_1.txt"
            sow_file.write_text("Statement of Work for LENS image processing and extraction.", encoding="utf-8")

            self.assertTrue(is_document_source(sow_file, {}))

            meeting_file = tmp_path / "sync.txt"
            meeting_file.write_text("[00:00:01] Speaker A: Hello team.\n", encoding="utf-8")
            self.assertFalse(is_document_source(meeting_file, {}))

            # Metadata override
            self.assertTrue(is_document_source(meeting_file, {"is_document": True}))
            self.assertTrue(is_document_source(meeting_file, {"output3_only": True}))

    def test_hierarchy_rejects_01_and_02_in_document_container(self) -> None:
        """Verify negative invariant: 01_* and 02_* are forbidden in document containers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_dir = Path(tmpdir) / "project_lens" / "05-26-2026"
            doc_dir.mkdir(parents=True, exist_ok=True)

            # Add document metadata and source_document.txt
            meta = {
                "project": "project_lens",
                "title": "LENS SOW",
                "is_document": True,
                "output3_only": True,
            }
            (doc_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (doc_dir / "source_document.txt").write_text("SOW source content", encoding="utf-8")
            valid_receipt = json.dumps({"status": "PASS", "valid": True, "details": "populated_for_test"})
            (doc_dir / "03_LENS_Phase_1_SOW_05-26-2026_0900AM_1000AM_ET_canonical_signal_projection.json").write_text(valid_receipt, encoding="utf-8")
            (doc_dir / "qa_gates_report.json").write_text(valid_receipt, encoding="utf-8")
            (doc_dir / "llm_judge_evaluation.json").write_text(valid_receipt, encoding="utf-8")
            (doc_dir / "final_release_decision.json").write_text(valid_receipt, encoding="utf-8")
            (doc_dir / "intake_package.json").write_text(valid_receipt, encoding="utf-8")

            # Compliant state: 0 violations
            violations = validate_meeting_directory(doc_dir)
            self.assertEqual(violations, [])

            # Inject prohibited 01 deliverable
            prohibited_01 = doc_dir / "01_LENS_Phase_1_SOW_05-26-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"
            prohibited_01.write_text("# Prohibited transcript", encoding="utf-8")

            violations = validate_meeting_directory(doc_dir)
            self.assertTrue(any("[PROHIBITED_DOCUMENT_DELIVERABLE]" in v for v in violations))
            self.assertTrue(any("Deliverable 01 prohibited in document container" in v for v in violations))

            # Remove 01 and inject prohibited 02 deliverable
            prohibited_01.unlink()
            prohibited_02 = doc_dir / "02_LENS_Phase_1_SOW_05-26-2026_0900AM_1000AM_ET_executive_recap.md"
            prohibited_02.write_text("# Prohibited recap", encoding="utf-8")

            violations = validate_meeting_directory(doc_dir)
            self.assertTrue(any("[PROHIBITED_DOCUMENT_DELIVERABLE]" in v for v in violations))
            self.assertTrue(any("Deliverable 02 prohibited in document container" in v for v in violations))

    def test_project_lens_05_26_2026_satisfies_hierarchy(self) -> None:
        """Verify that output/project_lens/05-26-2026 strictly complies with output hierarchy."""
        lens_sow_dir = Path("output/project_lens/05-26-2026")
        self.assertTrue(lens_sow_dir.exists(), "Directory output/project_lens/05-26-2026 must exist")

        # Must not contain 01 or 02
        self.assertEqual(list(lens_sow_dir.glob("01_*")), [], "Deliverable 01 must not exist")
        self.assertEqual(list(lens_sow_dir.glob("02_*")), [], "Deliverable 02 must not exist")

        # Must contain 03 and receipts
        self.assertEqual(len(list(lens_sow_dir.glob("03_*_canonical_signal_projection.json"))), 1)
        self.assertTrue((lens_sow_dir / "source_document.txt").exists())
        self.assertTrue((lens_sow_dir / "metadata.json").exists())
        self.assertTrue((lens_sow_dir / "qa_gates_report.json").exists())
        self.assertTrue((lens_sow_dir / "llm_judge_evaluation.json").exists())
        self.assertTrue((lens_sow_dir / "final_release_decision.json").exists())
        self.assertTrue((lens_sow_dir / "intake_package.json").exists())

        # Zero violations under validate_meeting_directory
        violations = validate_meeting_directory(lens_sow_dir)
        self.assertEqual(violations, [], f"Unexpected hierarchy violations in 05-26-2026: {violations}")

    def test_system_directory_maximo_evidence_refs_exist(self) -> None:
        """Verify all key_evidence_refs for system:maximo resolve to real files on disk."""
        maximo = ENTERPRISE_SYSTEMS.get("system:maximo")
        self.assertIsNotNone(maximo, "system:maximo must exist in ENTERPRISE_SYSTEMS")

        for ref in maximo.key_evidence_refs:
            ref_path = Path(ref)
            self.assertTrue(ref_path.exists(), f"Evidence ref '{ref}' does not exist on disk")
            # Prohibit pointing to 01 or 02 in 05-26-2026
            self.assertFalse("01_LENS_Phase_1_SOW" in ref, f"Evidence ref points to prohibited 01: {ref}")
            self.assertFalse("02_LENS_Phase_1_SOW" in ref, f"Evidence ref points to prohibited 02: {ref}")

    @patch("dual_pipeline.cli.handlers._ingest_document")
    def test_ingest_meeting_auto_routes_sow_to_document_pipeline(self, mock_ingest_doc: MagicMock) -> None:
        """Verify _ingest_meeting routes to _ingest_document when SOW content is detected."""
        from dual_pipeline.cli.handlers import _ingest_meeting

        mock_ingest_doc.return_value = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            sow_path = Path(tmpdir) / "sow.md"
            sow_path.write_text(
                "# Statement of Work -- LENS Project\n"
                "Purchase contract terms and deliverables.\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                input=str(sow_path),
                project="project_lens",
                meeting=None,
                document=False,
                output3_only=False,
                email=False,
            )

            ret = _ingest_meeting(args)
            self.assertEqual(ret, 0)
            mock_ingest_doc.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
