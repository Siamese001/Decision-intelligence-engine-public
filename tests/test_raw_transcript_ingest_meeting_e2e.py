"""End-to-end integration tests for raw transcript ingestion via RawTranscriptPipeline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline


def test_raw_transcript_ingestion_end_to_end_offline() -> None:
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        raw_transcript_path = temp_dir / "raw_input_transcript.md"
        raw_text = (
            "# Project LENS Live Meeting Feed\n\n"
            "**harper Hoffman:** [03:12] We reviewed the SSW with Amit Air and Solom. "
            "We checked the figlioli pitch pitch on the turbine at La Paz.\n"
            "**Amit Ayer:** [04:00] The rule creation tool had instability earlier this week in Dev.\n"
            "**harper Hoffman:** [07:12] Thank you everyone.\n"
        )
        raw_transcript_path.write_text(raw_text, encoding="utf-8")

        metadata = {
            "meeting_name": "Project_LENS_Phase_II_Catchup",
            "date": "09/11/2026",
            "date_filename": "09-11-2026",
            "time": "04:32 PM - 04:37 PM ET",
            "time_filename": "0432PM_0437PM_ET",
            "duration_minutes": 5,
            "platform": "Microsoft Teams",
            "classification": "confidential",
            "audiences": ["nea-product", "partner_advisory-delivery", "project-lens-team", "internal_consulting", "nee-engineering"],
            "meeting_type": "EXECUTIVE_ALIGNMENT_REBASELINE",
            "attendees": {
                "Apex Global Enterprises (NEA)": [
                    "harper Hoffman (Product Manager)",
                ],
                "PartnerAdvisory": [
                    "Amit Ayer (Lead Enterprise Architect)",
                ],
            },
            "roster": [
                {"name": "harper Hoffman", "role": "Product Manager"},
                {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
            ],
            "expected_domain_terms": [
                "Bonfiglioli",
                "La Casa",
                "SteerCo",
            ],
            "cutoff_marker": "**harper Hoffman:** [07:12]",
        }

        # Initialize pipeline in deterministic offline mode
        pipeline = RawTranscriptPipeline(provider_type="offline")

        result = pipeline.run(
            input_path=str(raw_transcript_path),
            metadata=metadata,
            output_dir=str(temp_dir),
            mutate_graph=False,
        )

        assert result is not None
        assert "01_transcript" in result
        assert "02_recap" in result
        assert "03_graph" in result

        out_01_path = Path(result["01_transcript"])
        out_02_path = Path(result["02_recap"])
        out_03_path = Path(result["03_graph"])

        assert out_01_path.exists()
        assert out_02_path.exists()
        assert out_03_path.exists()

        f1_content = out_01_path.read_text(encoding="utf-8")
        assert "Amit Air" not in f1_content
        assert "Amit Ayer" in f1_content
        assert "SSW" not in f1_content
        assert "SOW" in f1_content
        assert "Solom" not in f1_content
        assert "PartnerAdvisory" in f1_content
        assert "La Paz" not in f1_content
        assert "La Casa" in f1_content

        f3_content = json.loads(out_03_path.read_text(encoding="utf-8"))
        assert f3_content["schema_version"] == "5.7"
        assert f3_content["graph_mutations"]["action_updates"] == []

        # Validate Pydantic schema model
        validated_model = CanonicalSignalProjectionV57.model_validate(f3_content)
        assert validated_model.schema_version == "5.7"

        # Verify QA Gate report and Release Decision
        qa_report_path = temp_dir / "qa_gates_report.json"
        release_decision_path = temp_dir / "final_release_decision.json"

        assert qa_report_path.exists()
        assert release_decision_path.exists()

        qa_report = json.loads(qa_report_path.read_text(encoding="utf-8"))
        assert qa_report.get("status") == "PASS"

        release_decision = json.loads(release_decision_path.read_text(encoding="utf-8"))
        assert release_decision.get("status") == "PASS"
        assert release_decision.get("authoritative_write_authorized") is True


def test_raw_transcript_ingestion_end_to_end_30m_realistic() -> None:
    """Verify end-to-end ingestion of an authentic 30-minute meeting with complete Gate 1.6 & Gate 2 passing."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        raw_transcript_path = temp_dir / "chakra_30m_transcript.md"

        # Generate 25 authentic dialogue turns across 30 minutes (>1,000 words dialogue)
        turns = []
        turn_text = (
            "We confirmed the operational alignment for the enterprise decision intelligence pipeline. "
            "Amit Ayer and Cameron Mercer reviewed the telemetry ingestion flow and verified zero concurrency lock "
            "contention across SQLite tables when write-ahead-logging and 30-second busy timeouts are active. "
            "The delivery pod is on track for phase one milestone sign-off."
        )
        for i in range(25):
            speaker = "Cameron Mercer" if i % 2 == 0 else "Amit Ayer"
            turns.append(f"### [00:{i:02d}:00]\n\n**{speaker}:**\nTurn {i}: {turn_text}\n")
        raw_text = "# Chakra Architecture Review Live Meeting\n\n" + "".join(turns)
        raw_transcript_path.write_text(raw_text, encoding="utf-8")

        metadata = {
            "meeting_name": "Chakra_Architecture_Review",
            "date": "08/27/2026",
            "date_filename": "08-27-2026",
            "time": "09:00 AM - 09:30 AM ET",
            "time_filename": "0900AM_0930AM_ET",
            "duration_minutes": 30,
            "platform": "Microsoft Teams",
            "classification": "confidential",
            "audiences": ["partner_advisory-delivery", "nea-product", "internal_consulting"],
            "meeting_type": "STRATEGIC_DISCOVERY",
            "attendees": {
                "Apex Global Enterprises (NEA)": ["Cameron Mercer (Executive)"],
                "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
            },
            "roster": [
                {"name": "Cameron Mercer", "role": "Executive"},
                {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
            ],
        }

        pipeline = RawTranscriptPipeline(provider_type="offline")
        result = pipeline.run(
            input_path=str(raw_transcript_path),
            metadata=metadata,
            output_dir=str(temp_dir),
            mutate_graph=False,
        )

        assert result is not None
        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()

        qa_report = json.loads((temp_dir / "qa_gates_report.json").read_text(encoding="utf-8"))
        assert qa_report.get("status") == "PASS"

        # Explicitly assert Gate 1.6 and Gate 2.1-2.7 passed for the 30m meeting
        gate_1_6 = qa_report["output_01_forensic_transcript"]["gates"]["Gate_1_6_Duration_Turn_Density"]
        assert gate_1_6["passed"] is True
        assert gate_1_6["total_turns"] >= 21
        assert gate_1_6["total_words"] >= 900

        gate_2_sections = qa_report["output_02_executive_recap"]["gates"]["Gate_2_1_Parsed_Structural_Completeness"]
        assert gate_2_sections["passed"] is True
        assert gate_2_sections["sections"]["Meeting Objective"] is True
        assert gate_2_sections["sections"]["Action & Governance Ledger"] is True

        release_decision = json.loads((temp_dir / "final_release_decision.json").read_text(encoding="utf-8"))
        assert release_decision.get("status") == "PASS"
        assert release_decision.get("authoritative_write_authorized") is True


import unittest


class RawTranscriptIngestMeetingE2ETests(unittest.TestCase):
    """Unittest TestCase wrapper ensuring both unittest and pytest discovery."""

    def test_raw_transcript_ingestion_end_to_end_offline(self):
        test_raw_transcript_ingestion_end_to_end_offline()

    def test_raw_transcript_ingestion_end_to_end_30m_realistic(self):
        test_raw_transcript_ingestion_end_to_end_30m_realistic()


if __name__ == "__main__":
    unittest.main()

