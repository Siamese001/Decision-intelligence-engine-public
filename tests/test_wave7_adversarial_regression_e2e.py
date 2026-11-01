"""
tests/test_wave7_adversarial_regression_e2e.py

End-to-End Adversarial Regression Suite for Wave 7:
1. Full Type B meeting replay: Slides_Jen_Amit (dialogue intake -> 01, 02, 03 + GraphDB).
2. Full Type A document replay: Sprint_Mandy (document/signal intake -> 03 only + GraphDB).
3. Adversarial boundary & malformed payload rejection (corrupt JSON, empty files).
4. Adversarial backdoor bypass rejection (execute_hemant_desai_judge.py decommissioned).
5. Adversarial forbidden consulting headers on technical syncs (Gate 2 fails closed).
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.prompts import MeetingArchetype
from dual_pipeline.transcript_qa import run_qa_gate_2
from tools.validate_output_hierarchy import validate_meeting_directory

ROOT = Path(__file__).resolve().parent.parent


class TestWave7AdversarialRegressionE2E(unittest.TestCase):
    """Full lifecycle E2E replay and adversarial regression suite for Wave 7."""

    def test_type_b_slides_jen_amit_e2e(self) -> None:
        """Type B Meeting Dialogue Replay: Slides_Jen_Amit produces 01, 02, 03 + GraphDB."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "Slides_Jen_Amit_transcript.txt"
            source_file.write_text(
                "**Jennifer Pan:** [00:00:05] Amit, let's align on the Phase 1 architecture deck slides.\n"
                "**Amit Ayer:** [00:00:25] We mapped the Unifier and LAMS data flows to eliminate manual purple sheet reconciliation.\n"
                "**Jennifer Pan:** [00:01:10] The executive summary must emphasize developer capacity expansion and risk mitigation.\n"
                "**Amit Ayer:** [00:02:00] We will deliver the initial release package by 10/01/2026.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "project_lens" / "09-25-2026" / "Phase1_Designs_Jen_Amit"
            out_dir.mkdir(parents=True, exist_ok=True)

            def fake_publish(package_path, metadata, written, **kwargs):
                written["intake_publish"] = {
                    "status": "COMPLETE",
                    "mutation_summary": {"status": "PASS", "nodes_written": 3, "assertions_written": 2},
                }

            with patch("dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph", side_effect=fake_publish):
                pipeline = CanonicalAgenticPipeline(
                    provider_type="offline",
                    model_name="gpt-5.6-sol",
                    reasoning_effort="high",
                )
                result = pipeline.run(
                    input_path=source_file,
                    metadata={
                        "project": "project_lens",
                        "meeting_name": "Phase1_Designs_Jen_Amit",
                        "date": "09/25/2026",
                        "time": "02:30 PM - 02:35 PM ET",
                        "attendees": "Amit Ayer, Jennifer Pan",
                        "classification": "internal",
                    },
                    output_dir=str(out_dir),
                    mutate_graph=True,
                    directive_id="dir-slides-001",
                    reviewer="Amit Ayer",
                    rationale="Phase 1 architecture review",
                )

            # Contractual verification of all 3 outputs
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["modality"], CanonicalModality.MEETING_TRANSCRIPT.value)
            self.assertTrue(Path(result["01_transcript"]).exists())
            self.assertTrue(Path(result["02_recap"]).exists())
            self.assertTrue(Path(result["03_graph"]).exists())
            self.assertTrue(Path(result["intake_package"]).exists())
            self.assertIn("graph_mutation", result)

            # Output 02 structural normalization verification
            recap_text = Path(result["02_recap"]).read_text(encoding="utf-8")
            self.assertTrue(re.search(r"#{1,3}\s+Meeting Objective", recap_text))
            self.assertTrue(re.search(r"#{1,3}\s+Key Discussion Points & Findings", recap_text))
            self.assertTrue(re.search(r"#{1,3}\s+Action & Governance Ledger", recap_text))
            self.assertTrue(re.search(r"#{1,3}\s+Appendix: Detailed Stakeholder Perspectives", recap_text))

            # Assert complete absence of legacy headers
            for legacy_hdr in ("Next Steps & Action Items", "Open Decisions / Blockers", "Executive Decision"):
                self.assertFalse(bool(re.search(rf"#{{1,3}}\s+{re.escape(legacy_hdr)}", recap_text)))

            # Hierarchy validation
            violations = validate_meeting_directory(out_dir, project_name="project_lens")
            self.assertEqual(violations, [], f"Meeting container hierarchy must be valid: {violations}")

    def test_type_a_sprint_mandy_document_signal_e2e(self) -> None:
        """Type A Document Replay: Sprint_Mandy produces strictly Output 03 and mutates graph."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            doc_file = tmp_path / "Sprint_Mandy_Planning.md"
            doc_file.write_text(
                "# Sprint Planning & Scope Specification - harper Hoffman\n\n"
                "## Project Context\n"
                "Governs the sprint scope for Apex Energy Transmission asset data ingestion.\n\n"
                "## System Boundaries\n"
                "- System: system:transmission_telemetry calibrated.\n"
                "- Decision: decision:quarantine_unverified_feeds ratified.\n",
                encoding="utf-8",
            )

            out_dir = tmp_path / "output" / "project_lens" / "09-25-2026" / "Sprint_Planning_Mandy"
            out_dir.mkdir(parents=True, exist_ok=True)

            def fake_publish(package_path, metadata, written, **kwargs):
                written["intake_publish"] = {
                    "status": "COMPLETE",
                    "mutation_summary": {"status": "PASS", "nodes_written": 2, "assertions_written": 1},
                }

            with patch("dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph", side_effect=fake_publish):
                pipeline = CanonicalAgenticPipeline(
                    provider_type="offline",
                    model_name="gpt-5.6-sol",
                    reasoning_effort="high",
                )
                result = pipeline.run(
                    input_path=doc_file,
                    metadata={
                        "project": "project_lens",
                        "title": "Sprint Planning Mandy",
                        "date": "09/25/2026",
                        "document": True,
                        "classification": "confidential",
                        "author": "harper Hoffman",
                    },
                    output_dir=str(out_dir),
                    mutate_graph=True,
                    directive_id="dir-sprint-mandy-001",
                    reviewer="Amit Ayer",
                    rationale="Sprint planning document intake",
                )

            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["modality"], CanonicalModality.DOCUMENT.value)
            self.assertTrue(result.get("output3_only"))
            self.assertIsNone(result.get("01_transcript"))
            self.assertIsNone(result.get("02_recap"))
            self.assertTrue(Path(result["03_graph"]).exists())
            self.assertIn("graph_mutation", result)

            # Ensure 01 and 02 do not exist on disk
            self.assertEqual(len(list(out_dir.glob("01_*"))), 0)
            self.assertEqual(len(list(out_dir.glob("02_*"))), 0)
            self.assertEqual(len(list(out_dir.glob("03_*"))), 1)

    def test_adversarial_corrupt_and_malformed_inputs(self) -> None:
        """Adversarial Test: Corrupt JSON signal envelopes fail closed cleanly without crashing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            corrupt_file = tmp_path / "03_corrupt_projection.json"
            corrupt_file.write_text("{ unclosed_json_syntax: 123 ", encoding="utf-8")

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            with self.assertRaises((json.JSONDecodeError, ValueError, RuntimeError)):
                pipeline.run(
                    input_path=corrupt_file,
                    metadata={"project": "project_lens", "date": "09/25/2026"},
                    output_dir=str(tmp_path / "output_corrupt"),
                )

    def test_adversarial_backdoor_bypass_rejected(self) -> None:
        """Adversarial Test: Invoking decommissioned backdoor script exits code 1."""
        script_path = ROOT / "scripts" / "execute_hemant_desai_judge.py"
        res = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("GOVERNANCE VIOLATION", res.stderr + res.stdout)
        self.assertIn("decommissioned", (res.stderr + res.stdout).lower())

    def test_adversarial_forbidden_headers_rejected_by_gate2(self) -> None:
        """Adversarial Test: Injected forbidden consulting headers fail QA Gate 2 closed."""
        forbidden_recap = """# Subject: Architecture Review -- Executive Recap | 09/25/2026
**Date:** 09/25/2026
**Time:** 10:00 AM - 10:30 AM ET
**Attendees:**
- PartnerAdvisory: Amit Ayer (Lead Architect)

## Meeting Objective
Review system architecture and ingestion boundaries.

## Key Discussion Points & Findings
- Finding 1: Pipeline operates within sub-second latencies.

## Business Meaning and Value Case
Enterprise consulting value proposition and market monetization thesis.

## Immediate Delivery Scope & Release Boundaries
In scope: bounded consulting wedge. Out of scope: production cutover.

## Action & Governance Ledger
| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Complete pipeline validation | Amit Ayer | 10/01/2026 | [00:01:00] | Proposed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
### Amit Ayer (PartnerAdvisory, Lead Architect)
- **Architecture**: Ingestion guarantees zero unannotated timestamp gaps.
"""
        gate_res = run_qa_gate_2(
            forbidden_recap,
            metadata={"archetype": MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value, "duration_minutes": 30},
        )
        self.assertEqual(gate_res["status"], "FAIL")
        self.assertTrue(
            any("GATE_2_FORBIDDEN_SECTION_DETECTED" in f for f in gate_res.get("failures", [])),
            f"Expected forbidden section detection failure: {gate_res.get('failures')}",
        )


if __name__ == "__main__":
    unittest.main()
