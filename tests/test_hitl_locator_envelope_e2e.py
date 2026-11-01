"""
tests/test_hitl_locator_envelope_e2e.py

Class B E2E Receipt Test for Wave 3:
Validates that Output 3 signal projections containing TRANSCRIPT_HITL evidence items
with locator: {"kind": "hitl_decision_ledger", "value": "hitl"} resolve deterministically
during prepare_package() without crashing on "section 'hitl'", and that the resulting
intake package strictly validates against dual_pipeline schemas and seals canonical digests.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import artifact_digest, read_json
from tools.prepare_evidence_envelope_intake import prepare_package


SAMPLE_TRANSCRIPT = """Oct 13, 2026
Apex Global (int) | Weekly PartnerAdvisory & GCP Alignment (See Agenda) - Transcript
00:00:25

Anya Gayvoronskaya: Hi, Alex.
Alex Brannan: Yeah. How are you?
Anya Gayvoronskaya: I'm good. How are you today?
Alex Brannan: I'm good. Hey Diane. Hey Brian.
Bryan Armalavage: Hey, good afternoon folks.
Alex Brannan: Interrupt him.
Robson N. Silva: Hey, good afternoon everyone.
Anya Gayvoronskaya: Hi everyone. I'm pinging our team. They should be on shortly.
Amit Ayer: Hey everyone,
Anya Gayvoronskaya: Hi Amit.
Amit Ayer: can you guys hear me?
Anya Gayvoronskaya: Yes. Do you know if Dan's joining today?
Amit Ayer: Dan cannot join. No.
Anya Gayvoronskaya: Okay. Okay. Anyone else from the following side potentially?
Amit Ayer: Um, yeah, we should ping.

00:03:30

Rusty Gattmann: Anya team, sorry I'm a couple minutes late. Congratulations. I saw that correctly. Like you got awarded the automation RFP. Did I see that correctly?
Amit Ayer: We did.
Rusty Gattmann: That's huge, right?
Amit Ayer: Yeah, that's very big. We're happy about that.
Chad Brockman: Nice.

00:07:53

Alex Brannan: I also want to introduce Vani Eswarappa as the partner engineer dedicated to PartnerAdvisory.
Rusty Gattmann: Yes, Vani is our dedicated partner engineer. Make sure Chad Brockman and Robson Silva connect with her.
Vani Eswarappa: Hi everyone, excited to work together on Apex Global initiatives.
"""


def _build_projection_with_hitl_evidence() -> dict:
    """Builds a realistic V5.7 projection matching dlq_20261014_115753_c4518856."""
    return {
        "schema_version": "5.7",
        "meeting_id": "meeting-20261013-weekly-partner_advisory-gcp-alignment",
        "title": "Weekly PartnerAdvisory & GCP Alignment",
        "classification": "restricted",
        "audiences": ["nee-engineering"],
        "evidence_index": {
            "ev-01": {
                "speaker": "person:amit-ayer",
                "source_classification": "REPORTED FACT",
                "kind": "strategic",
                "text": "Amit Ayer: We did.",
                "locator": {
                    "kind": "timestamp",
                    "value": "00:03:30",
                },
                "grounding_status": "GROUNDED",
                "evidence_id": "ev-01",
                "statement_id": "ev-01",
                "exact_text": "Amit Ayer: We did.",
            },
            "ev-02": {
                "speaker": "person:alex-brannan",
                "source_classification": "FACT",
                "kind": "technical",
                "text": "Alex Brannan: I also want to introduce Vani Eswarappa as the partner engineer dedicated to PartnerAdvisory.",
                "locator": {
                    "kind": "timestamp",
                    "value": "00:07:53",
                },
                "grounding_status": "GROUNDED",
                "evidence_id": "ev-02",
                "statement_id": "ev-02",
                "exact_text": "Alex Brannan: I also want to introduce Vani Eswarappa as the partner engineer dedicated to PartnerAdvisory.",
            },
            "ev_hitl_role_alex_brannan": {
                "statement_id": "stmt_hitl_role_alex_brannan",
                "evidence_id": "ev_hitl_role_alex_brannan",
                "kind": "summary",
                "text": "Human operator verified professional title 'Partner Alliances Manager' and organization 'Google' for Alex Brannan.",
                "exact_text": "Human operator verified professional title 'Partner Alliances Manager' and organization 'Google' for Alex Brannan.",
                "grounding_status": "GROUNDED",
                "speaker": "Operator",
                "source": "TRANSCRIPT_HITL",
                "source_type": "TRANSCRIPT_HITL",
                "resolution_id": "",
                "ledger_entry_id": "",
                "locator": {
                    "kind": "hitl_decision_ledger",
                    "value": "hitl",
                },
            },
            "ev_hitl_role_rusty_gattmann": {
                "statement_id": "stmt_hitl_role_rusty_gattmann",
                "evidence_id": "ev_hitl_role_rusty_gattmann",
                "kind": "summary",
                "text": "Human operator verified professional title 'Strategic Account Executive' and organization 'Google' for Rusty Gattmann.",
                "exact_text": "Human operator verified professional title 'Strategic Account Executive' and organization 'Google' for Rusty Gattmann.",
                "grounding_status": "GROUNDED",
                "speaker": "Operator",
                "source": "TRANSCRIPT_HITL",
                "source_type": "TRANSCRIPT_HITL",
                "resolution_id": "",
                "ledger_entry_id": "",
                "locator": {
                    "kind": "hitl_decision_ledger",
                    "value": "hitl",
                },
            },
            "ev_hitl_role_chad_brockman": {
                "statement_id": "stmt_hitl_role_chad_brockman",
                "evidence_id": "ev_hitl_role_chad_brockman",
                "kind": "summary",
                "text": "Human operator verified professional title 'Customer Engineer' and organization 'Google' for Chad Brockman.",
                "exact_text": "Human operator verified professional title 'Customer Engineer' and organization 'Google' for Chad Brockman.",
                "grounding_status": "GROUNDED",
                "speaker": "Operator",
                "source": "TRANSCRIPT_HITL",
                "source_type": "TRANSCRIPT_HITL",
                "resolution_id": "",
                "ledger_entry_id": "",
                "locator": {
                    "kind": "hitl_decision_ledger",
                    "value": "hitl",
                },
            },
            "ev_hitl_role_vani_eswarappa": {
                "statement_id": "stmt_hitl_role_vani_eswarappa",
                "evidence_id": "ev_hitl_role_vani_eswarappa",
                "kind": "summary",
                "text": "Human operator verified professional title 'Partner Engineer' and organization 'Google' for Vani Eswarappa.",
                "exact_text": "Human operator verified professional title 'Partner Engineer' and organization 'Google' for Vani Eswarappa.",
                "grounding_status": "GROUNDED",
                "speaker": "Operator",
                "source": "TRANSCRIPT_HITL",
                "source_type": "TRANSCRIPT_HITL",
                "resolution_id": "",
                "ledger_entry_id": "",
                "locator": {
                    "kind": "hitl_decision_ledger",
                    "value": "hitl",
                },
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:alex_brannan",
                    "name": "Alex Brannan",
                    "type": "person",
                    "role": "Partner Alliances Manager",
                    "locator": {"kind": "hitl_decision_ledger", "value": "hitl"},
                }
            ],
            "relationship_signals": [
                {
                    "from_id": "person:alex_brannan",
                    "to_id": "org:google",
                    "relationship": "MEMBER_OF",
                    "locator": {"kind": "hitl_decision_ledger", "value": "hitl"},
                }
            ],
            "action_updates": [
                {
                    "action_id": "act-01",
                    "action": "Connect Chad Brockman and Robson Silva with Vani Eswarappa",
                    "owner": "Chad Brockman",
                    "status": "planned",
                    "locator": {"kind": "timestamp", "value": "00:07:53"},
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "decision:dec-01",
                    "decision": "Leverage Vani Eswarappa for joint PartnerAdvisory and GCP architecture reviews",
                    "status": "APPROVED",
                }
            ],
        },
    }


@pytest.mark.e2e
def test_prepare_package_with_hitl_locators_e2e():
    """Verify exact production regression: prepare_package succeeds on HITL evidence items."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        source_file = tmp_path / "PartnerAdvisory_Gcp_Weekly_Alignment.txt"
        source_file.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")

        draft_file = tmp_path / "draft_output_3.json"
        draft_data = _build_projection_with_hitl_evidence()
        draft_file.write_text(json.dumps(draft_data, indent=2), encoding="utf-8")

        output_file = tmp_path / "intake_package.json"

        # Execute prepare_package exactly as canonical pipeline lifecycle does
        result = prepare_package(
            draft_path=draft_file,
            source_path=source_file,
            output_path=output_file,
            source_location=str(source_file),
            classification="restricted",
            audiences=["nee-engineering"],
            started_at="2026-10-13T00:00:25-04:00",
            organizer="Amit Ayer",
        )

        # 1. Package was generated and written
        assert output_file.is_file()
        assert result["contract_id"] == "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0"

        package = read_json(output_file)
        validate_artifact(package)

        # 2. Canonical digest is sealed and matches
        expected_digest = artifact_digest(package)
        assert package["canonical_digest"]["value"] == expected_digest
        assert result["canonical_digest"]["value"] == expected_digest

        # 3. Speakers and statements are preserved with valid locators
        speaker_refs = [s["speaker_ref"] for s in package["speakers"]]
        assert "speaker-alex-brannan" in speaker_refs or "speaker-operator" in speaker_refs

        all_statements = [stmt for spk in package["speakers"] for stmt in spk.get("statements", [])]
        assert len(all_statements) >= 4

        # 4. Verify all locators satisfy schema: kind in {'section', 'timestamp', 'region', ...}
        for stmt in all_statements:
            loc = stmt["locator"]
            assert loc["kind"] in {"section", "timestamp", "page", "region", "cell", "record", "uri_fragment"}
            assert loc["kind"] != "hitl_decision_ledger"  # Normalized to region or timestamp
            if "Human operator verified" in stmt["text"]:
                assert loc["kind"] == "region"
                assert "hitl" in loc["value"]
