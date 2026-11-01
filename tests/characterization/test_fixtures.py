"""
tests/characterization/test_fixtures.py

Deterministic golden fixtures for characterization tests across Ingestion,
Query, and Release Evaluation baselines.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.manifest import IngestionMeetingManifest

SAMPLE_TEAMS_RAW_TRANSCRIPT = """Meeting transcript
September 12, 2026

Dan started transcription

Dan  00:01
Welcome everyone to the architecture review. Today we need to align on the LENS pipeline integration and delivery commitments.

Sarah  00:04
Yes, the technical architecture contracts between AWS S3 and the local Metal pipeline are finalized. We have established a 250 millisecond SLA.

Dan  00:08
That is confirmed. Next steps are to finalize the data contracts and deploy the ingestion worker by October 15.

Sarah  00:12
Understood. I will own the worker deployment deliverable.

Dan stopped transcription
"""

SAMPLE_CANONICAL_PROJECTION_DATA: Dict[str, Any] = {
    "schema_version": "5.7",
    "meeting_id": "PROJECT_NEXUS_Architecture_Integration_Review_09-12-2026_1000AM1030AM",
    "classification": "confidential",
    "audiences": ["partner_advisory", "client"],
    "evidence_index": {
        "STMT-01": {
            "id": "STMT-01",
            "kind": "decision",
            "speaker": "Dan",
            "source_classification": "confidential",
            "text": "Next steps are to finalize the data contracts and deploy the ingestion worker by October 15.",
            "locator": {"kind": "timestamp", "value": "00:08"},
        },
        "STMT-02": {
            "id": "STMT-02",
            "kind": "technical",
            "speaker": "Sarah",
            "source_classification": "confidential",
            "text": "technical architecture contracts between AWS S3 and the local Metal pipeline are finalized",
            "locator": {"kind": "timestamp", "value": "00:04"},
        },
    },
    "graph_mutations": {
        "entity_upserts": [
            {
                "id": "person:dan",
                "type": "Stakeholder",
                "name": "Dan",
                "mutation": "UPSERT",
            },
            {
                "id": "person:sarah",
                "type": "Stakeholder",
                "name": "Sarah",
                "mutation": "UPSERT",
            },
        ],
        "relationship_signals": [
            {
                "signal_id": "SIG-01",
                "from_id": "person:dan",
                "to_id": "person:sarah",
                "relationship": "VALIDATES",
                "evidence_ids": ["STMT-01"],
            }
        ],
        "decision_updates": [
            {
                "decision_id": "DEC-01",
                "status": "RATIFIED",
                "decision_question": "Finalize data contracts and deploy ingestion worker by October 15, 2026?",
                "rationale": "Aligned on delivery commitments",
                "observed_at": "09/12/2026",
                "evidence_ids": ["STMT-01"],
            }
        ],
        "proposal_updates": [],
        "action_updates": [
            {
                "action_id": "ACT-01",
                "owner_id": "person:sarah",
                "status": "COMMITTED",
                "due_date": "10/15/2026",
                "evidence_ids": ["STMT-01"],
            }
        ],
        "risk_updates": [],
    },
    "evidence_only_statement_ids": ["STMT-02"],
}


def get_sample_manifest() -> IngestionMeetingManifest:
    """Returns a fully populated, deterministic IngestionMeetingManifest."""
    return create_sample_manifest()


def create_sample_manifest(base_dir: Optional[Path] = None) -> IngestionMeetingManifest:
    """Creates a sample manifest optionally rooted in an isolated test directory."""
    output_root = (base_dir / "output") if base_dir else None
    intake_dir = (base_dir / "intake") if base_dir else None
    return IngestionMeetingManifest(
        project_name="project_nexus",
        meeting_title="Architecture Integration Review",
        meeting_date="09/12/2026",
        start_time_et="10:00 AM",
        end_time_et="10:30 AM",
        raw_text=SAMPLE_TEAMS_RAW_TRANSCRIPT,
        meeting_objective="Align on LENS pipeline integration and delivery commitments.",
        attendees=[
            {"name": "Dan", "company": "PartnerAdvisory", "role": "Solution Architect"},
            {"name": "Sarah", "company": "Client", "role": "Lead Engineer"},
        ],
        ambiguity_rows=[
            {
                "id": "AMB-01",
                "description": "Verified speaker attributions",
                "statements": "STMT-01",
                "status": "RESOLVED",
                "pathway": "Session verified",
            }
        ],
        f3_data=SAMPLE_CANONICAL_PROJECTION_DATA,
        output_root=output_root,
        intake_dir=intake_dir,
    )
