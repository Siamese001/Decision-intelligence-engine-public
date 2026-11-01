"""
tests/test_ingestion_typology_governance_e2e.py

End-to-End test suite verifying Ingestion Typology Governance under the Single Governed Entrypoint:
1. Type A (Document / Signal Envelope): Generates strictly Output 03 + intake package + receipts;
   Output 01 and Output 02 are bypassed by contract with zero disk footprint.
2. Type B (Meeting Audio / Spoken Dialogue / Transcript): Generates all three deliverables
   (01 Forensic Transcript, 02 Executive Recap, 03 Canonical Signal Projection) + receipts.
3. Model Resolution: Verifies gpt-5.6-sol binding under ModelTier.DIALECTICAL_JUDGE.
4. Adversarial Compliance: Asserts that accidental or malicious 01/02 files in Type A containers
   are caught and rejected by [PROHIBITED_DOCUMENT_DELIVERABLE].
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.canonical_pipeline import (
    CanonicalAgenticPipeline,
    CanonicalModality,
)
from dual_pipeline.model_registry import ModelRegistry, ModelTier
from tools.validate_output_hierarchy import validate_meeting_directory

ROOT = Path(__file__).resolve().parent.parent


class TestIngestionTypologyGovernanceE2E(unittest.TestCase):
    def test_model_resolution_gpt56_sol(self) -> None:
        """Verify gpt-5.6-sol resolves correctly with reasoning_effort='max'."""
        model_name, effort = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.assertEqual(model_name, "gpt-5.6-sol")
        self.assertEqual(effort, "max")

    def test_type_a_document_ingestion_e2e(self) -> None:
        """
        Type A Ingestion (Document):
        Produces strictly Output 03. Output 01 and Output 02 must NOT exist on disk.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            doc_file = tmp_path / "architecture_rfc.md"
            doc_file.write_text(
                "# Architecture Specification RFC 404\n\n"
                "## Overview\n"
                "This document specifies the technical design for the NEE Universal Ingestion Gateway.\n\n"
                "## Decisions\n"
                "- System: system:universal-gateway implements 7-stage pipeline.\n"
                "- Decision: decision:single-entrypoint enforce zero backdoors.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "project_lens" / "09-25-2026" / "Architecture_RFC_404"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = CanonicalAgenticPipeline(
                provider_type="offline",
                model_name="gpt-5.6-sol",
                reasoning_effort="max",
            )
            result = pipeline.run(
                input_path=doc_file,
                metadata={
                    "project": "project_lens",
                    "title": "Architecture RFC 404",
                    "date": "09/25/2026",
                    "document": True,
                    "classification": "internal",
                    "author": "Amit Ayer",
                },
                output_dir=str(out_dir),
            )

            # Contract verifications
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["modality"], CanonicalModality.DOCUMENT.value)
            self.assertTrue(result.get("output3_only"))
            self.assertIsNone(result.get("01_transcript"))
            self.assertIsNone(result.get("02_recap"))
            self.assertTrue(Path(result["03_graph"]).exists())
            self.assertTrue(Path(result["intake_package"]).exists())
            self.assertTrue(Path(result["execution_receipt"]).exists())

            # Disk file isolation verifications
            out_p = Path(result["output_dir"])
            f01_files = list(out_p.glob("01_*"))
            f02_files = list(out_p.glob("02_*"))
            f03_files = list(out_p.glob("03_*_canonical_signal_projection.json"))

            self.assertEqual(len(f01_files), 0, "Deliverable 01 must NOT exist in Type A container")
            self.assertEqual(len(f02_files), 0, "Deliverable 02 must NOT exist in Type A container")
            self.assertEqual(len(f03_files), 1, "Deliverable 03 must exist exactly once")

            # Hierarchy compliance verification
            violations = validate_meeting_directory(out_p, project_name="project_lens")
            self.assertEqual(violations, [], f"Output container should be valid: {violations}")

    def test_type_a_signal_envelope_ingestion_e2e(self) -> None:
        """
        Type A Ingestion (Pre-Packaged Signal Envelope / JSON Projection):
        Input is a raw JSON signal projection / envelope.
        Produces strictly Output 03 and authorizes graph mutation.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            envelope_data = {
                "schema": "urn:nee:dual-pipeline:canonical-signal-projection:5.7.0",
                "meeting_name": "Turbine_Sensor_Feed",
                "date": "09/25/2026",
                "signals": [
                    {
                        "source_statement": "Wind turbine telemetry calibrated.",
                        "kind": "technical",
                        "topic": "telemetry",
                        "entity_ids": ["system:scada_feed"],
                    }
                ],
                "graph_mutations": {
                    "entity_upserts": [
                        {"id": "system:scada_feed", "name": "SCADA Feed", "type": "system"}
                    ]
                },
            }
            envelope_file = tmp_path / "03_Turbine_Sensor_Feed_09-25-2026_canonical_signal_projection.json"
            envelope_file.write_text(json.dumps(envelope_data, indent=2), encoding="utf-8")

            out_dir = tmp_path / "output" / "project_lens" / "09-25-2026" / "Turbine_Sensor_Feed"
            out_dir.mkdir(parents=True, exist_ok=True)

            def fake_publish(package_path, metadata, written, **kwargs):
                written["intake_publish"] = {"status": "COMPLETE", "mutation_summary": {"status": "PASS", "entities_written": 1}}

            with patch("dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph", side_effect=fake_publish) as mock_pub:
                pipeline = CanonicalAgenticPipeline(
                    provider_type="offline",
                    model_name="gpt-5.6-sol",
                    reasoning_effort="max",
                )
                result = pipeline.run(
                    input_path=envelope_file,
                    metadata={
                        "project": "project_lens",
                        "title": "Turbine Sensor Feed",
                        "date": "09/25/2026",
                        "classification": "confidential",
                    },
                    output_dir=str(out_dir),
                    mutate_graph=True,
                    directive_id="dir-sensor-001",
                    reviewer="Amit Ayer",
                    rationale="Sensor telemetry intake",
                )

                self.assertEqual(result["status"], "COMPLETE")
                self.assertEqual(result["modality"], CanonicalModality.DOCUMENT.value)
                self.assertTrue(result.get("output3_only"))
                self.assertIsNone(result.get("01_transcript"))
                self.assertIsNone(result.get("02_recap"))
                self.assertTrue(Path(result["03_graph"]).exists())
                self.assertIn("graph_mutation", result)
                mock_pub.assert_called_once()

    def test_type_b_meeting_dialogue_ingestion_e2e(self) -> None:
        """
        Type B Ingestion (Meeting Audio / Spoken Dialogue / Transcript):
        Produces all three deliverables (01 Forensic Transcript, 02 Executive Recap, 03 Canonical Signal Projection).
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            meeting_transcript = tmp_path / "meeting_audio.txt"
            meeting_transcript.write_text(
                "**harper Hoffman:** [00:00:05] Today we are reviewing the wind farm SOW deliverables.\n"
                "**Amit Ayer:** [00:00:20] We have validated 150 turbines across 5 generation sites.\n"
                "**Jennifer Pan:** [00:01:05] Engineering signoff will be granted by end of week.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "project_lens" / "09-25-2026" / "Wind_Farm_SOW_Review"
            out_dir.mkdir(parents=True, exist_ok=True)

            pipeline = CanonicalAgenticPipeline(
                provider_type="offline",
                model_name="gpt-5.6-sol",
                reasoning_effort="max",
            )
            result = pipeline.run(
                input_path=meeting_transcript,
                metadata={
                    "project": "project_lens",
                    "meeting_name": "Wind_Farm_SOW_Review",
                    "date": "09/25/2026",
                    "time": "10:00 AM - 10:30 AM ET",
                    "attendees": "harper Hoffman, Amit Ayer, Jennifer Pan",
                    "classification": "confidential",
                },
                output_dir=str(out_dir),
            )

            # Contract verifications
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["modality"], CanonicalModality.MEETING_TRANSCRIPT.value)
            self.assertFalse(result.get("output3_only", False))

            # All three deliverables must exist on disk and in result
            self.assertIsNotNone(result.get("01_transcript"))
            self.assertIsNotNone(result.get("02_recap"))
            self.assertIsNotNone(result.get("03_graph"))

            p01 = Path(result["01_transcript"])
            p02 = Path(result["02_recap"])
            p03 = Path(result["03_graph"])

            self.assertTrue(p01.exists() and p01.stat().st_size > 0)
            self.assertTrue(p02.exists() and p02.stat().st_size > 0)
            self.assertTrue(p03.exists() and p03.stat().st_size > 0)

            # Hierarchy compliance verification
            violations = validate_meeting_directory(Path(result["output_dir"]), project_name="project_lens")
            self.assertEqual(violations, [], f"Meeting container should have zero violations: {violations}")

    def test_type_a_adversarial_prohibited_deliverables_rejected(self) -> None:
        """
        Adversarial Test:
        If an operator or compromised routine places an unauthorized 01 or 02 file
        into a Type A document container, directory compliance MUST fail closed
        with [PROHIBITED_DOCUMENT_DELIVERABLE].
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            out_dir = tmp_path / "output" / "project_lens" / "09-25-2026" / "Doc_Adversarial_Test"
            out_dir.mkdir(parents=True, exist_ok=True)

            # Write valid document metadata
            (out_dir / "metadata.json").write_text(
                json.dumps({"is_document": True, "output3_only": True, "project": "project_lens", "date": "09/25/2026"}),
                encoding="utf-8",
            )
            # Write valid 03 deliverable
            (out_dir / "03_Doc_Adversarial_Test_09-25-2026_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
            (out_dir / "qa_gates_report.json").write_text("{}", encoding="utf-8")
            (out_dir / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")
            (out_dir / "final_release_decision.json").write_text("{}", encoding="utf-8")
            (out_dir / "intake_package.json").write_text("{}", encoding="utf-8")

            # Initially compliant
            violations = validate_meeting_directory(out_dir, project_name="project_lens")
            self.assertEqual(violations, [])

            # Adversarially inject a prohibited 01 file
            bad_01 = out_dir / "01_Doc_Adversarial_Test_09-25-2026_hitl_resolved_transcript.md"
            bad_01.write_text("# Sneaked Transcript", encoding="utf-8")

            violations = validate_meeting_directory(out_dir, project_name="project_lens")
            self.assertTrue(any("[PROHIBITED_DOCUMENT_DELIVERABLE]" in v for v in violations))


if __name__ == "__main__":
    unittest.main()
