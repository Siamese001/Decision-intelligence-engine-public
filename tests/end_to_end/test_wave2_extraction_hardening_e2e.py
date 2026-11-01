#!/usr/bin/env python3
"""End-to-End Test Suite for Wave 2 Output 03 Extraction Hardening & Semantic Contract Validation.

Exercises:
1. SignalExtractor gating of raw projections containing conditional dialogue assertions:
   - Verifies promotion of conditional statements into proposal_updates with status PROPOSED.
   - Verifies that promoted_signal_count > 0 expands C-17 evidence ceiling beyond floor of 3.
2. post_process_stage03_projection end-to-end execution:
   - Sanitizes and validates against CanonicalSignalProjectionV57 schema.
   - Enforces action_updates is strictly empty.
3. prepare_package & normalize_evidence_envelope integration:
   - Packages Output 03 into urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0.
   - Validates semantics and decomposes into normalized candidate bundle.
"""
from __future__ import annotations

import json
import unittest

import pytest

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.normalization import normalize_evidence_envelope
from dual_pipeline.signal_extraction import SignalExtractor
from dual_pipeline.transcript_stages_core.graph_executor import post_process_stage03_projection


@pytest.mark.e2e
class Wave2ExtractionHardeningE2ETests(unittest.TestCase):
    """Full lifecycle E2E tests for Wave 2 extraction hardening."""

    def test_conditional_dialogue_promoted_to_proposals_e2e(self) -> None:
        """Verify conditional dialogue statements promote to PROPOSED proposals and expand C-17 ceiling."""
        forensic_transcript = (
            "Speaker A [00:01:00]: If we agree on terms, we propose adopting PostgreSQL for unified metadata.\n"
            "Speaker B [00:01:30]: Pending SOW approval, the team is exploring Kafka for event streaming.\n"
            "Speaker A [00:02:00]: We also have a general observation about cloud architecture latency."
        )

        raw_projection = {
            "schema_version": "5.7",
            "meeting_id": "meeting-e2e-wave2-001",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "evidence_index": {
                "stmt-01": {
                    "text": "If we agree on terms, we propose adopting PostgreSQL for unified metadata.",
                    "exact_text": "If we agree on terms, we propose adopting PostgreSQL for unified metadata.",
                    "kind": "proposal",
                    "speaker": "Speaker A",
                },
                "stmt-02": {
                    "text": "Pending SOW approval, the team is exploring Kafka for event streaming.",
                    "exact_text": "Pending SOW approval, the team is exploring Kafka for event streaming.",
                    "kind": "technical",
                    "speaker": "Speaker B",
                },
                "stmt-03": {
                    "text": "We also have a general observation about cloud architecture latency.",
                    "exact_text": "We also have a general observation about cloud architecture latency.",
                    "kind": "summary",
                    "speaker": "Speaker A",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:postgres",
                        "type": "system",
                        "name": "PostgreSQL",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["stmt-01"],
                    },
                    {
                        "id": "system:kafka",
                        "type": "system",
                        "name": "Kafka",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["stmt-02"],
                    },
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
            "evidence_only_statement_ids": ["stmt-01", "stmt-02", "stmt-03", "stmt-extra-04", "stmt-extra-05"],
        }

        extractor = SignalExtractor()
        promoted_proj, receipt = extractor.extract_and_gate(
            raw_projection,
            forensic_transcript=forensic_transcript,
        )

        mutations = promoted_proj["graph_mutations"]
        proposal_updates = mutations.get("proposal_updates", [])
        self.assertGreaterEqual(
            len(proposal_updates),
            2,
            "Conditional dialogue statements must be promoted into proposal_updates",
        )

        pids = {p["proposal_id"] for p in proposal_updates}
        self.assertIn("proposal:01", pids)
        self.assertIn("proposal:02", pids)

        for prop in proposal_updates:
            self.assertEqual(prop["status"], "PROPOSED")
            self.assertTrue(len(prop["evidence_ids"]) >= 1)

        # C-17 evidence ceiling verification: promoted_count > 0 prevents epistemic starvation
        promoted_count = receipt["promoted_signal_count"]
        self.assertGreater(promoted_count, 0)
        expected_ceiling = max(3, 2 * promoted_count)
        self.assertEqual(receipt["evidence_ceiling_enforced"], expected_ceiling)
        self.assertGreaterEqual(len(promoted_proj["evidence_only_statement_ids"]), 3)

    def test_stage03_post_process_pipeline_e2e(self) -> None:
        """Verify post_process_stage03_projection sanitizes, enforces Gate 3.5, and outputs valid V5.7 schema."""
        forensic_transcript = (
            "Speaker A [00:01:00]: If we agree on the scope, we propose adopting PostgreSQL for storage.\n"
            "Speaker B [00:02:00]: We have an open risk regarding network throughput during failover."
        )

        raw_payload = {
            "schema_version": "5.7",
            "meeting_id": "meeting-e2e-wave2-002",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "evidence_index": {
                "stmt-101": {
                    "text": "If we agree on the scope, we propose adopting PostgreSQL for storage.",
                    "exact_text": "If we agree on the scope, we propose adopting PostgreSQL for storage.",
                    "kind": "proposal",
                    "speaker": "Speaker A",
                },
                "stmt-102": {
                    "text": "We have an open risk regarding network throughput during failover.",
                    "exact_text": "We have an open risk regarding network throughput during failover.",
                    "kind": "risk",
                    "speaker": "Speaker B",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:postgres",
                        "type": "system",
                        "name": "PostgreSQL",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["stmt-101"],
                    }
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
            "evidence_only_statement_ids": ["stmt-101", "stmt-102"],
        }

        output_json_str = post_process_stage03_projection(
            json.dumps(raw_payload),
            metadata={"meeting_id": "meeting-e2e-wave2-002", "date": "09/27/2026"},
            forensic_transcript=forensic_transcript,
        )

        final_data = json.loads(output_json_str)
        self.assertEqual(final_data["schema_version"], "5.7")
        self.assertEqual(final_data["graph_mutations"]["action_updates"], [])
        self.assertGreaterEqual(len(final_data["graph_mutations"]["proposal_updates"]), 1)
        self.assertGreaterEqual(len(final_data["graph_mutations"]["risk_updates"]), 1)

    def test_evidence_envelope_intake_and_normalization_e2e(self) -> None:
        """Verify full package sealing, validation, and candidate bundle normalization."""
        raw_package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-e2e-wave2-003",
            "meeting": {
                "meeting_id": "meeting-e2e-wave2-003",
                "title": "Wave 2 Architecture Review",
                "started_at": "2026-09-27T10:00:00Z",
                "organizer": "organizer@consulting.example.com",
                "attendees": ["Alice Lead", "Bob Arch"],
                "platform_reference": None,
                "google_meet_reference": None,
                "teams_reference": None,
                "source_location": "governed-storage/wave2/review",
                "transcript_reference": None,
                "recording_reference": None,
                "source_hash": "a" * 64,
                "template_version": "1.0.0",
                "classification": "internal",
                "audiences": ["nee-engineering"],
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-alice",
                    "reported_name": "Alice Lead",
                    "email": "alice@consulting.example.com",
                    "role_or_org": "Architecture",
                    "statements": [
                        {
                            "statement_id": "stmt-001",
                            "kind": "technical",
                            "text": "Postgres schema migration is verified.",
                            "locator": {"kind": "section", "value": "Transcript / 00:01"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [
                {
                    "action_id": "action-001",
                    "reported_owner": "Alice Lead",
                    "action": "Complete database migration rollout.",
                    "due_date": "2026-10-15",
                    "status": "open",
                    "dependency": None,
                    "requested_follow_up": "Verify indexing",
                    "locator": {"kind": "section", "value": "Actions / 00:05"},
                }
            ],
            "adapter": {
                "name": "nee-evidence-envelope-intake",
                "version": "1.0.0",
            },
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [
                        {
                            "id": "system:postgres",
                            "type": "system",
                            "name": "Postgres",
                            "mutation": "CONFIRM",
                        }
                    ],
                    "proposal_updates": [
                        {
                            "proposal_id": "proposal:migration",
                            "status": "PROPOSED",
                            "description": "Postgres schema migration plan",
                            "evidence_ids": ["stmt-001"],
                        }
                    ],
                }
            },
        }

        sealed = seal_artifact(raw_package)
        validate_artifact(sealed)

        bundle = normalize_evidence_envelope(sealed, run_context="wave2-e2e")
        self.assertIn("contract_id", bundle)
        self.assertTrue(bundle["contract_id"].endswith(":normalized-candidate-bundle:1.0.0"))
        self.assertGreater(len(bundle["candidates"]), 0)
        self.assertGreater(len(bundle["evidence"]), 0)


if __name__ == "__main__":
    unittest.main()
