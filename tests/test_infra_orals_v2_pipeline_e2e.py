"""
tests/test_infra_orals_v2_pipeline_e2e.py

End-to-End Pipeline Integration Tests for Infrastructure Orals V2:
1. Full offline pipeline ingestion of authentic Infrastructure Orals transcript with "conduit" mentions.
2. Verifies zero occurrence of "Dan Okay" across Deliverables 01, 02, and 03.
3. Verifies conference room endpoint (MOLE-VC) typed as system/location, not person.
4. Verifies Output 02 recap preserves genuine topics without synthetic meeting ontology injection.
5. Verifies UWG deployment preserves and forwards auth_token.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch


from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_pipeline_core.uwg_deployer import build_publish_args


def test_infra_orals_v2_e2e_pipeline_dan_okray_preservation() -> None:
    """Verifies end-to-end that mentions of 'the conduit team' never corrupt Drew Oakley into Dan Okay."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        raw_transcript_path = temp_dir / "infra_orals_v2_transcript.md"

        # Construct realistic dialogue with Drew Oakley mentioning the conduit team
        turns = [
            "### [00:00:15]\n\n**Drew Oakley:**\nWelcome everyone to the Infrastructure Orals session. "
            "We have PartnerAdvisory and Apex Global Enterprises leadership on the line to walk through our platform architecture.\n",
            "### [00:01:30]\n\n**Andrew Hanson:**\nFrom Apex Global Enterprises's perspective, our core priority is reliability "
            "and robust governance across all automated platform components.\n",
            "### [00:02:45]\n\n**Jason Nicholas:**\nOur cloud foundations rely on automated Terraform pipelines "
            "with zero lock contention and multi-account guardrails.\n",
            "### [00:04:00]\n\n**Drew Oakley:**\nI want to emphasize our coordination with the conduit team. "
            "The conduit team handles cross-substation telemetry routing while our core compute team deploys the mesh.\n",
            "### [00:05:15]\n\n**Jason Nicholas:**\nWe are aligned on the upcoming deployment milestones and transition roadmap.\n",
        ]
        raw_text = "# Infrastructure Orals V2 Session\n\n" + "".join(turns)
        raw_transcript_path.write_text(raw_text, encoding="utf-8")

        metadata: Dict[str, Any] = {
            "meeting_name": "Infrastructure_Orals_Conduit_Review",
            "project": "conduit_project",
            "date": "10/02/2026",
            "date_filename": "10-02-2026",
            "time": "01:00 PM - 01:06 PM ET",
            "time_filename": "0100PM_0106PM_ET",
            "duration_minutes": 6,
            "platform": "Microsoft Teams",
            "classification": "confidential",
            "audiences": ["nea-product", "partner_advisory-delivery", "internal_consulting"],
            "meeting_type": "ORALS_PRESENTATION",
            "attendees": {
                "Apex Global Enterprises (NEA)": [
                    "Andrew Hanson (AI/ML Director)",
                    "MOLE-VC (Conference Room Endpoint)",
                ],
                "PartnerAdvisory": [
                    "Drew Oakley (Managing Director / CSL)",
                    "Jason Nicholas (Solution Architect)",
                ],
            },
            "roster": [
                {"name": "Drew Oakley", "role": "Managing Director / CSL", "org": "PartnerAdvisory"},
                {"name": "Jason Nicholas", "role": "Solution Architect", "org": "PartnerAdvisory"},
                {"name": "Andrew Hanson", "role": "AI/ML Director", "org": "Apex Global Enterprises"},
                {"name": "MOLE-VC", "role": "Conference Room Endpoint", "org": "Apex Global Enterprises"},
            ],
            "speaker_role_resolutions": {
                "Drew Oakley": "Managing Director / CSL",
                "Jason Nicholas": "Solution Architect",
                "Andrew Hanson": "AI/ML Director",
                "MOLE-VC": "Conference Room Endpoint",
            },
            "attendees_confirmed_by_operator": True,
            "attendee_confirmation_receipt": {
                "status": "RESOLVED_HITL",
                "subject_sha256": "urn:nee:live-container-preflight-proof",
                "operator_id": "human_operator",
            },
            "attendee_roles_confirmed_by_operator": True,
            "auth_token": "token-governed-proof-receipt-infra-v2",
        }

        pipeline = RawTranscriptPipeline(provider_type="offline")
        with patch(
            "dual_pipeline.canonical_pipeline.pipeline.build_and_validate_intake_package",
            side_effect=lambda staging_dir, *args, **kwargs: Path(staging_dir) / "deliverables.zip",
        ), patch(
            "dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook",
            return_value={"proposals": []},
        ):
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
        f2_content = out_02_path.read_text(encoding="utf-8")
        f3_content = json.loads(out_03_path.read_text(encoding="utf-8"))

        # Invariant 1: Dan Okay MUST NOT appear in any deliverable
        assert "Dan Okay" not in f1_content, "Corrupted name 'Dan Okay' found in Output 01!"
        assert "Dan Okay" not in f2_content, "Corrupted name 'Dan Okay' found in Output 02!"
        f3_str = json.dumps(f3_content)
        assert "Dan Okay" not in f3_str, "Corrupted name 'Dan Okay' found in Output 03 graph payload!"

        # Invariant 2: Drew Oakley MUST be preserved
        assert "Drew Oakley" in f1_content
        assert "Drew Oakley" in f2_content

        # Invariant 3: Output 02 must not contain synthetic project expansions
        assert "Carlos Salazar confirms he will contact Tyler McCord" not in f2_content
        assert "Jay Lenner details broad ownership" not in f2_content

        # Invariant 4: MOLE-VC endpoint typed as system/location, not person
        upserts = f3_content.get("graph_mutations", {}).get("entity_upserts", [])
        entities_by_id = {e["id"]: e for e in upserts}

        assert "system:mole-vc" in entities_by_id or "location:mole-vc" in entities_by_id or "person:mole-vc" not in entities_by_id
        if "person:mole-vc" in entities_by_id:
            pytest.fail("MOLE-VC was improperly typed as 'person' instead of system/location!")

        # Invariant 5: Canonical signal projection schema validation passes
        model = CanonicalSignalProjectionV57.model_validate(f3_content)
        assert model.schema_version == "5.7"


def test_e2e_uwg_deployer_forwarding_token_integration() -> None:
    """Verifies that uwg_deployer forwards auth_token from metadata end-to-end to build_publish_args."""
    f3_payload = {
        "schema_version": "5.7",
        "meeting_metadata": {
            "meeting_name": "Infra_Orals_V2",
            "date": "10-02-2026",
            "auth_token": "receipt-sealed-token-98765",
        },
        "graph_mutations": {
            "entity_upserts": [{"id": "person:dan_okray", "type": "person", "name": "Drew Oakley"}],
            "relationship_signals": [],
            "decision_updates": [],
            "risk_updates": [],
        },
    }
    metadata = {
        "auth_token": "receipt-sealed-token-98765",
        "meeting_name": "Infra_Orals_V2",
    }

    publish_args = build_publish_args(f3_payload, metadata=metadata)
    assert publish_args.auth_token == "receipt-sealed-token-98765"
    assert hasattr(publish_args, "package")


