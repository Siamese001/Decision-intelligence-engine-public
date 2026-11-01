#!/usr/bin/env python3
"""Strategic-reasoning semantics, Sherpa golden slice, and leakage tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.core import seal_artifact, sha256_bytes
from dual_pipeline.index import retrieval_metadata_for_record
from dual_pipeline.normalization import normalize_markdown_source
from dual_pipeline.query import reason_over_packet, validate_answer
from dual_pipeline.strategic_ingestion import STRATEGIC_CANDIDATE_PROFILES_KEY
from dual_pipeline.strategic_reasoning import (
    build_strategic_channels,
    create_strategic_evidence_artifact,
    create_strategic_reasoning_output,
    validate_strategic_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def _strategic_packet() -> dict:
    packet = json.loads(
        (ROOT / "tests" / "fixtures" / "sherpa_strategic_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    packet["retrieval_mode"] = "strategic"
    packet["allowed_lanes"] = ["approved", "candidate", "external_public"]
    packet["strategic_context"] = build_strategic_channels(packet)
    from dual_pipeline.core import digest

    packet["packet_digest"] = digest(packet)
    return packet


class StrategicReasoningTests(unittest.TestCase):
    def test_sherpa_business_case_normalizes_to_three_atomic_profiles(self) -> None:
        path = ROOT / "tests" / "fixtures" / "sherpa_official_business_case_intake.json"
        package = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            sha256_bytes(package["source"]["raw_markdown"].encode("utf-8")),
            package["source"]["source_file_sha256"],
        )
        bundle = normalize_markdown_source(
            package,
            run_context="strategic-reasoning-test",
        )
        profiles = bundle["extensions"][STRATEGIC_CANDIDATE_PROFILES_KEY]
        self.assertEqual(3, len(bundle["candidates"]))
        self.assertTrue(
            all(candidate["candidate_type"] == "assertion" for candidate in bundle["candidates"])
        )
        self.assertEqual(
            {"business_problem", "official_intent", "target_outcome"},
            {profile["knowledge_role"] for profile in profiles.values()},
        )
        target = next(
            profile
            for profile in profiles.values()
            if profile["knowledge_role"] == "target_outcome"
        )
        self.assertEqual("intended", target["realization_state"])

    def test_intended_outcome_cannot_be_reclassified_as_observed(self) -> None:
        with self.assertRaisesRegex(ValueError, "official-intent scope"):
            validate_strategic_profile(
                {
                    "knowledge_role": "target_outcome",
                    "modality": "intended",
                    "authority_scope": "official_project_intent",
                    "applicability_scope": "nee_specific",
                    "realization_state": "observed",
                }
            )

    def test_authority_scope_changes_use_not_graph_authority(self) -> None:
        official_intent = {
            "knowledge_role": "official_intent",
            "modality": "intended",
            "authority_scope": "official_project_intent",
            "applicability_scope": "nee_specific",
            "realization_state": "intended",
        }
        metadata = retrieval_metadata_for_record(
            "assertion",
            "approved",
            strategic_profile=official_intent,
        )
        self.assertEqual("approved", metadata["epistemic_status"])
        self.assertEqual("strategic_context", metadata["answer_use"])

    def test_explicit_candidate_source_chunk_is_strategically_retrievable_but_nonfactual(
        self,
    ) -> None:
        profile = {
            "knowledge_role": "constraint",
            "modality": "normative",
            "authority_scope": "source_position",
            "applicability_scope": "nee_specific",
            "realization_state": "not_applicable",
        }
        candidate = retrieval_metadata_for_record(
            "source_chunk",
            "candidate",
            strategic_profile=profile,
        )
        approved = retrieval_metadata_for_record(
            "source_chunk",
            "approved",
            strategic_profile=profile,
        )
        self.assertEqual("candidate", candidate["epistemic_status"])
        self.assertEqual("candidate_hypothesis", candidate["answer_use"])
        self.assertEqual("citation_only", approved["answer_use"])

    def test_role_separated_portfolio_keeps_target_outcome_out_of_observed_outcomes(self) -> None:
        packet = _strategic_packet()
        portfolio = packet["strategic_context"]
        self.assertEqual(3, portfolio["channel_counts"]["objectives"])
        self.assertEqual(0, portfolio["channel_counts"]["outcomes"])
        self.assertEqual(1, portfolio["channel_counts"]["principles"])
        self.assertEqual(1, portfolio["channel_counts"]["external_patterns"])
        artifact = create_strategic_evidence_artifact(
            query_run_id="query-run-strategic-test",
            graph_snapshot=packet["graph_snapshot"],
            source_packet_digest=packet["packet_digest"],
            portfolio=portfolio,
        )
        self.assertEqual(0, artifact["automatic_graph_mutation_count"])

    def test_strategic_recommendation_validates_but_intent_as_current_state_fails(self) -> None:
        packet = _strategic_packet()
        answer = reason_over_packet({"query_type": "opportunity_nba"}, packet)
        self.assertEqual("PASS", validate_answer(answer, packet)["status"])
        self.assertTrue(answer["next_best_actions"])
        self.assertTrue(answer["hypotheses"][0]["hypothesis"].startswith("CANDIDATE:"))

        target = next(
            item
            for item in packet["approved"]
            if item["record"]["strategic_profile"]["knowledge_role"] == "target_outcome"
        )
        invalid = dict(answer)
        invalid["current_state"] = [
            {
                "statement": target["record"]["retrieval_text"],
                "citations": [target["record"]["record_id"]],
                "evidence_ids": target["record"]["evidence_ids"],
                "lane": "approved",
            }
        ]
        result = validate_answer(invalid, packet)
        self.assertEqual("FAIL", result["status"])
        self.assertIn("current_state[0]", result["answer_use_failures"])

        output = create_strategic_reasoning_output(
            query_run_id="query-run-strategic-test",
            packet=packet,
            answer=answer,
        )
        self.assertEqual(0, output["automatic_graph_mutation_count"])
        self.assertIn("target_outcome", {
            role
            for option in output["options"]
            for role in option["basis_roles"]
        } | {
            role
            for action in output["next_best_actions"]
            for role in action["basis_roles"]
        })

    def test_strategic_profile_schema_rejects_intent_realization_leakage(self) -> None:
        fixture = json.loads(
            (
                ROOT
                / "contracts"
                / "dual_pipeline"
                / "fixtures"
                / "valid"
                / "strategic-assertion-profile.json"
            ).read_text(encoding="utf-8")
        )
        fixture["realization_state"] = "observed"
        invalid = seal_artifact(fixture)
        from dual_pipeline.contracts import validate_artifact

        with self.assertRaises(ValueError):
            validate_artifact(invalid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
