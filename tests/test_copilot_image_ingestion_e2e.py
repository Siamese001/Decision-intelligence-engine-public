"""
tests/test_copilot_image_ingestion_e2e.py

End-to-end verification of chat-uploaded PNG collateral ingestion into
Deliverables 01, 02, and 03 without a prior filesystem drop log.
Exercises the complete pipeline lifecycle, auto-elevation to COLLATERAL_REVIEWED,
downstream QA gate clearance, and sealed intake package creation.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.transcript_pipeline import RawTranscriptPipeline


class TestCopilotImageIngestionE2E(unittest.TestCase):
    """E2E suite verifying full copilot image ingestion flow."""

    def test_e2e_chat_uploaded_png_lifecycle(self) -> None:
        """Verify chat-uploaded PNG without drop log elevates and populates Deliverables 01-03."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            user_upload_dir = tmp_p / ".user_uploaded"
            user_upload_dir.mkdir(parents=True, exist_ok=True)

            # 1. Create a mock chat-uploaded presentation slide PNG
            png_file = user_upload_dir / "media_1790352506690.png"
            png_file.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
                b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            # 2. Create raw transcript referencing the slide
            transcript_content = (
                "## Transcript\n\n"
                "**[09:01:00] Alex Brennan:** Welcome team. Let's look at the current PartnerAdvisory engagements with Ford on the slide.\n\n"
                "**[09:02:00] Amit Ayer:** Yes, the Warranty Precision Claims AI project is progressing well on Google Cloud.\n\n"
                "**[09:03:00] Shreyak:** Agreed, and the Vehicle Telematics data platform architecture aligns with our target roadmap.\n\n"
                "**[09:04:00] Anya:** Let's ensure the FCSD Hub PM team tracks milestones for the upcoming sprint review.\n\n"
                "**[00:06:23] Amit Ayer:** working with images with LLM.\n\n"
                "**[00:16:25] Amit Ayer:** You're going through an Atlas engineer, reviewing an asset and clicking approve, submit, or edit a field. "
                "That review feeds a learning loop that we're putting in, stored to a persistent database. "
                "So the vision model is like... that comes through. `project_lens`\n"
            )
            raw_transcript_path = tmp_p / "raw_transcript.txt"
            raw_transcript_path.write_text(transcript_content, encoding="utf-8")

            output_dir = tmp_p / "output" / "test_copilot_image" / "09-25-2026" / "Ford_Engagements_Alex_Amit"
            output_dir.mkdir(parents=True, exist_ok=True)

            metadata = {
                "project": "test_copilot_image",
                "meeting_name": "Ford_Engagements_Alex_Amit",
                "date": "09/25/2026",
                "date_filename": "09-25-2026",
                "time": "09:00 AM - 10:00 AM ET",
                "time_filename": "0900AM_1000AM_ET",
                "attendees": "Alex Brennan, Amit Ayer, Shreyak, Anya",
                "classification": "confidential",
                "standardized_slug": "Ford_Engagements_Alex_Amit",
                "subfolder": "Ford_Engagements_Alex_Amit",
                "no_subfolder": True,
            }

            pipeline = RawTranscriptPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=str(raw_transcript_path),
                metadata=metadata,
                output_dir=str(output_dir),
                attachments=[png_file],
            )

            # Verify pipeline execution outputs
            self.assertIsNotNone(result)
            self.assertIn("01_transcript", result)
            self.assertIn("02_recap", result)
            self.assertIn("03_graph", result)

            # 3. Verify staging and auto-elevation in referenced_artifacts
            staged_artifacts = output_dir / "referenced_artifacts"
            self.assertTrue(staged_artifacts.is_dir())
            staged_png = staged_artifacts / "media_1790352506690.png"
            self.assertTrue(staged_png.exists())

            # 4. Verify collateral_drop_log.json was automatically created with COLLATERAL_REVIEWED
            drop_log_path = output_dir / "collateral_drop_log.json"
            self.assertTrue(drop_log_path.exists())
            drop_log_entries = json.loads(drop_log_path.read_text(encoding="utf-8"))
            self.assertTrue(len(drop_log_entries) >= 1)
            self.assertEqual(drop_log_entries[0]["attribution_mode"], "COLLATERAL_REVIEWED")
            self.assertEqual(drop_log_entries[0]["filename"], "media_1790352506690.png")

            # 5. Verify Deliverable 01 forensic transcript incorporates attendees
            f1_text = Path(result["01_transcript"]).read_text(encoding="utf-8")
            self.assertIn("Amit Ayer", f1_text)
            self.assertIn("Alex Brennan", f1_text)

            # 6. Verify Deliverable 02 executive recap exists and passes Gate 2 structure
            f2_text = Path(result["02_recap"]).read_text(encoding="utf-8")
            self.assertIn("Meeting Objective", f2_text)

            # 7. Verify Deliverable 03 canonical signal projection grounds the collateral
            f3_text = Path(result["03_graph"]).read_text(encoding="utf-8")
            graph_data = json.loads(f3_text)
            self.assertEqual(graph_data.get("schema_version"), "5.7")

            # Verify collateral entity was upserted in Deliverable 03
            mutations = graph_data.get("graph_mutations", {})
            entity_upserts = mutations.get("entity_upserts", [])
            collateral_entity = next(
                (e for e in entity_upserts if "media-1790352506690" in e.get("id", "")),
                None
            )
            self.assertIsNotNone(
                collateral_entity,
                "Output 03 must contain an entity upsert for the collateral slide"
            )
            self.assertEqual(collateral_entity.get("attribution_mode"), "COLLATERAL_REVIEWED")

            # 8. Verify sealed intake package and execution receipt
            intake_pkg = output_dir / "intake_package.json"
            self.assertTrue(intake_pkg.exists())
            receipt_path = output_dir / "pipeline_execution_receipt.json"
            self.assertTrue(receipt_path.exists())
            receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data.get("terminal_outcome"), "COMPLETE")


if __name__ == "__main__":
    unittest.main()
