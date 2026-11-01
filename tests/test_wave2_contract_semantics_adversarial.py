#!/usr/bin/env python3
"""Adversarial Test Suite for Wave 2 Output 03 Extraction & Contract Validation.

Covers:
1. Invariant bypass attempts: Duplicate ID injection into evidence-envelope-intake:1.0.0
   (speaker_ref, statement_id, action_id) ensuring fail-closed SEMANTIC_DUPLICATE_ID.
2. Tactical action containment: Gate 3.5 defaulting enforce_empty_actions to True,
   rejecting tactical action_updates without requiring explicit opt-in flags.
3. Extension mutation validation: com.partner_advisory.nee.canonical-signal-projection rejecting
   malformed entity prefixes, non-canonical decision/proposal/risk IDs, and invalid endpoint prefixes.
"""
from __future__ import annotations

import copy
import unittest

import pytest

import tools.validate_dual_pipeline_contracts as contracts
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3


@pytest.mark.governance
class Wave2ContractSemanticsAdversarialTests(unittest.TestCase):
    """Adversarial and boundary test cases for Wave 2 semantic invariants."""

    def _create_base_envelope(self) -> dict:
        return {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-adv-001",
            "canonical_digest": {
                "algorithm": "sha256",
                "scope": "artifact_without_canonical_digest",
                "value": "0" * 64,
            },
            "meeting": {
                "meeting_id": "meeting-adv-001",
                "title": "Adversarial Test Meeting",
                "started_at": "2026-09-27T10:00:00Z",
                "organizer": "tester@consulting.example.com",
                "attendees": ["Alice Lead", "Bob Engineer"],
                "platform_reference": None,
                "google_meet_reference": None,
                "teams_reference": None,
                "source_location": "governed-storage/adv",
                "transcript_reference": None,
                "recording_reference": None,
                "source_hash": "b" * 64,
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
                            "text": "Validated system architecture.",
                            "locator": {"kind": "section", "value": "Transcript / item 1"},
                            "gemini_confidence": 0.95,
                        },
                        {
                            "statement_id": "stmt-002",
                            "kind": "risk",
                            "text": "Open security consideration.",
                            "locator": {"kind": "section", "value": "Transcript / item 2"},
                            "gemini_confidence": 0.88,
                        },
                    ],
                },
                {
                    "speaker_ref": "speaker-bob",
                    "reported_name": "Bob Engineer",
                    "email": "bob@consulting.example.com",
                    "role_or_org": "Engineering",
                    "statements": [
                        {
                            "statement_id": "stmt-003",
                            "kind": "summary",
                            "text": "Reviewed performance metrics.",
                            "locator": {"kind": "section", "value": "Transcript / item 3"},
                            "gemini_confidence": 0.90,
                        }
                    ],
                },
            ],
            "actions": [
                {
                    "action_id": "action-001",
                    "reported_owner": "Alice Lead",
                    "action": "Document deployment runbook.",
                    "due_date": "2026-10-15",
                    "status": "open",
                    "dependency": None,
                    "requested_follow_up": "Share with ops",
                    "locator": {"kind": "section", "value": "Actions / item 1"},
                }
            ],
            "adapter": {
                "name": "nee-evidence-envelope-intake",
                "version": "1.0.0",
            },
            "extensions": {},
        }

    def test_evidence_envelope_validates_successfully(self) -> None:
        """Baseline envelope passes semantic validation."""
        artifact = self._create_base_envelope()
        contracts.validate_semantics(artifact)

    def test_evidence_envelope_rejects_duplicate_speaker_ref(self) -> None:
        """Duplicate speaker_ref in evidence-envelope-intake must trigger SEMANTIC_DUPLICATE_ID."""
        artifact = self._create_base_envelope()
        duplicate_speaker = copy.deepcopy(artifact["speakers"][0])
        artifact["speakers"].append(duplicate_speaker)

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_DUPLICATE_ID")
        self.assertIn("$.speakers", ctx.exception.issue.path)

    def test_evidence_envelope_rejects_duplicate_statement_id(self) -> None:
        """Duplicate statement_id across speakers must trigger SEMANTIC_DUPLICATE_ID."""
        artifact = self._create_base_envelope()
        dup_stmt = copy.deepcopy(artifact["speakers"][0]["statements"][0])
        artifact["speakers"][1]["statements"].append(dup_stmt)

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_DUPLICATE_ID")
        self.assertIn("statements", ctx.exception.issue.path)

    def test_evidence_envelope_rejects_duplicate_action_id(self) -> None:
        """Duplicate action_id in evidence-envelope-intake must trigger SEMANTIC_DUPLICATE_ID."""
        artifact = self._create_base_envelope()
        dup_action = copy.deepcopy(artifact["actions"][0])
        artifact["actions"].append(dup_action)

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_DUPLICATE_ID")
        self.assertIn("$.actions", ctx.exception.issue.path)

    def test_gate35_fails_closed_by_default_on_non_empty_action_updates(self) -> None:
        """Gate 3.5 must fail closed by default when tactical action_updates appear in mutations."""
        payload = {
            "evidence_index": {
                "stmt-01": {
                    "text": "Alice will migrate the database next week.",
                    "kind": "summary",
                }
            },
            "graph_mutations": {
                "action_updates": [
                    {
                        "action_id": "action-001",
                        "owner_id": "person:alice",
                        "status": "open",
                        "due_date": "10/15/2026",
                        "evidence_ids": ["stmt-01"],
                    }
                ],
                "entity_upserts": [
                    {
                        "id": "person:alice",
                        "type": "person",
                        "name": "Alice Lead",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["stmt-01"],
                    }
                ],
                "relationship_signals": [],
            },
        }

        # metadata=None: should default enforce_empty_actions to True
        res_none = run_qa_gate_3(
            payload,
            f1_text="Alice will migrate the database next week.",
            metadata=None,
        )
        self.assertFalse(res_none["passed"], "Gate 3 must fail when tactical action_updates exist")
        self.assertTrue(any("Gate 3.5: Non-empty action_updates detected" in f for f in res_none["failures"]))

        # metadata={}: should also default enforce_empty_actions to True
        res_empty = run_qa_gate_3(
            payload,
            f1_text="Alice will migrate the database next week.",
            metadata={},
        )
        self.assertFalse(res_empty["passed"])
        self.assertTrue(any("Gate 3.5: Non-empty action_updates detected" in f for f in res_empty["failures"]))

    def test_extension_canonical_signal_projection_rejects_invalid_entity_prefix(self) -> None:
        """Invalid entity prefix in canonical-signal-projection must fail SEMANTIC_INVALID_ENTITY_PREFIX."""
        artifact = self._create_base_envelope()
        artifact["extensions"]["com.partner_advisory.nee.canonical-signal-projection"] = {
            "entity_upserts": [
                {
                    "id": "invalid_prefix:unknown_node",
                    "type": "system",
                    "name": "Unknown",
                }
            ]
        }

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_INVALID_ENTITY_PREFIX")

    def test_extension_canonical_signal_projection_rejects_invalid_proposal_id(self) -> None:
        """Non-canonical proposal_id in canonical-signal-projection must fail SEMANTIC_INVALID_PROPOSAL_ID."""
        artifact = self._create_base_envelope()
        artifact["extensions"]["com.partner_advisory.nee.canonical-signal-projection"] = {
            "proposal_updates": [
                {
                    "proposal_id": "task:migration-plan",
                    "status": "PROPOSED",
                }
            ]
        }

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_INVALID_PROPOSAL_ID")

    def test_extension_canonical_signal_projection_rejects_invalid_decision_id(self) -> None:
        """Non-canonical decision_id in canonical-signal-projection must fail SEMANTIC_INVALID_DECISION_ID."""
        artifact = self._create_base_envelope()
        artifact["extensions"]["com.partner_advisory.nee.canonical-signal-projection"] = {
            "decision_updates": [
                {
                    "decision_id": "dec-12345",
                    "status": "RESOLVED",
                }
            ]
        }

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_INVALID_DECISION_ID")

    def test_extension_canonical_signal_projection_rejects_invalid_risk_id(self) -> None:
        """Non-canonical risk_id in canonical-signal-projection must fail SEMANTIC_INVALID_RISK_ID."""
        artifact = self._create_base_envelope()
        artifact["extensions"]["com.partner_advisory.nee.canonical-signal-projection"] = {
            "risk_updates": [
                {
                    "risk_id": "rsk-001",
                    "status": "OPEN",
                }
            ]
        }

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_INVALID_RISK_ID")


if __name__ == "__main__":
    unittest.main()
