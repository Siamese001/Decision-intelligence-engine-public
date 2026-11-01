from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from dual_pipeline.core import exact_file_copy, seal_artifact, sha256_bytes
from dual_pipeline.intake import (
    A2A_GUIDANCE_ANCHOR_KEY,
    IntakeEngine,
    build_dispositions,
    build_graph_change_set,
)
from dual_pipeline.normalization import normalize_a2a_fact_vector_packet
from dual_pipeline.release import GovernedGraphWriter
from dual_pipeline.source_spans import HUMAN_ATTESTATION, SELECTION_SCHEMA_VERSION
from dual_pipeline.source_trust import assert_pinned_vendor_host

ROOT = Path(__file__).resolve().parents[1]


class A2ASelectedSpanGovernanceTests(unittest.TestCase):
    def _package(self) -> dict:
        source_id = "source-openai-selected-fixture"
        source_version_id = "source-version-openai-selected-fixture"
        evidence_id = "evidence-openai-selected-fixture"
        final_url = "https://openai.github.io/openai-agents-python/selected/"
        quote = "The selected frozen source span supports the candidate claim."
        quote_sha256 = sha256_bytes(quote.encode("utf-8"))
        visible_start = 17
        visible_end = visible_start + len(quote)
        locator = {
            "kind": "uri_fragment",
            "value": f"{final_url}#selected:V{visible_start}-{visible_end}",
            "quote_digest": quote_sha256,
        }
        capture = {
            "capture_id": "capture-openai-selected-fixture",
            "capture_manifest_sha256": "a" * 64,
            "captured_at": "2026-07-25T00:00:00Z",
            "requested_url": final_url,
            "final_url": final_url,
            "raw_response_sha256": "b" * 64,
            "visible_text_sha256": "c" * 64,
            "evidence_outcomes": {evidence_id: "SELECTED_CURRENT_SPAN"},
            "source_span_ledger_sha256": "d" * 64,
            "selection_ledger_sha256": "e" * 64,
        }
        span = {
            "span_proposal_id": "a2a-span-selected-fixture",
            "prior_evidence_id": "evidence-prior-fixture",
            "fact_id": "FACT-SELECTED-001",
            "source_id": source_id,
            "source_version_id": source_version_id,
            "capture_id": capture["capture_id"],
            "raw_response_sha256": capture["raw_response_sha256"],
            "visible_text_sha256": capture["visible_text_sha256"],
            "requested_url": final_url,
            "final_url": final_url,
            "captured_at": capture["captured_at"],
            "heading_path": [{"level": 2, "text": "Selected"}],
            "anchor": "selected",
            "visible_start_offset": visible_start,
            "visible_end_offset": visible_end,
            "quote": quote,
            "quote_sha256": quote_sha256,
            "context_before_sha256": "f" * 64,
            "context_after_sha256": "0" * 64,
            "section_visible_text_sha256": "1" * 64,
            "extractor": {"method": "deterministic_lexical_v1"},
            "status": "SELECTED_FOR_HITL",
        }
        selection = {
            "schema_version": SELECTION_SCHEMA_VERSION,
            "span_ledger_sha256": capture["source_span_ledger_sha256"],
            "selection_ledger_sha256": capture["selection_ledger_sha256"],
            "reviewer": "fixture-reviewer",
            "decided_at": "2026-07-25T00:00:01Z",
            "human_attestation": HUMAN_ATTESTATION,
            "reviewer_authorization_reference": None,
            "decisions": [
                {
                    "fact_id": span["fact_id"],
                    "decision": "CONFIRM",
                    "span_proposal_id": span["span_proposal_id"],
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "rewritten_claim": None,
                    "rationale": "The exact frozen quote directly supports the narrow claim.",
                    "quote_sha256": quote_sha256,
                    "visible_start_offset": visible_start,
                    "visible_end_offset": visible_end,
                    "raw_response_sha256": capture["raw_response_sha256"],
                    "visible_text_sha256": capture["visible_text_sha256"],
                    "canonical_locator": locator,
                }
            ],
        }
        assertion_id = "candidate-openai-selected-assertion"
        citation_id = "candidate-openai-selected-citation"
        artifact = {
            "contract_id": ("urn:nee:dual-pipeline:a2a-fact-vector-packet-intake:1.0.0"),
            "schema_version": "1.0.0",
            "artifact_id": "a2a-selected-span-fixture",
            "packet": {
                "packet_id": "nee-a2a-selected-span-fixture",
                "source_zip_sha256": "2" * 64,
                "packet_manifest_sha256": "3" * 64,
                "signature_status": "verified_integrity_only",
                "trust_scope": (
                    "packet_integrity_only; organizational identity trust must be added by UWG"
                ),
            },
            "source": {
                "source_id": source_id,
                "source_version_id": source_version_id,
                "title": "OpenAI selected-span fixture",
                "source_location": final_url,
                "source_date": "2026-07-25",
                "classification": "public",
                "audiences": ["internal_consulting"],
                "source_posture": "external_first_party",
                "authority_rank": 8,
                "vendor": "OpenAI",
                "url": final_url,
                "first_party": True,
            },
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "source_version_id": source_version_id,
                    "locator": locator,
                    "relationship": "supports",
                    "directness": "direct",
                    "authority_rank": 8,
                    "content_digest": quote_sha256,
                    "verification_state": "fresh_capture_selected_span",
                }
            ],
            "candidates": [
                {
                    "candidate_id": assertion_id,
                    "source_item_id": "FACT-SELECTED-001",
                    "candidate_type": "assertion",
                    "assertion_type": "candidate_assertion",
                    "normalized_text": "The source supports a selected candidate claim.",
                    "materiality": "medium",
                    "entity_mentions": [
                        {
                            "mention_id": "mention-openai-selected-assertion",
                            "reported_text": "OpenAI selected span",
                            "stable_identifier": None,
                            "context": "Selected first-party source concept.",
                        }
                    ],
                    "evidence_ids": [evidence_id],
                    "source_locator": locator,
                    "classification": "public",
                    "audiences": ["internal_consulting"],
                    "extraction_confidence": 0.7,
                    "retrieval_kind": "assertion",
                    "answer_use": "candidate_hypothesis",
                    "review_question": "Does the exact quote support this candidate?",
                    "promotion_status": ("candidate_evidence_selected_span_hitl_required"),
                },
                {
                    "candidate_id": citation_id,
                    "source_item_id": evidence_id,
                    "candidate_type": "source_chunk",
                    "assertion_type": "candidate_assertion",
                    "normalized_text": quote,
                    "materiality": "medium",
                    "entity_mentions": [
                        {
                            "mention_id": "mention-openai-selected-citation",
                            "reported_text": "OpenAI selected-span fixture",
                            "stable_identifier": source_id,
                            "context": "Citation-only selected source evidence.",
                        }
                    ],
                    "evidence_ids": [evidence_id],
                    "source_locator": locator,
                    "classification": "public",
                    "audiences": ["internal_consulting"],
                    "extraction_confidence": 0.6,
                    "retrieval_kind": "evidence_span",
                    "answer_use": "citation_only",
                    "review_question": "Is this exact quote correctly recorded?",
                    "promotion_status": "citation_only",
                },
            ],
            "adapter": {"name": "nee-a2a-fact-vector-packet", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.a2a-fresh-capture": capture,
                "com.partner_advisory.nee.a2a-evidence-spans": {evidence_id: span},
                "com.partner_advisory.nee.a2a-source-span-selection": selection,
            },
        }
        return seal_artifact(artifact)

    def _reseed(self, package: dict) -> dict:
        return seal_artifact(package)

    def _finalization_binding(self, package: dict) -> dict:
        return {
            "receipt_id": "a2a-finalization-receipt-fixture",
            "receipt_canonical_digest": "4" * 64,
            "receipt_file_sha256": "5" * 64,
            "package_file_sha256": "6" * 64,
            "package_canonical_digest": package["canonical_digest"]["value"],
            "archive_root_id": "a2a-archive-fixture",
            "archive_binding_digest": "7" * 64,
            "verification_state": "finalization_archive_replayed",
        }

    def test_selected_span_requires_exact_locator_and_attested_quote(self) -> None:
        package = self._package()
        unverified = normalize_a2a_fact_vector_packet(package)
        self.assertFalse(
            unverified["extensions"]["com.partner_advisory.nee.a2a-packet-candidate-governance"]
            ["candidate-openai-selected-assertion"]["fact_promotion_eligible"]
        )
        with self.assertRaisesRegex(ValueError, "proof must be verifier-issued"):
            normalize_a2a_fact_vector_packet(
                package,
                finalization_proof=self._finalization_binding(package),  # type: ignore[arg-type]
            )

        bad_locator = deepcopy(package)
        bad_locator["evidence"][0]["locator"]["value"] = "https://evil.invalid/span"
        with self.assertRaisesRegex(ValueError, "frozen quote"):
            normalize_a2a_fact_vector_packet(self._reseed(bad_locator))

        bad_citation_status = deepcopy(package)
        bad_citation_status["candidates"][1]["promotion_status"] = (
            "candidate_evidence_selected_span_hitl_required"
        )
        with self.assertRaisesRegex(ValueError, "citation_only"):
            normalize_a2a_fact_vector_packet(self._reseed(bad_citation_status))

        bad_citation_text = deepcopy(package)
        bad_citation_text["candidates"][1]["normalized_text"] = "unrelated text"
        with self.assertRaisesRegex(ValueError, "frozen quote"):
            normalize_a2a_fact_vector_packet(self._reseed(bad_citation_text))

    def test_direct_graph_change_set_requires_formal_span_binding(self) -> None:
        normalized = normalize_a2a_fact_vector_packet(self._package())
        candidate = normalized["candidates"][0]
        resolutions = [
            {
                "mention_id": candidate["entity_mentions"][0]["mention_id"],
                "selected_entity_id": "node-fixture-subject",
                "outcome": "AUTO_LINK_EXACT",
            },
            {
                "mention_id": normalized["candidates"][1]["entity_mentions"][0]["mention_id"],
                "selected_entity_id": "node-fixture-citation",
                "outcome": "AUTO_LINK_EXACT",
            },
        ]
        with self.assertRaisesRegex(ValueError, "replayed finalization receipt"):
            build_graph_change_set(
                normalized,
                [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "disposition": "CANDIDATE",
                    }
                ],
                resolutions,
                ROOT / "graph" / "nee_graph.sqlite",
                decisions=[],
            )

    def test_selected_span_readiness_cannot_be_claimed_without_capture_and_selection(self) -> None:
        package = deepcopy(self._package())
        package["extensions"].pop("com.partner_advisory.nee.a2a-fresh-capture")
        package["extensions"].pop("com.partner_advisory.nee.a2a-evidence-spans")
        package["extensions"].pop("com.partner_advisory.nee.a2a-source-span-selection")
        with self.assertRaisesRegex(ValueError, "refetch-required"):
            normalize_a2a_fact_vector_packet(self._reseed(package))

    def test_selected_span_ingest_requires_a_replayed_finalization_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-a2a-selected-ingest-") as directory:
            root = Path(directory)
            package_path = root / "selected.intake.json"
            package_path.write_text(json.dumps(self._package()), encoding="utf-8")
            runtime = root / "runtime"
            engine = IntakeEngine(ROOT / "graph" / "nee_graph.sqlite", runtime)
            with self.assertRaisesRegex(ValueError, r"(requires an A2A finalization receipt|unsupported intake contract)"):
                engine.ingest(package_path)
            self.assertFalse((runtime / "runs").exists())

    def test_direct_graph_change_set_rejects_stripped_a2a_governance(self) -> None:
        normalized = normalize_a2a_fact_vector_packet(self._package())
        candidate = normalized["candidates"][0]
        stripped = deepcopy(normalized)
        stripped["extensions"].pop("com.partner_advisory.nee.a2a-packet-candidate-governance")
        resolutions = [
            {
                "mention_id": item["entity_mentions"][0]["mention_id"],
                "selected_entity_id": f"node-fixture-{index}",
                "outcome": "AUTO_LINK_EXACT",
            }
            for index, item in enumerate(stripped["candidates"])
        ]
        with self.assertRaisesRegex(ValueError, "missing its required source governance"):
            build_graph_change_set(
                stripped,
                [{"candidate_id": candidate["candidate_id"], "disposition": "CANDIDATE"}],
                resolutions,
                ROOT / "graph" / "nee_graph.sqlite",
                decisions=[],
            )

    def test_direct_graph_change_set_rejects_stripped_a2a_route_and_governance(self) -> None:
        """Source provenance, not a mutable route field, keeps the A2A gate active."""

        normalized = normalize_a2a_fact_vector_packet(self._package())
        candidate = normalized["candidates"][0]
        stripped = deepcopy(normalized)
        stripped["extensions"].pop("com.partner_advisory.nee.a2a-packet-candidate-governance")
        stripped["extensions"]["com.partner_advisory.nee.context"].pop("source_kind")
        resolutions = [
            {
                "mention_id": item["entity_mentions"][0]["mention_id"],
                "selected_entity_id": f"node-fixture-{index}",
                "outcome": "AUTO_LINK_EXACT",
            }
            for index, item in enumerate(stripped["candidates"])
        ]
        with self.assertRaisesRegex(ValueError, "missing its required source governance"):
            build_graph_change_set(
                stripped,
                [{"candidate_id": candidate["candidate_id"], "disposition": "CANDIDATE"}],
                resolutions,
                ROOT / "graph" / "nee_graph.sqlite",
                decisions=[],
            )

    def test_derived_a2a_guidance_can_stage_without_fact_finalization_proof(self) -> None:
        """Forced HITL still permits non-fact A2A guidance/pattern retrieval lanes."""

        candidate_id = "candidate-a2a-derived-guidance"
        evidence_id = "evidence-a2a-derived-guidance"
        bundle = {
            "run_id": "run-a2a-derived-guidance",
            "trace_id": "1" * 32,
            "source_id": "SRC-NEE-A2A-GUIDANCE",
            "source_version_id": "source-version-a2a-guidance",
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "source_id": "SRC-NEE-A2A-GUIDANCE",
                    "source_version_id": "source-version-a2a-guidance",
                    "locator": {"kind": "record", "value": "guidance"},
                    "relationship": "illustrates",
                    "directness": "direct",
                    "authority_rank": 8,
                    "content_digest": "e" * 64,
                    "verification_state": "derived_packet_content",
                }
            ],
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "candidate_type": "action",
                    "assertion_type": "candidate_assertion",
                    "normalized_text": "Keep handoff guidance in its labeled guidance lane.",
                    "materiality": "medium",
                    "entity_mentions": [{"mention_id": "mention-a2a-derived-guidance"}],
                    "evidence_ids": [evidence_id],
                    "source_locator": {"kind": "derived", "value": "guidance"},
                    "classification": "public",
                    "audiences": ["internal_consulting"],
                    "extraction_confidence": 0.8,
                }
            ],
            "extensions": {
                "com.partner_advisory.nee.context": {"source_kind": "a2a_fact_vector_packet"},
                "com.partner_advisory.nee.source": {
                    "source_zip_sha256": "a" * 64,
                    "packet_manifest_sha256": "b" * 64,
                },
                "com.partner_advisory.nee.a2a-packet-candidate-governance": {
                    candidate_id: {
                        "source_item_id": "GP-001",
                        "retrieval_kind": "guidance",
                        "answer_use": "guidance",
                        "fact_promotion_eligible": False,
                        "review_questions": ["Keep this non-factual guidance item?"],
                        "promotion_status": "recommendation_only",
                        "force_hitl": True,
                    }
                },
            },
        }
        decision = seal_artifact(
            {
                "contract_id": "urn:nee:dual-pipeline:hitl-decision:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": "hitl-a2a-derived-guidance",
                "decision_id": "decision-a2a-derived-guidance",
                "review_item_id": "review-a2a-derived-guidance",
                "run_id": bundle["run_id"],
                "trace_id": bundle["trace_id"],
                "tier": 2,
                "decision": "APPROVE_CANDIDATE",
                "reviewer": "fixture-reviewer",
                "decided_at": "2026-07-25T00:00:00Z",
                "rationale": "Useful guidance, explicitly retained outside the factual lane.",
                "evidence_reviewed": ["evidence-a2a-derived-guidance"],
                "alternatives": ["HOLD", "REJECT"],
                "before_state_digest": "c" * 64,
                "proposed_after_state_digest": "d" * 64,
                "affected_candidate_ids": [candidate_id],
                "challenger_required": False,
                "challenger_id": None,
                "extensions": {
                    A2A_GUIDANCE_ANCHOR_KEY: "pattern_agent_to_agent_protocol"
                },
            }
        )
        resolutions = [
            {
                "mention_id": "mention-a2a-derived-guidance",
                "selected_entity_id": None,
                "outcome": "HITL_CONFIRM_ENTITY",
                "requires_hitl": True,
            }
        ]
        dispositions = build_dispositions(
            bundle,
            [{"subject_id": candidate_id, "intake_route": "ACTION_ITEM"}],
            resolutions,
            decisions=[decision],
        )
        self.assertEqual("CANDIDATE", dispositions[0]["disposition"])
        self.assertTrue(dispositions[0]["identity_resolved"])
        change_set = build_graph_change_set(
            bundle,
            dispositions,
            resolutions,
            ROOT / "graph" / "nee_graph.sqlite",
            decisions=[decision],
        )
        self.assertIsNotNone(change_set)
        assert change_set is not None
        self.assertEqual("add_action_item", change_set["operations"][0]["operation"])
        payload = change_set["extensions"]["com.partner_advisory.nee.operation-payloads"][
            change_set["operations"][0]["operation_id"]
        ]
        self.assertEqual(
            "pattern_agent_to_agent_protocol", payload["subject_node_id"]
        )
        self.assertFalse(payload["retrieval_governance"]["fact_promotion_eligible"])
        self.assertNotIn("a2a_finalization_binding", payload["retrieval_governance"])

        approved = deepcopy(change_set)
        approved["status"] = "approved"
        approved["approval_id"] = "approval-a2a-derived-guidance"
        approved["extensions"]["com.partner_advisory.nee.source"]["a2a_evidence_spans"] = None
        approved = seal_artifact(approved)
        with tempfile.TemporaryDirectory(prefix="nee-a2a-guidance-writer-") as directory:
            graph_copy = Path(directory) / "graph.sqlite"
            exact_file_copy(ROOT / "graph" / "nee_graph.sqlite", graph_copy)
            receipt = GovernedGraphWriter(graph_copy).apply(approved)
            self.assertEqual(1, receipt["operation_count"])
            connection = sqlite3.connect(graph_copy)
            try:
                location_status = connection.execute(
                    "SELECT location_status FROM evidence_item WHERE evidence_id=?",
                    (evidence_id,),
                ).fetchone()
                self.assertEqual(("unavailable_in_baseline",), location_status)
            finally:
                connection.close()

    def test_vendor_label_requires_a_pinned_official_host(self) -> None:
        assert_pinned_vendor_host("OpenAI", "https://openai.github.io/openai-agents-python/")
        assert_pinned_vendor_host("Anthropic", "https://www.anthropic.com/engineering/")
        with self.assertRaisesRegex(ValueError, "pinned official host"):
            assert_pinned_vendor_host("OpenAI", "https://openai.invalid/fake")


if __name__ == "__main__":
    unittest.main()
