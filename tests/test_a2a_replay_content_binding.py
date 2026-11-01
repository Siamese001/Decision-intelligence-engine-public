from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import exact_file_copy
from dual_pipeline.release import (
    ReleaseManager,
    _a2a_intake_authorization_attests_source,
    _replayed_a2a_payload_governance,
)

ROOT = Path(__file__).resolve().parents[1]


class A2AReplayContentBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-a2a-replay-content-")
        self.root = Path(self.temp.name)
        self.graph_path = self.root / "seed.sqlite"
        exact_file_copy(ROOT / "graph" / "nee_graph.sqlite", self.graph_path)
        self.runtime = self.root / "runtime"
        self.manager = ReleaseManager(self.runtime, self.graph_path)
        self.binding = {
            "receipt_id": "a2a-finalization-receipt-fixture",
            "receipt_canonical_digest": "a" * 64,
            "receipt_file_sha256": "b" * 64,
            "package_file_sha256": "c" * 64,
            "package_canonical_digest": "d" * 64,
            "archive_root_id": "a2a-archive-fixture",
            "archive_binding_digest": "e" * 64,
            "verification_state": "finalization_archive_replayed",
        }
        self.reference = {
            "run_id": "run-a2a-replay-fixture",
            "package_relative_path": (
                "runs/run-a2a-replay-fixture/a2a_finalization/finalized_packet.json"
            ),
            "receipt_relative_path": (
                "runs/run-a2a-replay-fixture/a2a_finalization/finalization_receipt.json"
            ),
            "package_file_sha256": self.binding["package_file_sha256"],
            "receipt_file_sha256": self.binding["receipt_file_sha256"],
            "archive_root_id": self.binding["archive_root_id"],
        }
        self.replayed_bundle = self._bundle()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _bundle(self) -> dict:
        evidence_id = "evidence-a2a-replay-fixture"
        candidate_id = "candidate-a2a-replay-fixture"
        span = {
            "selection_ledger_sha256": "f" * 64,
            "span_ledger_sha256": "0" * 64,
            "reviewer": "fixture-reviewer",
            "decided_at": "2026-07-25T00:00:00Z",
            "human_attestation": "I confirm the source span exactly supports this narrow claim.",
            "reviewer_authorization_reference": None,
            "fact_id": "FACT-REPLAY-001",
            "decision": "CONFIRM",
            "span_proposal_id": "span-replay-fixture",
            "evidence_id": evidence_id,
            "source_id": "source-a2a-replay-fixture",
            "rationale": "Fixture selected the exact source span.",
            "quote": "The replayed packet supports this fixture assertion.",
            "quote_sha256": "1" * 64,
            "visible_start_offset": 0,
            "visible_end_offset": 52,
            "raw_response_sha256": "2" * 64,
            "visible_text_sha256": "3" * 64,
            "canonical_locator": {
                "kind": "uri_fragment",
                "value": "https://openai.com/fixture#span",
                "quote_digest": "1" * 64,
            },
        }
        candidate = {
            "candidate_id": candidate_id,
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "normalized_text": "The replayed packet supports this fixture assertion.",
            "materiality": "medium",
            "entity_mentions": [],
            "evidence_ids": [evidence_id],
            "source_locator": deepcopy(span["canonical_locator"]),
            "classification": "public",
            "audiences": ["internal_consulting"],
            "extraction_confidence": 0.9,
        }
        governance = {
            "source_item_id": "FACT-REPLAY-001",
            "retrieval_kind": "assertion",
            "answer_use": "candidate_hypothesis",
            "fact_promotion_eligible": True,
            "review_questions": ["Does the selected quote support the claim?"],
            "promotion_status": "candidate_evidence_selected_span_hitl_required",
            "evidence_verification_states": {evidence_id: "fresh_capture_selected_span"},
            "source_selection_bindings": [span],
            "finalization_binding": deepcopy(self.binding),
            "force_hitl": True,
        }
        return {
            "source_id": span["source_id"],
            "source_version_id": "source-version-a2a-replay-fixture",
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "source_id": span["source_id"],
                    "source_version_id": "source-version-a2a-replay-fixture",
                    "locator": deepcopy(span["canonical_locator"]),
                    "relationship": "supports",
                    "directness": "direct",
                    "authority_rank": 8,
                    "content_digest": span["quote_sha256"],
                }
            ],
            "candidates": [candidate],
            "extensions": {
                "com.partner_advisory.nee.context": {
                    "source_kind": "a2a_fact_vector_packet",
                    "source_posture": "external_first_party",
                    "authority_rank": 8,
                },
                "com.partner_advisory.nee.source": {"name": "A2A replay fixture", "date": "2026-07-25"},
                "com.partner_advisory.nee.a2a-packet-candidate-governance": {
                    candidate_id: governance
                },
            },
        }

    def _summary(self) -> dict:
        return {
            "source_kind": "a2a_fact_vector_packet",
            "a2a_finalization_binding": self.binding,
            "a2a_finalization_reference": self.reference,
            "package_file_sha256": self.binding["package_file_sha256"],
        }

    def _proposed_change_set(self, *, candidate: dict | None = None) -> dict:
        expected_candidate = candidate or self.replayed_bundle["candidates"][0]
        candidate_id = expected_candidate["candidate_id"]
        governance = self.replayed_bundle["extensions"][
            "com.partner_advisory.nee.a2a-packet-candidate-governance"
        ][candidate_id]
        source = {
            **self.replayed_bundle["extensions"]["com.partner_advisory.nee.source"],
            "source_id": self.replayed_bundle["source_id"],
            "source_version_id": self.replayed_bundle["source_version_id"],
            "source_posture": "external_first_party",
            "authority_rank": 8,
            "evidence": self.replayed_bundle["evidence"],
        }
        entity_operation = "operation-a2a-replay-entity"
        assertion_operation = "operation-a2a-replay-assertion"
        return {
            "operations": [
                {
                    "operation_id": entity_operation,
                    "operation": "create_entity",
                    "candidate_id": candidate_id,
                },
                {
                    "operation_id": assertion_operation,
                    "operation": "add_candidate_assertion",
                    "candidate_id": candidate_id,
                },
            ],
            "extensions": {
                "com.partner_advisory.nee.source": source,
                "com.partner_advisory.nee.operation-payloads": {
                    entity_operation: {
                        "candidate": expected_candidate,
                        "disposition": "CANDIDATE",
                        "entity": {"external_concept_id": "external-replay-fixture"},
                    },
                    assertion_operation: {
                        "candidate": expected_candidate,
                        "disposition": "CANDIDATE",
                        "retrieval_governance": _replayed_a2a_payload_governance(
                            governance,
                            self.reference,
                        ),
                    },
                },
            },
        }

    def _reverify(self, bundle: dict, proposed: dict | None) -> None:
        with patch(
            "dual_pipeline.release.reverify_ingress_finalization_normalized_bundle",
            return_value=(object(), self.replayed_bundle),
        ), patch(
            "dual_pipeline.release.verified_finalization_binding",
            return_value=self.binding,
        ):
            self.manager._reverify_a2a_component_finalization(
                bundle=bundle,
                summary=self._summary(),
                proposed=proposed,
            )

    def test_release_rejects_stored_candidate_content_that_differs_from_replay(self) -> None:
        stored = deepcopy(self.replayed_bundle)
        stored["candidates"][0]["normalized_text"] = "Forged graph candidate text."
        with self.assertRaisesRegex(RuntimeError, "normalized source content differs"):
            self._reverify(stored, None)

    def test_release_rejects_proposed_candidate_content_that_differs_from_replay(self) -> None:
        forged_candidate = deepcopy(self.replayed_bundle["candidates"][0])
        forged_candidate["normalized_text"] = "Forged graph candidate text."
        with self.assertRaisesRegex(RuntimeError, "candidate content differs"):
            self._reverify(self.replayed_bundle, self._proposed_change_set(candidate=forged_candidate))

    def test_release_allows_replay_bound_assertion_plus_external_concept_operations(self) -> None:
        self._reverify(self.replayed_bundle, self._proposed_change_set())

    def test_production_a2a_intake_audit_requires_source_attest_and_both_resources(self) -> None:
        audit = {
            "capabilities": ["knowledge:intake", "knowledge:source-attest"],
            "authorized_resource_names": [
                "a2a_finalization_archive",
                "a2a_finalization_receipt",
                "a2a_fact_vector_packet_package",
            ],
        }
        self.assertTrue(_a2a_intake_authorization_attests_source(audit))

        for field, value in (
            ("capabilities", ["knowledge:intake"]),
            ("authorized_resource_names", ["a2a_finalization_receipt"]),
            ("authorized_resource_names", ["a2a_finalization_archive"]),
            ("authorized_resource_names", "a2a_finalization_archive"),
        ):
            with self.subTest(field=field, value=value):
                weakened = deepcopy(audit)
                weakened[field] = value
                self.assertFalse(_a2a_intake_authorization_attests_source(weakened))


if __name__ == "__main__":
    unittest.main()
