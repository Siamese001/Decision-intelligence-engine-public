"""Unit tests for DocumentPipeline unified agentic lifecycle and receipt generation.

Verifies:
1. DocumentPipeline is a subclass of UnifiedAgenticPipeline.
2. pipeline_name property returns 'DocumentPipeline'.
3. End-to-end execution traverses all 7 stages in strict sequence:
   U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 -> EVALS.
4. pipeline_execution_receipt.json is unconditionally written to the output directory.
"""
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline
from dual_pipeline.unified_agentic_pipeline import (
    PIPELINE_STAGE_SEQUENCE,
    UnifiedAgenticPipeline,
)


class TestDocumentPipelineUnifiedAgentic(unittest.TestCase):
    """Verify DocumentPipeline conformance to UnifiedAgenticPipeline contracts."""

    def test_document_pipeline_is_unified_agentic_subclass(self) -> None:
        """DocumentPipeline must inherit from UnifiedAgenticPipeline."""
        self.assertTrue(
            issubclass(DocumentPipeline, UnifiedAgenticPipeline),
            "DocumentPipeline must be a subclass of UnifiedAgenticPipeline",
        )

    def test_pipeline_name_property(self) -> None:
        """DocumentPipeline must declare pipeline_name == 'DocumentPipeline'."""
        pipeline = DocumentPipeline(provider_type="offline")
        self.assertEqual(pipeline.pipeline_name, "DocumentPipeline")

    def test_offline_execution_emits_pipeline_receipt(self) -> None:
        """Offline execution must produce a valid 7-stage pipeline_execution_receipt.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            sample_doc = tmp_path / "sample_architecture_sow.txt"
            sample_doc.write_text(
                "# Project Nexus Architecture Specification\n\n"
                "Date: 09/17/2026\n"
                "Attendees: Amit Ayer, Drew Oakley\n\n"
                "## Decisions\n"
                "- System: NEXUS-CORE will transition to unified pipeline DAG.\n"
                "- Decision: Authoritative writes require 7-stage receipt.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "project_nexus" / "09-17-2026" / "Architecture_SOW"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = DocumentPipeline(provider_type="offline")
            metadata = {
                "project": "project_nexus",
                "title": "Project Nexus Architecture Specification",
                "date": "09/17/2026",
                "date_filename": "09-17-2026",
                "filename_slug": "Architecture_SOW",
                "author": "Amit Ayer",
            }

            res = pipeline.run(
                input_path=sample_doc,
                metadata=metadata,
                output_dir=str(out_dir),
                mutate_graph=False,
            )

            self.assertEqual(res["status"], "COMPLETE")
            self.assertTrue(res["output3_only"])

            # Verify receipt file exists
            receipt_file = out_dir / "pipeline_execution_receipt.json"
            self.assertTrue(receipt_file.exists(), f"Missing receipt file: {receipt_file}")

            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["pipeline_name"], "DocumentPipeline")
            self.assertEqual(receipt_data["terminal_outcome"], "COMPLETE")

            # Verify all 7 stages recorded in receipt
            stages = receipt_data["stages"]
            for stage in PIPELINE_STAGE_SEQUENCE:
                self.assertIn(
                    stage.value,
                    stages,
                    f"Stage {stage.value} must be recorded in execution receipt",
                )
                self.assertEqual(
                    stages[stage.value]["status"],
                    "PASS",
                    f"Stage {stage.value} status must be PASS",
                )


if __name__ == "__main__":
    unittest.main()
