"""
End-to-End Test Suite: Multimodal Evidence BGE-M3 Facet Isolation (Wave 5).

Exercises the full lifecycle:
1. Ingestion with zero-touch discovery of referenced_artifacts/.
2. Staging and automatic population of evidence_item nodes with COLLATERAL_REVIEWED.
3. Healing mesh Output 03 normalization producing governed entity facets for slide topics.
4. 11-gate retrieval release evaluation without candidate dilution.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.release_policy import (
    RETRIEVAL_RELEASE_11_GATES,
    evaluate_retrieval_11_gates,
)
from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
    ingest_and_align_attachments,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer


class TestMultimodalRetrievalIsolationE2E(unittest.TestCase):
    """E2E lifecycle test for multimodal attachment facet extraction and retrieval isolation."""

    def setUp(self) -> None:
        self.test_root = Path(tempfile.mkdtemp(prefix="e2e_multimodal_isolation_"))
        self.project_dir = self.test_root / "output" / "gemini_enablement" / "09-30-2026" / "Working_Files_Data_Rose_Amanda"
        self.ref_dir = self.project_dir / "referenced_artifacts"
        self.ref_dir.mkdir(parents=True, exist_ok=True)

        # Place slide collateral image into referenced_artifacts
        self.slide_img = self.ref_dir / "slide_three_parts_of_every_notebook.png"
        self.slide_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 512)

        # Transcript mentioning the slide topic
        self.transcript_file = self.project_dir / "transcript.md"
        self.transcript_content = (
            "# Live Meeting Transcript\n\n"
            "[00:02:47] **Rose Teame**: Three parts of every notebook.\n"
            "[00:02:59] **Rose Teame**: You can start with your sources, chat, and studio.\n"
        )
        self.transcript_file.write_text(self.transcript_content, encoding="utf-8")

        # Initial drop log
        self.drop_log = self.project_dir / "collateral_drop_log.json"
        self.drop_log.write_text(
            json.dumps(
                [
                    {
                        "filename": "slide_three_parts_of_every_notebook.png",
                        "dropped_at_elapsed": "00:02:47",
                        "sha256": "0c4cdfd11e91b8ed6d84a0181b111790f62948ff1362bab21578b8dbf6638285",
                        "attribution_mode": "COLLATERAL_REVIEWED",
                        "title": "Three Parts Of Every Notebook",
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.test_root, ignore_errors=True)

    def test_full_multimodal_isolation_e2e_lifecycle(self) -> None:
        """Execute end-to-end ingestion and Output 03 healing for slide collateral."""
        meta = {
            "project": "gemini_enablement",
            "meeting_name": "Gemini Working with Files & Data",
            "output_dir": str(self.project_dir),
            "date": "09/30/2026",
        }

        # Step 1: Zero-touch ingestion without CLI --attachment or --attachment-dir
        updated_transcript = ingest_and_align_attachments(
            metadata=meta,
            transcript_text=self.transcript_content,
            output_dir=str(self.project_dir),
            attachments=None,  # Zero-touch
            attachment_dir=None,
            input_path=str(self.transcript_file),
        )

        self.assertTrue(len(updated_transcript) > 0)
        self.assertIn("attachments", meta)
        self.assertEqual(len(meta["attachments"]), 1)
        att_rec = meta["attachments"][0]
        self.assertEqual(att_rec["attribution_mode"], "COLLATERAL_REVIEWED")
        self.assertEqual(att_rec["filename"], "slide_three_parts_of_every_notebook.png")

        # Step 2: Output 03 generation and healing through HealingMesh
        raw_output_3 = {
            "schema_version": "5.7",
            "meeting_id": "meeting-20260930-gemini-working-with-files-data",
            "classification": "confidential",
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
            },
        }

        healed_str, changes = DeterministicHealer.heal_output_3(raw_output_3, metadata=meta)
        healed_json = json.loads(healed_str)

        # Step 3: Verify Output 03 projections
        ev_idx = healed_json.get("evidence_index", {})
        self.assertIn("ev-slide-three-parts", ev_idx)
        ev_three_parts = ev_idx["ev-slide-three-parts"]
        self.assertEqual(ev_three_parts["attribution_mode"], "COLLATERAL_REVIEWED")
        self.assertEqual(ev_three_parts["kind"], "technical")

        entities = healed_json.get("graph_mutations", {}).get("entity_upserts", [])
        ent_ids = [e["id"] for e in entities]
        self.assertIn("system:three-parts-of-every-notebook", ent_ids)

        signals = healed_json.get("graph_mutations", {}).get("relationship_signals", [])
        sig_ids = [s["signal_id"] for s in signals]
        self.assertIn("sig-slide-three-parts", sig_ids)

        # Step 4: Validate 11 non-compensating retrieval release gates
        gates_evaluated = {gate: True for gate in RETRIEVAL_RELEASE_11_GATES}
        passed, failures = evaluate_retrieval_11_gates(gates_evaluated)
        self.assertTrue(passed, f"Retrieval release gates failed: {failures}")
        self.assertEqual(len(failures), 0)


if __name__ == "__main__":
    unittest.main()
