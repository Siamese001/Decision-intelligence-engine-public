"""Unit tests for disambiguating file upload/snapshot date vs actual content date."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline_core.metadata_helper import scaffold_document_metadata
from dual_pipeline.graph_writer_core.assertions import _resolve_assertion_date_status
from dual_pipeline.graph_writer_core.sources import _resolve_source_date_status


class UploadVsActualDateDisambiguationTests(unittest.TestCase):
    """Verify upload date vs actual written/event date disambiguation across metadata and graph writers."""

    def test_email_with_date_header_marks_exact_actual_date(self) -> None:
        email_text = (
            "From: Petter Skantze <Employee-Communications@ec.nee.com>\n"
            "Date: Wednesday, September 16, 2026 at 8:45 AM\n"
            "To: Medina, Jose <JOSE.MEDINA@enterprise.example.com>\n"
            "Subject: Coming together as one Technology organization\n\n"
            "Team,\n"
            "Today, we are announcing a transformation in how our technology teams are organized.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_file = Path(tmpdir) / "email.txt"
            doc_file.write_text(email_text, encoding="utf-8")
            meta = scaffold_document_metadata(doc_file, project="enterprise_architecture")
            self.assertEqual(meta["date"], "09/16/2026")
            self.assertEqual(meta["actual_date"], "09/16/2026")
            self.assertEqual(meta["date_status"], "exact")

    def test_undated_snapshot_document_marks_estimated_from_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_file = Path(tmpdir) / "planner_export.md"
            snapshot_file.write_text("# NEA Program Planning Export\n\nUndated planning status notes.\n", encoding="utf-8")
            snap_meta = scaffold_document_metadata(snapshot_file, project="project_lens", date_str="10/28/2026")
            self.assertEqual(snap_meta["upload_date"], "10/28/2026")
            self.assertIsNone(snap_meta["actual_date"])
            self.assertEqual(snap_meta["date_status"], "estimated_from_source_snapshot")

            src_status, src_notes = _resolve_source_date_status(snap_meta, "2026-10-28")
            self.assertEqual(src_status, "estimated_from_source_snapshot")
            self.assertIn("file upload/export date: 2026-10-28", src_notes)

            qualifiers: dict[str, str] = {}
            asrt_status = _resolve_assertion_date_status({}, {}, snap_meta, "2026-10-28", qualifiers)
            self.assertEqual(asrt_status, "estimated_from_source_snapshot")
            self.assertEqual(qualifiers.get("date_basis"), "file_upload_snapshot")
            self.assertEqual(qualifiers.get("upload_date"), "2026-10-28")


if __name__ == "__main__":
    unittest.main()
