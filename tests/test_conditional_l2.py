#!/usr/bin/env python3
"""Focused acceptance for deterministic conditional external-L2 execution."""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

from dual_pipeline.core import (  # noqa: E402
    digest,
    exact_file_copy,
    read_json,
    sha256_file,
    utc_now,
)
from dual_pipeline.index import (  # noqa: E402
    HybridRetriever,
)
from dual_pipeline.intake import IntakeEngine  # noqa: E402
from dual_pipeline.l2 import (  # noqa: E402
    DECISION_MODEL_OUTPUT_SCHEMA,
    CallPurpose,
    L2Disposition,
    L2ProviderError,
    L2ProviderRequest,
    OpenAIProviderFactory,
    ProviderFailureCode,
    analyze_request_intent,
    build_call_record,
    decide_l2_execution,
    evaluate_semantic_quality,
    execute_conditional_l2,
    load_l2_config,
    validate_decision_model_output,
)
from dual_pipeline.query import QueryEngine, infer_query_type, reason_over_packet  # noqa: E402
from dual_pipeline.query_router import QueryDestination, route_query  # noqa: E402
from dual_pipeline.release import ReleaseManager  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402
from tests.l2_fakes import (  # noqa: E402
    FakeProviderFactory,
    missing_schema_version,
    missing_unambiguous_current_state_citation,
    repair_moves_debug_to_current_state,
    repair_with_invented_claim,
    response_with_unknown_debug_field,
    schema_valid_quality_failure,
    truncated_valid_json,
)

NORMAL_MODEL = "gpt-5.6-luna"
HIGH_MODEL = "gpt-5.6-luna"
MOST_COMPLEX_MODEL = "gpt-5.6-luna"
AUDIENCE = "internal_consulting,nee-engineering"


def _insert_source_bound_candidate(graph_path: Path) -> None:
    """Add the minimum retrieval-cleared candidate for L2 review tests.

    The production seed graph intentionally has no candidate records admitted
    through the W1 clearance policy.  Candidate-review tests therefore use an
    isolated graph copy with an explicit source, extraction run, evidence, and
    review owner rather than weakening that policy.
    """

    now = utc_now()
    baseline_graph_sha256 = sha256_file(graph_path)
    source_id = "source-conditional-l2-candidate"
    source_version_id = "source-version-conditional-l2-candidate"
    extraction_run_id = "extract-conditional-l2-candidate"
    evidence_id = "evidence-conditional-l2-candidate"
    assertion_id = "assertion-conditional-l2-candidate"
    source_hash = digest({"source": source_id, "version": source_version_id})
    connection = sqlite3.connect(graph_path)
    connection.row_factory = sqlite3.Row
    try:
        subject = connection.execute(
            "SELECT node_id FROM node WHERE node_id='workstream_project_beacon' AND active=1"
        ).fetchone()
        if subject is None:
            raise AssertionError("Project Beacon fixture subject is absent")
        connection.execute(
            """INSERT INTO source
               (source_id,source_type,source_name,source_date,authority_rank,source_posture,
                cold_archive_ref,notes,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                source_id,
                "official_web_snapshot",
                "Conditional L2 candidate fixture source",
                "2026-08-11",
                9,
                "external_first_party",
                "fixture://conditional-l2/candidate",
                "Test-only source-bound candidate review fixture.",
                now,
            ),
        )
        connection.execute(
            """INSERT INTO source_version
               (source_version_id,source_id,version_number,source_fingerprint,content_hash,
                source_date_normalized,source_date_precision,source_date_status,authority_rank,
                scope_text,posture_text,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source_version_id,
                source_id,
                1,
                source_hash,
                source_hash,
                "2026-08-11",
                "day",
                "exact",
                9,
                "Conditional L2 candidate review fixture.",
                "external_first_party",
                now,
            ),
        )
        connection.execute(
            """INSERT INTO extraction_run
               (extraction_run_id,algorithm_version,baseline_graph_sha256,
                input_manifest_sha256,started_at,completed_at,status,deterministic)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                extraction_run_id,
                "conditional-l2-test-structured-adapter-v1",
                baseline_graph_sha256,
                source_hash,
                now,
                now,
                "completed",
                1,
            ),
        )
        connection.execute(
            """INSERT INTO evidence_item
               (evidence_id,evidence_fingerprint,source_id,source_version_id,locator_kind,
                locator_value,page_number,section_name,paragraph_number,row_reference,slide_number,
                transcript_timestamp,direct_quote,evidence_directness,source_authority_rank,
                extraction_confidence,corroboration_group,location_status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id,
                digest({"evidence": evidence_id}),
                source_id,
                source_version_id,
                "section",
                "Conditional L2 candidate assertion",
                None,
                "Candidate review fixture",
                None,
                None,
                None,
                None,
                "The source supports a bounded candidate review.",
                "direct",
                9,
                1.0,
                "conditional-l2-candidate-corroboration",
                "exact",
                now,
            ),
        )
        connection.execute(
            """INSERT INTO assertion
               (assertion_id,assertion_fingerprint,subject_node_id,predicate,legacy_predicate,
                object_node_id,object_literal,object_literal_type,assertion_type,atomicity_state,
                scope_text,speaker_or_author,primary_source_id,extraction_run_id,extraction_method,
                origin_type,confidence_label,confidence_score,validation_state,currentness_state,
                valid_from,valid_to,date_precision,date_status,recorded_at,superseded_at,
                superseded_by_assertion_id,resolution_state,canonical_status,canonical_eligible,
                retrieval_eligible,materiality,classification,allowed_audiences_json,hitl_required,
                validation_priority,validation_owner,qualifiers_json,legacy_record_type,
                legacy_record_ids_json,created_at,updated_at,approval_reference)
               VALUES (""" + ",".join("?" for _ in range(43)) + ")",
            (
                assertion_id,
                digest({"assertion": assertion_id}),
                subject["node_id"],
                "HAS_CLAIM_TEXT",
                None,
                None,
                "Project Beacon candidate review requires source-bound promotion evidence.",
                "text",
                "extracted_claim",
                "atomic",
                "Project Beacon candidate review fixture",
                None,
                source_id,
                extraction_run_id,
                "conditional-l2-test-fixture",
                "human_source",
                "high",
                0.99,
                "needs_review",
                "current",
                "2026-08-11",
                None,
                "day",
                "exact",
                now,
                None,
                None,
                "resolved",
                "candidate",
                0,
                1,
                "medium",
                "internal",
                json.dumps(["nee-engineering"]),
                1,
                "P1",
                "nee-engineering-review",
                json.dumps(
                    {
                        "answer_use": "candidate_hypothesis",
                        "dual_pipeline_candidate_type": "assertion",
                        "fact_promotion_eligible": True,
                        "retrieval_kind": "assertion",
                    },
                    sort_keys=True,
                ),
                "claim",
                "[]",
                now,
                now,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO assertion_evidence VALUES (?,?,?,?,?)",
            (assertion_id, evidence_id, "supports", 1, now),
        )
        connection.execute(
            """INSERT INTO search_document
               (search_document_id,object_type,object_id,title,body,canonical_status,
                currentness_state,retrieval_eligible,source_id,assertion_id,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "search-conditional-l2-candidate",
                "assertion",
                assertion_id,
                "Project Beacon candidate review",
                "Project Beacon candidate review requires source-bound promotion evidence.",
                "candidate",
                "current",
                0,
                source_id,
                assertion_id,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


class CountingEmbeddingBackend(BgeM3TestBackend):
    supports_batch_encoding = True

    def __init__(self) -> None:
        self.encode_count = 0

    def encode(self, text: str):
        self.encode_count += 1
        return super().encode(text)

    def encode_many(self, texts: list[str]):
        self.encode_count += len(texts)
        return super().encode_many(texts)


class NeverInitializeFactory:
    def __init__(self) -> None:
        self.initialization_count = 0

    def __call__(self, model_policy):
        del model_policy
        self.initialization_count += 1
        raise AssertionError("external provider must remain lazy")


class ConditionalL2AcceptanceTests(unittest.TestCase):
    def test_decision_output_schema_requires_every_top_level_property(self) -> None:
        self.assertEqual(
            set(DECISION_MODEL_OUTPUT_SCHEMA["properties"]),
            set(DECISION_MODEL_OUTPUT_SCHEMA["required"]),
        )

    def test_semantic_quality_uses_bound_route_material_records(self) -> None:
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "unrelated-one",
                        "evidence_ids": ["evidence-unrelated-one"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
                {
                    "record": {
                        "record_id": "unrelated-two",
                        "evidence_ids": ["evidence-unrelated-two"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
                {
                    "record": {
                        "record_id": "risk-one",
                        "evidence_ids": ["evidence-risk-one"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
                {
                    "record": {
                        "record_id": "risk-two",
                        "evidence_ids": ["evidence-risk-two"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
            ],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "graph_paths": [],
            "required_material_record_ids_by_lane": {
                "approved": ["risk-one", "risk-two"],
                "candidate": [],
                "external_public": [],
            },
        }
        answer = {
            "query_type": "gap_risk_inconsistency",
            "current_state": [],
            "patterns": [],
            "pain_points": [
                {
                    "pain_point": "Governed risk one",
                    "citations": ["risk-one"],
                    "evidence_ids": ["evidence-risk-one"],
                },
                {
                    "pain_point": "Governed risk two",
                    "citations": ["risk-two"],
                    "evidence_ids": ["evidence-risk-two"],
                },
            ],
            "opportunities": [],
            "guidance": [],
            "hypotheses": [],
            "source_evidence": [],
            "next_best_actions": [],
            "counterevidence": [],
            "assumptions": ["The active governed snapshot is the supplied evidence boundary."],
            "what_would_change_the_conclusion": ["A newer approved risk record would change the conclusion."],
            "proposed_assertions": [],
            "rationale": "The two route-specific risks are supported by their governed records.",
            "automatic_graph_mutation_count": 0,
        }
        plan = {"query_type": "gap_risk_inconsistency", "materiality": "medium"}
        self.assertEqual(
            (),
            evaluate_semantic_quality(
                question="Assess the governed gaps and risks.",
                plan=plan,
                evidence_packet=packet,
                answer=answer,
            ),
        )

        missing = deepcopy(answer)
        missing["pain_points"] = missing["pain_points"][:1]
        deficiencies = evaluate_semantic_quality(
            question="Assess the governed gaps and risks.",
            plan=plan,
            evidence_packet=packet,
            answer=missing,
        )
        coverage_deficiency = next(
            deficiency
            for deficiency in deficiencies
            if deficiency.field == "evidence_coverage"
        )
        self.assertEqual(("risk-two",), coverage_deficiency.missing_record_ids)

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="nee-conditional-l2-")
        cls.runtime = Path(cls.temp.name) / "runtime"
        cls.graph = Path(cls.temp.name) / "conditional-l2-graph.sqlite"
        exact_file_copy(ROOT / "graph" / "nee_graph.sqlite", cls.graph)
        _insert_source_bound_candidate(cls.graph)
        cls.graph_sha256 = sha256_file(cls.graph)
        cls.backend = CountingEmbeddingBackend()
        cls.manager = ReleaseManager(cls.runtime, cls.graph)
        cls.active_pair = cls.manager.bootstrap(cls.backend)
        cls.backend.encode_count = 0
        cls.config = load_l2_config()
        cls.model_environment = mock.patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-only-not-a-real-key"}
        )
        cls.model_environment.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.model_environment.stop()
        cls.temp.cleanup()

    def _ask(
        self,
        question: str,
        *,
        query_type: str | None,
        factory=None,
        materiality: str | None = None,
        provider_egress_allowed: bool = True,
    ):
        result = QueryEngine(
            self.runtime,
            self.graph,
            self.backend,
            provider_factory=factory,
            l2_config=self.config,
        ).ask(
            question,
            query_type=query_type,
            materiality=materiality,
            provider_egress_allowed=provider_egress_allowed,
            audience=AUDIENCE,
        )
        return result, self.runtime / "runs" / result["run_id"]

    def _assert_call_counts(
        self,
        result: dict,
        *,
        primary: int,
        repair: int,
        escalation: int,
    ) -> None:
        self.assertEqual(primary, result["primary_call_count"])
        self.assertEqual(repair, result["schema_repair_call_count"])
        self.assertEqual(escalation, result["quality_escalation_call_count"])
        self.assertEqual(primary + repair + escalation, result["model_call_count"])
        self.assertEqual(result["model_call_count"], result["external_l2_call_count"])
        self.assertEqual(0, result["judge_call_count"])
        self.assertEqual(0, result["challenger_call_count"])

    def _assert_sealed_stage_chain(
        self,
        result: dict,
        run_dir: Path,
        expected_stages: list[str],
    ) -> None:
        """Assert the summary and durable run seal agree on the exact stage chain."""

        self.assertEqual(expected_stages, result["required_stages"])
        seal = read_json(run_dir / "run_seal.json")
        self.assertEqual("PASS", seal["status"])
        self.assertEqual(expected_stages, seal["required_stages"])
        self.assertEqual(expected_stages, seal["observed_stages"])
        self.assertFalse(seal["missing_stages"])

    def _assert_completed_di_l2_contract(self, result: dict, run_dir: Path) -> None:
        expected_stages = [
            "U0",
            "L1",
            "L0",
            "C0.1",
            "C0.2",
            "C0.3",
            "L2D",
            "PA",
            "L2",
            "X1",
            "X2",
            "X3",
            "L4",
        ]
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("NEE_DECISION_INTELLIGENCE", result["destination"])
        self._assert_sealed_stage_chain(result, run_dir, expected_stages)
        for artifact in (
            "l2_execution_decision.json",
            "l2_evidence_packet.json",
            "prompt_assembly.json",
            "l2_execution.json",
            "selected_answer.json",
            "x_gates.json",
        ):
            self.assertTrue((run_dir / artifact).is_file(), artifact)

    def _assert_pre_l2_coverage_block_contract(self, result: dict, run_dir: Path) -> None:
        expected_stages = [
            "U0",
            "L1",
            "L0",
            "C0.1",
            "C0.2",
            "C0.3",
            "L2D",
            "X1",
            "X2",
            "X3",
            "L4",
        ]
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("NEE_DECISION_INTELLIGENCE", result["destination"])
        self.assertEqual(
            "RETRIEVAL_COVERAGE_INSUFFICIENT", result["terminal_outcome"]
        )
        self._assert_sealed_stage_chain(result, run_dir, expected_stages)
        for artifact in (
            "l2_execution_decision.json",
            "l2_evidence_packet.json",
            "prompt_assembly.json",
            "l2_execution.json",
            "selected_answer.json",
        ):
            self.assertFalse((run_dir / artifact).exists(), artifact)

    def test_A_exact_lookup_forbids_external_l2(self) -> None:
        factory = NeverInitializeFactory()
        result, run_dir = self._ask(
            "Project Beacon governed record A",
            query_type="exact_lookup",
            factory=factory,
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("forbidden", result["l2_policy"])
        self.assertFalse(result["l2_required"])
        self.assertEqual("EXACT_LOOKUP_DETERMINISTIC", result["l2_reason_code"])
        self.assertEqual("SKIPPED", result["l2_disposition"])
        self.assertEqual(0, factory.initialization_count)
        self._assert_call_counts(result, primary=0, repair=0, escalation=0)
        self.assertFalse((run_dir / "prompt_assembly.json").exists())
        self.assertIsNone(result["prompt_hash"])
        plan = read_json(run_dir / "query_plan.json")
        retrieval = read_json(run_dir / "hybrid_retrieval.json")
        exact = read_json(run_dir / "exact_retrieval.json")
        self.assertEqual("factual", plan["retrieval_mode"])
        self.assertEqual(["approved"], plan["allowed_lanes"])
        self.assertEqual({"approved"}, set(retrieval["lanes"]))
        self.assertTrue(
            all(row["lane"] == "approved" and row["answer_use"] == "factual_answer"
                for row in exact["exact_anchors"])
        )
        decision = read_json(run_dir / "l2_execution_decision.json")
        self.assertFalse(decision["required"])

    def test_B_simple_current_state_skips_l2_but_runs_local_embedding(self) -> None:
        factory = NeverInitializeFactory()
        before = self.backend.encode_count
        result, run_dir = self._ask(
            "What is the current status of Project Beacon B?",
            query_type="current_state_corroboration",
            factory=factory,
            materiality="high",
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("forbidden", result["l2_policy"])
        self.assertFalse(result["l2_required"])
        self.assertEqual("EXACT_LOOKUP_DETERMINISTIC", result["l2_reason_code"])
        self.assertGreater(self.backend.encode_count, before)
        self.assertTrue((run_dir / "hybrid_retrieval.json").is_file())
        self.assertFalse((run_dir / "prompt_assembly.json").exists())
        self.assertEqual(0, factory.initialization_count)
        self._assert_call_counts(result, primary=0, repair=0, escalation=0)

    def test_C_completed_decision_intelligence_seals_full_l2_stage_chain(self) -> None:
        factory = FakeProviderFactory()
        result, run_dir = self._ask(
            "Why is the current Project Beacon status governed this way C?",
            query_type="relationship_explanation",
            factory=factory,
            materiality="high",
        )
        self.assertTrue(result["l2_required"])
        self.assertEqual(NORMAL_MODEL, result["selected_model"])
        self.assertEqual([NORMAL_MODEL], factory.called_models)
        self.assertEqual(["PRIMARY"], factory.call_purposes)
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)
        self._assert_completed_di_l2_contract(result, run_dir)

    def test_D_normal_relationship_explanation_uses_openai_once(self) -> None:
        factory = FakeProviderFactory()
        result, run_dir = self._ask(
            "Explain the governed relationship between Project Beacon and AI-enabled SDLC / PDLC D.",
            query_type="relationship_explanation",
            factory=factory,
            materiality="medium",
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("standard", result["selected_model_profile"])
        self.assertEqual([NORMAL_MODEL], factory.called_models)
        self.assertFalse((run_dir / "query_classification.json").exists())
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)

    def test_D2_ambiguous_route_is_provider_free_and_explicit(self) -> None:
        factory = NeverInitializeFactory()
        result, run_dir = self._ask(
            "Tell me about Chakra D2.",
            query_type=None,
            factory=factory,
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("ROUTE_AMBIGUOUS", result["destination"])
        self.assertEqual("ROUTE_AMBIGUOUS", result["terminal_outcome"])
        self.assertEqual(0, result["total_external_model_call_count"])
        self.assertEqual(0, factory.initialization_count)
        self.assertTrue((run_dir / "query_destination_decision.json").is_file())
        self.assertFalse((run_dir / "query_classification.json").exists())

    def test_D3_eligible_route_has_no_classification_artifact(self) -> None:
        factory = FakeProviderFactory()
        result, run_dir = self._ask(
            "Explain the governed relationship between Project Beacon and PDLC D3.",
            query_type=None,
            factory=factory,
        )
        self.assertIn(result["status"], {"COMPLETED", "BLOCKED"})
        self.assertEqual("NEE_DECISION_INTELLIGENCE", result["destination"])
        self.assertFalse((run_dir / "query_classification.json").exists())

    def test_D4_candidate_lane_requires_explicit_query_type(self) -> None:
        result, run_dir = self._ask(
            "Review the governed candidate assertions D4.",
            query_type="candidate_promotion_review",
            factory=FakeProviderFactory(),
        )
        self.assertEqual("candidate_promotion_review", result["query_type"])
        plan = read_json(run_dir / "query_plan.json")
        self.assertEqual("candidate_review", plan["retrieval_mode"])
        self.assertEqual(["approved", "candidate"], plan["allowed_lanes"])
        self.assertFalse((run_dir / "query_classification.json").exists())

    def test_E_pre_l2_coverage_block_seals_no_pa_or_l2_artifacts(self) -> None:
        factory = FakeProviderFactory()
        result, run_dir = self._ask(
            "Explain the strategic relationship between Project Beacon and PDLC E.",
            query_type="relationship_explanation",
            factory=factory,
            materiality="high",
        )
        self.assertEqual(
            "enhanced",
            read_json(run_dir / "query_plan.json")["assurance_profile"],
        )
        self.assertEqual([], factory.called_models)
        self._assert_pre_l2_coverage_block_contract(result, run_dir)

    def test_F_cross_workstream_synthesis_defaults_normal_and_calls_once(self) -> None:
        factory = FakeProviderFactory()
        result, _ = self._ask(
            "Synthesize governed patterns across Project Beacon and NEA F.",
            query_type="cross_workstream_synthesis",
            factory=factory,
        )
        self.assertEqual("standard", result["selected_model_profile"])
        self.assertEqual([NORMAL_MODEL], factory.called_models)
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)

    def test_G_gap_risk_inconsistency_defaults_normal_and_calls_once(self) -> None:
        factory = FakeProviderFactory()
        result, _ = self._ask(
            "Assess the governed Project Beacon gaps and risks G.",
            query_type="gap_risk_inconsistency",
            factory=factory,
        )
        self.assertEqual("standard", result["selected_model_profile"])
        self.assertEqual([NORMAL_MODEL], factory.called_models)
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)

    def test_H_normal_opportunity_uses_openai_once(self) -> None:
        factory = FakeProviderFactory()
        result, run_dir = self._ask(
            "Recommend one governed Project Beacon opportunity H.",
            query_type="opportunity_nba",
            factory=factory,
            materiality="medium",
        )
        plan = read_json(run_dir / "query_plan.json")
        self.assertEqual("standard", plan["assurance_profile"])
        retrieval = read_json(run_dir / "hybrid_retrieval.json")
        self.assertEqual("strategic", plan["retrieval_mode"])
        self.assertEqual(
            ["approved", "candidate", "external_public"],
            plan["allowed_lanes"],
        )
        self.assertEqual(
            {"approved", "candidate", "external_public"},
            set(retrieval["lanes"]),
        )

    def test_I_high_opportunity_uses_openai_once(self) -> None:
        factory = FakeProviderFactory()
        result, _ = self._ask(
            "Recommend a strategic governed Project Beacon opportunity I.",
            query_type="opportunity_nba",
            factory=factory,
            materiality="high",
        )
        run_dir = self.runtime / "runs" / result["run_id"]
        self.assertEqual(
            "enhanced",
            read_json(run_dir / "query_plan.json")["assurance_profile"],
        )

    def test_I2_enhanced_architecture_uses_terra_at_max_once(self) -> None:
        factory = FakeProviderFactory()
        question = (
            "Synthesize the future-state architecture, functional design, technical "
            "design, A2A patterns, modularity, loose coupling, governance, security, "
            "observability, durable state, failure handling, and extension model. "
        ) * 8
        result, run_dir = self._ask(
            question,
            query_type="cross_workstream_synthesis",
            factory=factory,
            materiality="high",
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("enhanced", result["assurance_profile"])
        self.assertEqual(MOST_COMPLEX_MODEL, result["selected_model"])
        self.assertEqual([MOST_COMPLEX_MODEL], factory.called_models)
        self.assertEqual(
            ["high"],
            [record.model_policy.reasoning_effort for record in factory.requests],
        )
        decision = read_json(run_dir / "l2_execution_decision.json")
        self.assertEqual("2.0.0", decision["schema_version"])
        self.assertEqual("enhanced", decision["assurance_profile"])
        self.assertEqual("enhanced", decision["selected_model_profile"])
        self.assertNotIn("initial_complexity", decision)
        self.assertNotIn("final_complexity", decision)
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)

    def test_J_candidate_review_is_proposal_only_and_never_writes_graph(self) -> None:
        factory = FakeProviderFactory()
        before = sha256_file(self.graph)
        result, run_dir = self._ask(
            "Review the Project Beacon candidate promotion proposal J.",
            query_type="candidate_promotion_review",
            factory=factory,
            materiality="medium",
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("enhanced", result["selected_model_profile"])
        self.assertEqual([HIGH_MODEL], factory.called_models)
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)
        self.assertEqual(before, sha256_file(self.graph))
        self.assertEqual(result["graph_sha256_before"], result["graph_sha256_after"])
        self.assertEqual(0, result["graph_mutation_count"])
        proposals = result["answer"]["proposed_assertions"]
        self.assertTrue(proposals)
        self.assertTrue(all(row["proposal_only"] for row in proposals))
        self.assertTrue(all(row["lane"] == "candidate" for row in proposals))
        plan = read_json(run_dir / "query_plan.json")
        retrieval = read_json(run_dir / "hybrid_retrieval.json")
        self.assertEqual("candidate_review", plan["retrieval_mode"])
        self.assertEqual(["approved", "candidate"], plan["allowed_lanes"])
        self.assertEqual({"approved", "candidate"}, set(retrieval["lanes"]))

    def test_J1_candidate_review_does_not_require_an_opportunity_or_action(self) -> None:
        result, run_dir = self._ask(
            "Should NEE promote the governed Project Beacon candidate or keep it as a candidate?",
            query_type="candidate_promotion_review",
            factory=NeverInitializeFactory(),
            materiality="high",
            provider_egress_allowed=False,
        )
        self.assertEqual("COMPLETED", result["status"])
        plan = read_json(run_dir / "query_plan.json")
        packet = read_json(run_dir / "evidence_packet.json")
        answer = reason_over_packet(plan, packet)
        self.assertFalse(answer["opportunities"])
        self.assertFalse(answer["next_best_actions"])
        deficiencies = evaluate_semantic_quality(
            question="Should NEE promote the governed Project Beacon candidate or keep it as a candidate?",
            plan=plan,
            evidence_packet=packet,
            answer=answer,
        )
        self.assertFalse(
            any(
                deficiency.code.value == "RECOMMENDATION_RATIONALE_INCOMPLETE"
                for deficiency in deficiencies
            )
        )

    def test_J2_guidance_cannot_be_cited_as_a_factual_claim(self) -> None:
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "record-factual",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "Approved fact",
                        "retrieval_text": "Approved risk evidence supports the governed opportunity.",
                        "evidence_ids": ["evidence-factual"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "evidence": [{"evidence_id": "evidence-factual"}],
                },
                {
                    "record": {
                        "record_id": "record-guidance",
                        "record_type": "action_item",
                        "lane": "approved",
                        "title": "Approved guidance",
                        "retrieval_text": "Validate the opportunity with the accountable owner.",
                        "evidence_ids": ["evidence-guidance"],
                    },
                    "retrieval_metadata": {"answer_use": "guidance"},
                    "evidence": [{"evidence_id": "evidence-guidance"}],
                },
            ],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "gaps": [],
        }
        valid = reason_over_packet({"query_type": "opportunity_nba"}, packet)
        self.assertTrue(
            validate_decision_model_output(
                valid,
                evidence_packet=packet,
                expected_query_type="opportunity_nba",
            ).valid
        )
        forged = deepcopy(valid)
        forged["current_state"] = [
            {
                "statement": "A guidance item is not a fact.",
                "citations": ["record-guidance"],
                "evidence_ids": ["evidence-guidance"],
                "lane": "approved",
            }
        ]
        validation = validate_decision_model_output(
            forged,
            evidence_packet=packet,
            expected_query_type="opportunity_nba",
        )
        self.assertFalse(validation.valid)
        self.assertTrue(
            any("answer-use" in issue.message for issue in validation.issues)
        )

    def test_K_schema_repair_uses_same_model_once_and_no_new_evidence(self) -> None:
        factory = FakeProviderFactory(
            {CallPurpose.PRIMARY: [missing_schema_version]}
        )
        result, run_dir = self._ask(
            "Explain the governed Project Beacon relationship K.",
            query_type="relationship_explanation",
            factory=factory,
            materiality="high",
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual([NORMAL_MODEL, NORMAL_MODEL], factory.called_models)
        self.assertEqual(["PRIMARY", "SCHEMA_REPAIR"], factory.call_purposes)
        self._assert_call_counts(result, primary=1, repair=1, escalation=0)
        repair = factory.requests[1].request
        self.assertIsInstance(repair.original_response, dict)
        self.assertNotIn("schema_version", repair.original_response)
        self.assertTrue(repair.validation_errors)
        self.assertEqual("", repair.question)
        self.assertEqual({}, repair.plan)
        self.assertIsNone(repair.evidence_packet)
        self.assertIsNone(repair.prompt)
        ledger = read_json(run_dir / "l2_execution.json")
        self.assertEqual("REPAIRED", ledger["l2_disposition"])

        truncated_factory = FakeProviderFactory(
            {CallPurpose.PRIMARY: [truncated_valid_json]}
        )
        truncated, _ = self._ask(
            "Explain the governed Project Beacon relationship K truncated.",
            query_type="relationship_explanation",
            factory=truncated_factory,
            materiality="high",
        )
        self.assertEqual("COMPLETED", truncated["status"])
        self.assertEqual([NORMAL_MODEL, NORMAL_MODEL], truncated_factory.called_models)
        self.assertEqual(
            ["PRIMARY", "SCHEMA_REPAIR"], truncated_factory.call_purposes
        )
        self.assertIsInstance(truncated_factory.requests[1].request.original_response, str)
        self.assertFalse(
            truncated_factory.requests[1].request.original_response.endswith("}")
        )
        self._assert_call_counts(truncated, primary=1, repair=1, escalation=0)

        malformed_factory = FakeProviderFactory(
            {CallPurpose.PRIMARY: ['{"rationale":"unterminated']}
        )
        malformed, _ = self._ask(
            "Explain the governed Project Beacon relationship K malformed.",
            query_type="relationship_explanation",
            factory=malformed_factory,
            materiality="medium",
        )
        self.assertEqual("BLOCKED", malformed["status"])
        self.assertEqual(["PRIMARY"], malformed_factory.call_purposes)
        self.assertEqual(
            "INVALID_STRUCTURED_RESPONSE", malformed["provider_failure_code"]
        )
        self._assert_call_counts(malformed, primary=1, repair=0, escalation=0)

        citation_factory = FakeProviderFactory(
            {CallPurpose.PRIMARY: [missing_unambiguous_current_state_citation]}
        )
        citation_repaired, _ = self._ask(
            "Explain the governed Project Beacon relationship K citation repair.",
            query_type="relationship_explanation",
            factory=citation_factory,
            materiality="high",
        )
        self.assertEqual("COMPLETED", citation_repaired["status"])
        self.assertEqual(
            ["PRIMARY", "SCHEMA_REPAIR"], citation_factory.call_purposes
        )
        citation_repair = citation_factory.requests[1].request
        missing_row = next(
            row
            for row in citation_repair.original_response["current_state"]
            if "citations" not in row
        )
        primary_packet = citation_factory.requests[0].request.evidence_packet
        self.assertIsNotNone(primary_packet)
        allowed_record_ids = {
            item["record"]["record_id"]
            for lane in ("approved", "candidate", "external_public")
            for item in primary_packet[lane]
            if set(item["record"]["evidence_ids"]) & set(missing_row["evidence_ids"])
        }
        self.assertEqual(1, len(allowed_record_ids))
        allowed_record_id = next(iter(allowed_record_ids))
        self.assertTrue(
            any(
                f"allowed_record_ids={allowed_record_id}" in error
                for error in citation_repair.validation_errors
            ),
            citation_repair.validation_errors,
        )
        self.assertEqual("", citation_repair.question)
        self.assertEqual({}, citation_repair.plan)
        self.assertIsNone(citation_repair.evidence_packet)
        self.assertIsNone(citation_repair.prompt)
        self._assert_call_counts(
            citation_repaired, primary=1, repair=1, escalation=0
        )

    def test_L_standard_typed_quality_failure_blocks_without_escalation(self) -> None:
        factory = FakeProviderFactory(
            {CallPurpose.PRIMARY: [schema_valid_quality_failure]}
        )
        result, run_dir = self._ask(
            "Explain the governed Project Beacon relationship and rationale L.",
            query_type="relationship_explanation",
            factory=factory,
            materiality="medium",
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual([NORMAL_MODEL], factory.called_models)
        self.assertEqual(["PRIMARY"], factory.call_purposes)
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)
        self.assertEqual("SEMANTIC_QUALITY_GATE_FAILED", result["provider_failure_code"])

    def test_M_failed_escalation_blocks_without_a_third_call(self) -> None:
        factory = FakeProviderFactory(
            {
                CallPurpose.PRIMARY: [schema_valid_quality_failure],
                CallPurpose.QUALITY_ESCALATION: [schema_valid_quality_failure],
            }
        )
        result, _ = self._ask(
            "Explain the governed Project Beacon relationship and rationale M.",
            query_type="relationship_explanation",
            factory=factory,
            materiality="high",
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("X3B_ESCALATE_HITL", result["x3_outcome"])
        self.assertEqual("SEMANTIC_QUALITY_GATE_FAILED", result["provider_failure_code"])
        self.assertEqual([MOST_COMPLEX_MODEL, MOST_COMPLEX_MODEL], factory.called_models)
        self.assertEqual(
            ["PRIMARY", "QUALITY_ESCALATION"], factory.call_purposes
        )
        self._assert_call_counts(result, primary=1, repair=0, escalation=1)

    def test_N_required_l2_with_missing_api_key_fails_closed_before_call(self) -> None:
        factory = OpenAIProviderFactory(self.config)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "dual_pipeline.l2._key_from_ignored_local_env",
            return_value=None,
        ):
            result, run_dir = self._ask(
                "Explain the governed Project Beacon relationship N.",
                query_type="relationship_explanation",
                factory=factory,
                materiality="medium",
            )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("X3B_ESCALATE_HITL", result["x3_outcome"])
        self.assertEqual("MISSING_API_KEY", result["provider_failure_code"])
        self.assertEqual(1, result["provider_initialization_count"])
        self._assert_call_counts(result, primary=0, repair=0, escalation=0)
        self.assertTrue((run_dir / "l2_execution_decision.json").is_file())
        self.assertTrue((run_dir / "evidence_packet.json").is_file())

    def test_N1_oversized_request_fails_before_provider_initialization(self) -> None:
        config = replace(
            self.config,
            request_budget=replace(
                self.config.request_budget,
                maximum_provider_request_chars=1,
            ),
        )
        factory = NeverInitializeFactory()
        result = QueryEngine(
            self.runtime,
            self.graph,
            self.backend,
            provider_factory=factory,
            l2_config=config,
        ).ask(
            "Explain the governed Project Beacon relationship budget.",
            query_type="relationship_explanation",
            materiality="medium",
            audience=AUDIENCE,
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BUDGET_EXHAUSTED", result["provider_failure_code"])
        self.assertEqual(0, result["provider_initialization_count"])
        self.assertEqual(0, factory.initialization_count)
        self._assert_call_counts(result, primary=0, repair=0, escalation=0)

    def test_N2_openai_responses_uses_bound_model_and_strict_schema(self) -> None:
        captured: dict[str, object] = {}

        class FakeHttpResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                del args

            def read(self):
                return (
                    b'{"model":"gpt-5.6-luna","output_text":"{}",'
                    b'"usage":{"input_tokens":12,"input_tokens_details":'
                    b'{"cached_tokens":2},"output_tokens":3,"total_tokens":15}}'
                )

        def urlopen(request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeHttpResponse()

        provider = OpenAIProviderFactory(self.config)(self.config.standard)
        with mock.patch("dual_pipeline.l2.urllib.request.urlopen", side_effect=urlopen):
            response = provider.generate(
                L2ProviderRequest(
                    purpose=CallPurpose.PRIMARY,
                    question="Return a governed object.",
                    plan={},
                    evidence_packet={},
                    prompt={},
                )
            )
        self.assertEqual({}, response.output)
        self.assertEqual(NORMAL_MODEL, response.model)
        self.assertEqual(12, response.usage.input_tokens)
        self.assertEqual(2, response.usage.cached_input_tokens)
        self.assertEqual(3, response.usage.output_tokens)
        request = captured["request"]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(NORMAL_MODEL, body["model"])
        self.assertEqual("high", body["reasoning"]["effort"])
        self.assertEqual([], body["tools"])
        self.assertFalse(body["store"])
        self.assertEqual("json_schema", body["text"]["format"]["type"])
        schema = body["text"]["format"]["schema"]
        pending = [("$", schema)]
        while pending:
            path, node = pending.pop()
            if not isinstance(node, dict):
                continue
            if node.get("type") == "object":
                properties = node.get("properties", {})
                self.assertEqual(
                    set(properties),
                    set(node.get("required", [])),
                    f"strict provider schema has optional properties at {path}",
                )
                self.assertFalse(node.get("additionalProperties", True), path)
            for name, child in node.items():
                if isinstance(child, dict):
                    pending.append((f"{path}.{name}", child))
                elif isinstance(child, list):
                    pending.extend(
                        (f"{path}.{name}[{index}]", item)
                        for index, item in enumerate(child)
                        if isinstance(item, dict)
                    )
        self.assertEqual(
            "^CANDIDATE:",
            schema["properties"]["hypotheses"]["items"]["properties"][
                "hypothesis"
            ]["pattern"],
        )

    def test_N3_openai_incomplete_response_preserves_reason_usage_and_digest(self) -> None:
        class FakeHttpResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                del args

            def read(self):
                return (
                    b'{"model":"gpt-5.6-luna","status":"incomplete",'
                    b'"incomplete_details":{"reason":"max_output_tokens"},'
                    b'"usage":{"input_tokens":120,"input_tokens_details":'
                    b'{"cached_tokens":20},"output_tokens":16384,'
                    b'"total_tokens":16504}}'
                )

        provider = OpenAIProviderFactory(self.config)(self.config.standard)
        with mock.patch(
            "dual_pipeline.l2.urllib.request.urlopen",
            return_value=FakeHttpResponse(),
        ), self.assertRaises(L2ProviderError) as caught:
            provider.generate(
                L2ProviderRequest(
                    purpose=CallPurpose.PRIMARY,
                    question="Return a governed object.",
                    plan={},
                    evidence_packet={},
                    prompt={},
                )
            )
        failure = caught.exception
        self.assertEqual(ProviderFailureCode.BUDGET_EXHAUSTED, failure.code)
        self.assertEqual(
            "provider response incomplete: max_output_tokens", failure.safe_message
        )
        self.assertIsNotNone(failure.usage)
        self.assertEqual(16384, failure.usage.output_tokens)
        self.assertIsNotNone(failure.response_digest)
        self.assertIsNotNone(failure.latency_ms)
        call_record = build_call_record(
            model_policy=self.config.standard,
            model_profile="standard",
            purpose=CallPurpose.PRIMARY,
            ordinal=1,
            response=None,
            latency_ms=failure.latency_ms,
            failure=failure,
        )
        self.assertEqual(16384, call_record.output_tokens)
        self.assertEqual(16504, call_record.total_tokens)
        self.assertEqual(failure.response_digest, call_record.response_digest)
        self.assertEqual(failure.latency_ms, call_record.latency_ms)

    def test_O_deterministic_query_succeeds_when_provider_is_unavailable(self) -> None:
        factory = NeverInitializeFactory()
        result, _ = self._ask(
            "Project Beacon governed record O",
            query_type="exact_lookup",
            factory=factory,
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(0, factory.initialization_count)
        self._assert_call_counts(result, primary=0, repair=0, escalation=0)

    def test_P_offline_required_query_returns_sealed_evidence_only(self) -> None:
        factory = NeverInitializeFactory()
        with mock.patch.dict(os.environ, {"NEE_EXTERNAL_L2_OFFLINE": "1"}):
            result, run_dir = self._ask(
                "Explain the governed Project Beacon relationship P.",
                query_type="relationship_explanation",
                factory=factory,
                materiality="medium",
            )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("X3E_ALLOW_EVIDENCE_ONLY", result["x3_outcome"])
        self.assertEqual("evidence_only", result["answer_delivery"])
        self.assertEqual("unavailable", result["synthesis_status"])
        self.assertEqual(
            "OFFLINE_EXTERNAL_PROVIDER_FORBIDDEN", result["evidence_only_reason"]
        )
        self.assertEqual("OFFLINE_EXTERNAL_PROVIDER_FORBIDDEN", result["l2_reason_code"])
        self.assertFalse(result["provider_egress_allowed"])
        self.assertEqual(0, factory.initialization_count)
        self._assert_call_counts(result, primary=0, repair=0, escalation=0)
        self.assertTrue(result["answer"]["current_state"])
        self.assertIn("Evidence-only", (run_dir / "answer.md").read_text(encoding="utf-8"))
        gates = read_json(run_dir / "x_gates.json")
        self.assertEqual("PASS", gates["X1"]["status"])
        self.assertEqual("PASS", gates["X2"]["status"])

    def test_Q_bge_retrieval_is_independent_when_l2_is_skipped(self) -> None:
        factory = NeverInitializeFactory()
        before = self.backend.encode_count
        result, run_dir = self._ask(
            "What is the current status of Project Beacon Q?",
            query_type="current_state_corroboration",
            factory=factory,
            materiality="medium",
        )
        retrieval = read_json(run_dir / "hybrid_retrieval.json")
        self.assertGreater(self.backend.encode_count, before)
        self.assertGreater(retrieval["final_record_count"], 0)
        self.assertTrue(result["dense_executed"])
        self.assertTrue(result["sparse_executed"])
        self.assertTrue(result["colbert_executed"])
        self.assertEqual(0, factory.initialization_count)
        self._assert_call_counts(result, primary=0, repair=0, escalation=0)

    def test_R_ingestion_has_static_and_runtime_zero_l2_boundary(self) -> None:
        protected = [
            ROOT / "dual_pipeline" / name
            for name in ("intake.py", "normalization.py", "identity.py")
        ] + list((ROOT / "ingestion").rglob("*.py")) + list(
            (ROOT / "intake").rglob("*.py")
        )
        violations: list[str] = []
        for path in protected:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (
                    node.module == "dual_pipeline.l2" or node.module == "l2"
                ):
                    violations.append(f"{path}:{node.lineno}")
                if isinstance(node, ast.Import) and any(
                    alias.name == "dual_pipeline.l2" for alias in node.names
                ):
                    violations.append(f"{path}:{node.lineno}")
        self.assertFalse(violations)

        active = self.manager.active_pair()
        engine = IntakeEngine(
            self.manager._resolve(active["graph_path"]),
            self.runtime,
            retriever=HybridRetriever(
                self.manager._resolve(active["sidecar_path"]), self.backend
            ),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
            active_pair=active,
        )
        with mock.patch(
            "dual_pipeline.l2.OpenAIReasoningProvider.__init__",
            side_effect=AssertionError("ingestion must not initialize OpenAI"),
        ) as provider_initializer:
            summary = engine.ingest(
                ROOT
                / "contracts"
                / "dual_pipeline"
                / "fixtures"
                / "valid"
                / "evidence-envelope-intake.json"
            )
        provider_initializer.assert_not_called()
        self.assertEqual("COMPLETED", summary["status"])
        self.assertEqual(0, summary["provider_call_count"])
        self.assertEqual(0, summary["external_l2_call_count"])
        self.assertEqual(0, summary["judge_call_count"])
        self.assertEqual(0, summary["challenger_call_count"])
        self.assertEqual(summary["graph_sha256_before"], summary["graph_sha256_after"])

    def test_S_ledger_and_otel_record_decision_usage_cost_and_disposition(self) -> None:
        factory = FakeProviderFactory()
        result, run_dir = self._ask(
            "Explain the governed Project Beacon relationship S.",
            query_type="relationship_explanation",
            factory=factory,
            materiality="medium",
        )
        ledger = read_json(run_dir / "l2_execution.json")
        self.assertEqual("PRIMARY", ledger["l2_disposition"])
        self.assertEqual(1, ledger["model_call_count"])
        self.assertEqual(100, ledger["input_tokens"])
        self.assertEqual(10, ledger["cached_input_tokens"])
        self.assertEqual(90, ledger["uncached_input_tokens"])
        self.assertEqual(50, ledger["output_tokens"])
        self.assertEqual(150, ledger["total_tokens"])
        self.assertEqual(2.0, ledger["input_output_ratio"])
        self.assertEqual(0.1, ledger["cached_input_share"])
        self.assertEqual(0.0000182, ledger["estimated_input_cost"])
        self.assertEqual(0.00006, ledger["estimated_output_cost"])
        self.assertEqual(0.0000782, ledger["estimated_total_cost"])
        call = ledger["calls"][0]
        required_call_fields = {
            "provider",
            "model",
            "model_profile",
            "reasoning_effort",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "total_tokens",
            "estimated_input_cost",
            "estimated_output_cost",
            "estimated_total_cost",
            "latency_ms",
            "call_purpose",
            "call_ordinal",
            "call_cap_status",
            "response_digest",
            "status",
        }
        self.assertTrue(required_call_fields <= set(call))

        otel = [read_json(path) for path in sorted((run_dir / "otel").glob("*.json"))]
        decision = next(event for event in otel if event["attributes"]["stage"] == "L2D")
        decision_fields = {
            "query_type",
            "materiality",
            "l2_policy",
            "l2_required",
            "l2_reason_code",
            "selected_model_profile",
            "selected_model",
            "provider_egress_allowed",
            "approved_evidence_count",
            "candidate_evidence_count",
            "conflict_count",
            "gap_count",
            "graph_path_count",
            "request_input_chars",
            "request_schema_chars",
            "request_serialized_chars",
            "request_payload_digest",
            "budget_status",
            "budget_reason_code",
        }
        self.assertTrue(decision_fields <= set(decision["attributes"]))
        self.assertEqual("ALLOW", decision["attributes"]["budget_status"])
        calls = [
            event
            for event in otel
            if event["attributes"].get("call_purpose") == "PRIMARY"
        ]
        self.assertEqual(1, len(calls))
        forbidden = re.compile(r"(?:raw|question|prompt|answer|claim|transcript|quote)", re.I)
        for event in otel:
            self.assertFalse([key for key in event["attributes"] if forbidden.search(key)])
        self._assert_call_counts(result, primary=1, repair=0, escalation=0)

    def test_T_all_call_caps_and_zero_judge_challenger_are_enforced(self) -> None:
        self.assertEqual(HIGH_MODEL, self.config.standard.model)
        self.assertEqual(MOST_COMPLEX_MODEL, self.config.enhanced.model)
        self.assertEqual("high", self.config.standard.reasoning_effort)
        self.assertEqual("high", self.config.enhanced.reasoning_effort)
        self.assertEqual(1, self.config.standard.maximum_primary_calls_per_run)
        self.assertEqual(0, self.config.standard.maximum_repair_calls_per_run)
        self.assertEqual(0, self.config.standard.maximum_escalation_calls_per_run)
        self.assertEqual(1, self.config.enhanced.maximum_primary_calls_per_run)
        self.assertEqual(1, self.config.enhanced.maximum_repair_calls_per_run)
        self.assertEqual(1, self.config.enhanced.maximum_escalation_calls_per_run)

        high_factory = FakeProviderFactory(
            {CallPurpose.PRIMARY: [schema_valid_quality_failure]}
        )
        high, _ = self._ask(
            "Explain the strategic governed Project Beacon relationship T high.",
            query_type="relationship_explanation",
            factory=high_factory,
            materiality="high",
        )
        self.assertEqual("COMPLETED", high["status"])
        self.assertEqual(
            ["PRIMARY", "QUALITY_ESCALATION"], high_factory.call_purposes
        )
        self.assertEqual(
            [MOST_COMPLEX_MODEL, MOST_COMPLEX_MODEL], high_factory.called_models
        )
        self.assertEqual(
            ["high", "high"],
            [record.model_policy.reasoning_effort for record in high_factory.requests],
        )
        self._assert_call_counts(high, primary=1, repair=0, escalation=1)
        escalation = high_factory.requests[1].request
        self.assertTrue(escalation.quality_deficiencies)

        repair_factory = FakeProviderFactory(
            {
                CallPurpose.PRIMARY: [missing_schema_version],
                CallPurpose.SCHEMA_REPAIR: [missing_schema_version],
            }
        )
        repaired, _ = self._ask(
            "Explain the governed Project Beacon relationship T repair.",
            query_type="relationship_explanation",
            factory=repair_factory,
            materiality="high",
        )
        self.assertEqual("BLOCKED", repaired["status"])
        self.assertEqual(["PRIMARY", "SCHEMA_REPAIR"], repair_factory.call_purposes)
        self._assert_call_counts(repaired, primary=1, repair=1, escalation=0)

    def test_U_inferred_semantic_intents_never_route_to_exact_lookup(self) -> None:
        semantic_questions = (
            "Explain the Project Beacon posture.",
            "Compare Project Beacon with Chakra.",
            "Recommend the next Project Beacon action.",
            "Prioritize the Project Beacon work.",
            "Defend the Project Beacon rationale.",
            "What are the implications of the Project Beacon status?",
            "Analyze PDLC.",
            "Evaluate PDLC.",
            "Summarize PDLC.",
            "Give an overview of PDLC.",
            "Which is better, PDLC or Chakra?",
            "What are the pros and cons of PDLC?",
            "What is the meaning of PDLC status?",
            "What is the priority for PDLC?",
        )
        for question in semantic_questions:
            with self.subTest(question=question):
                self.assertNotEqual(
                    QueryDestination.GRAPH_EVIDENCE_UTILITY,
                    route_query(question).destination,
                )
        self.assertNotEqual(
            QueryDestination.GRAPH_EVIDENCE_UTILITY,
            route_query("Assess PDLC.").destination,
        )
        self.assertEqual(
            QueryDestination.ROUTE_AMBIGUOUS,
            route_query("How many PDLC records are approved?").destination,
        )
        self.assertEqual(
            "exact_lookup",
            infer_query_type("Show governed record BP-1."),
        )

        bounded = analyze_request_intent(
            "current status of Research and Development"
        )
        self.assertTrue(bounded.bounded_factual_state)
        self.assertEqual(1, bounded.component_count)

        factory = FakeProviderFactory()
        result, _ = self._ask(
            "Compare Project Beacon with Chakra and explain the implications U.",
            query_type=None,
            factory=factory,
            materiality="medium",
        )
        self.assertNotEqual("exact_lookup", result["query_type"])
        self.assertEqual("NEE_DECISION_INTELLIGENCE", result["destination"])
        if result["status"] == "BLOCKED":
            self.assertEqual(
                "RETRIEVAL_COVERAGE_INSUFFICIENT",
                result["terminal_outcome"],
            )
            self.assertEqual([], factory.called_models)
        else:
            self.assertTrue(result["l2_required"])
            self.assertEqual([NORMAL_MODEL], factory.called_models)
            self._assert_call_counts(result, primary=1, repair=0, escalation=0)

    def test_V_tamper_and_nested_output_adversarial_controls_fail_closed(self) -> None:
        factory = FakeProviderFactory()
        result, run_dir = self._ask(
            "Review the governed Project Beacon candidate promotion proposal V.",
            query_type="candidate_promotion_review",
            factory=factory,
            materiality="medium",
        )
        self.assertEqual("COMPLETED", result["status"])
        validated_request = read_json(run_dir / "validated_request.json")
        plan = read_json(run_dir / "query_plan.json")
        route = read_json(run_dir / "route_decision.json")
        packet = read_json(run_dir / "evidence_packet.json")
        prompt = read_json(run_dir / "prompt_assembly.json")
        self.assertEqual(
            [item["record"]["record_id"] for item in packet["approved"][:2]],
            prompt["slots"]["R0"]["required_approved_citation_ids"],
        )
        self.assertTrue(
            prompt["slots"]["R0"]["candidate_hypothesis_required"]
        )
        self.assertEqual(
            "CANDIDATE:", prompt["slots"]["R0"]["candidate_text_prefix"]
        )
        decision = decide_l2_execution(
            validated_request=validated_request,
            plan=plan,
            route=route,
            evidence_packet=packet,
            config=self.config,
        )

        tampered_packet = deepcopy(packet)
        tampered_packet["gaps"].append("tampered unsealed gap")
        packet_factory = FakeProviderFactory()
        packet_failure = execute_conditional_l2(
            decision=decision,
            config=self.config,
            validated_request=validated_request,
            route=route,
            question=validated_request["normalized_question"],
            plan=plan,
            evidence_packet=tampered_packet,
            prompt=prompt,
            provider_factory=packet_factory,
        )
        self.assertEqual(L2Disposition.FAILED, packet_failure.disposition)
        self.assertEqual("INVALID_STRUCTURED_RESPONSE", packet_failure.provider_failure_code)
        self.assertEqual(0, packet_failure.provider_initialization_count)

        tampered_prompt = deepcopy(prompt)
        tampered_prompt["slots"]["U0"] = "tampered prompt"
        prompt_factory = FakeProviderFactory()
        prompt_failure = execute_conditional_l2(
            decision=decision,
            config=self.config,
            validated_request=validated_request,
            route=route,
            question=validated_request["normalized_question"],
            plan=plan,
            evidence_packet=packet,
            prompt=tampered_prompt,
            provider_factory=prompt_factory,
        )
        self.assertEqual(L2Disposition.FAILED, prompt_failure.disposition)
        self.assertEqual("INVALID_STRUCTURED_RESPONSE", prompt_failure.provider_failure_code)
        self.assertEqual(0, prompt_failure.provider_initialization_count)

        modified_request = deepcopy(validated_request)
        modified_question = (
            f"{validated_request['normalized_question']} Altered after the L2 decision."
        )
        modified_request["normalized_question"] = modified_question
        rehashed_prompt = deepcopy(prompt)
        rehashed_prompt["slots"]["U0"] = modified_question
        rehashed_prompt["slot_digests"]["U0"] = digest(modified_question)
        rehashed_prompt["prompt_hash"] = digest(
            {
                key: value
                for key, value in rehashed_prompt.items()
                if key != "prompt_hash"
            }
        )
        binding_factory = FakeProviderFactory()
        binding_failure = execute_conditional_l2(
            decision=decision,
            config=self.config,
            validated_request=modified_request,
            route=route,
            question=modified_question,
            plan=plan,
            evidence_packet=packet,
            prompt=rehashed_prompt,
            provider_factory=binding_factory,
        )
        self.assertEqual(L2Disposition.FAILED, binding_failure.disposition)
        self.assertEqual(
            "INVALID_STRUCTURED_RESPONSE", binding_failure.provider_failure_code
        )
        self.assertEqual(0, binding_failure.provider_initialization_count)
        self.assertEqual([], binding_factory.initialized_models)
        self.assertEqual([], binding_factory.requests)

        forged_skip_factory = FakeProviderFactory()
        forged_skip = execute_conditional_l2(
            decision=replace(decision, required=False),
            config=self.config,
            validated_request=validated_request,
            route=route,
            question=validated_request["normalized_question"],
            plan=plan,
            evidence_packet=packet,
            prompt=prompt,
            provider_factory=forged_skip_factory,
        )
        self.assertEqual(L2Disposition.FAILED, forged_skip.disposition)
        self.assertEqual(0, forged_skip.provider_initialization_count)
        self.assertEqual([], forged_skip_factory.initialized_models)
        self.assertEqual([], forged_skip_factory.requests)

        decision_id_factory = FakeProviderFactory()
        decision_id_failure = execute_conditional_l2(
            decision=replace(decision, decision_id="0" * 64),
            config=self.config,
            validated_request=validated_request,
            route=route,
            question=validated_request["normalized_question"],
            plan=plan,
            evidence_packet=packet,
            prompt=prompt,
            provider_factory=decision_id_factory,
        )
        self.assertEqual(L2Disposition.FAILED, decision_id_failure.disposition)
        self.assertEqual(0, decision_id_failure.provider_initialization_count)
        self.assertEqual([], decision_id_factory.initialized_models)
        self.assertEqual([], decision_id_factory.requests)

        tampered_route = deepcopy(route)
        tampered_route["query_route"] = "TAMPERED_ROUTE"
        tampered_route["canonical_digest"]["value"] = digest(
            {
                key: value
                for key, value in tampered_route.items()
                if key != "canonical_digest"
            }
        )
        route_factory = FakeProviderFactory()
        route_failure = execute_conditional_l2(
            decision=decision,
            config=self.config,
            validated_request=validated_request,
            route=tampered_route,
            question=validated_request["normalized_question"],
            plan=plan,
            evidence_packet=packet,
            prompt=prompt,
            provider_factory=route_factory,
        )
        self.assertEqual(L2Disposition.FAILED, route_failure.disposition)
        self.assertEqual(0, route_failure.provider_initialization_count)
        self.assertEqual([], route_factory.initialized_models)
        self.assertEqual([], route_factory.requests)

        valid = reason_over_packet(plan, packet)
        self.assertTrue(valid["current_state"])

        missing_mutation_count = deepcopy(valid)
        missing_mutation_count.pop("automatic_graph_mutation_count")
        missing_mutation_validation = validate_decision_model_output(
            missing_mutation_count,
            evidence_packet=packet,
            expected_query_type=plan["query_type"],
        )
        self.assertFalse(missing_mutation_validation.valid)
        self.assertTrue(missing_mutation_validation.repairable)

        invalid_mutation_count = deepcopy(valid)
        invalid_mutation_count["automatic_graph_mutation_count"] = 1
        invalid_mutation_validation = validate_decision_model_output(
            invalid_mutation_count,
            evidence_packet=packet,
            expected_query_type=plan["query_type"],
        )
        self.assertFalse(invalid_mutation_validation.valid)
        self.assertFalse(invalid_mutation_validation.repairable)

        candidate_record = packet["candidate"][0]["record"]
        candidate_proposal = deepcopy(valid)
        candidate_proposal["proposed_assertions"] = [
            {
                "proposal": f"CANDIDATE: review {candidate_record['title']}",
                "citations": [candidate_record["record_id"]],
                "evidence_ids": list(candidate_record["evidence_ids"]),
                "lane": "candidate",
                "proposal_only": True,
            }
        ]
        candidate_proposal["hypotheses"] = []
        proposal_deficiencies = evaluate_semantic_quality(
            question=validated_request["normalized_question"],
            plan=plan,
            evidence_packet=packet,
            answer=candidate_proposal,
        )
        self.assertNotIn(
            "MATERIAL_CANDIDATE_POSTURE_NOT_EXPLICIT",
            {value.code.value for value in proposal_deficiencies},
        )
        missing_proposal_only = deepcopy(candidate_proposal)
        missing_proposal_only["proposed_assertions"][0].pop("proposal_only")
        missing_proposal_validation = validate_decision_model_output(
            missing_proposal_only,
            evidence_packet=packet,
            expected_query_type=plan["query_type"],
        )
        self.assertFalse(missing_proposal_validation.valid)
        self.assertTrue(missing_proposal_validation.repairable)

        invalid_proposal_only = deepcopy(candidate_proposal)
        invalid_proposal_only["proposed_assertions"][0]["proposal_only"] = False
        invalid_proposal_validation = validate_decision_model_output(
            invalid_proposal_only,
            evidence_packet=packet,
            expected_query_type=plan["query_type"],
        )
        self.assertFalse(invalid_proposal_validation.valid)
        self.assertFalse(invalid_proposal_validation.repairable)

        evidence_support_count: dict[tuple[str, str], int] = {}
        for lane in ("approved", "candidate", "external_public"):
            for item in packet[lane]:
                for evidence_id in item["record"]["evidence_ids"]:
                    key = (lane, evidence_id)
                    evidence_support_count[key] = evidence_support_count.get(key, 0) + 1
        unambiguous_row = next(
            row
            for row in valid["current_state"]
            if row["evidence_ids"]
            and all(
                evidence_support_count[(row["lane"], evidence_id)] == 1
                for evidence_id in row["evidence_ids"]
            )
        )
        missing_citations = deepcopy(valid)
        citation_row_index = valid["current_state"].index(unambiguous_row)
        missing_citations["current_state"][citation_row_index].pop("citations")
        missing_citation_validation = validate_decision_model_output(
            missing_citations,
            evidence_packet=packet,
            expected_query_type=plan["query_type"],
        )
        self.assertFalse(missing_citation_validation.valid)
        self.assertTrue(missing_citation_validation.repairable)

        nested_scalar = deepcopy(valid)
        nested_scalar["current_state"][0]["statement"] = 7
        self.assertFalse(
            validate_decision_model_output(
                nested_scalar,
                evidence_packet=packet,
                expected_query_type=plan["query_type"],
            ).valid
        )

        empty_citations = deepcopy(valid)
        empty_citations["current_state"][0]["citations"] = []
        self.assertFalse(
            validate_decision_model_output(
                empty_citations,
                evidence_packet=packet,
                expected_query_type=plan["query_type"],
            ).valid
        )

        self.assertGreaterEqual(len(packet["approved"]), 2)
        mismatched = deepcopy(valid)
        mismatched["current_state"] = [deepcopy(valid["current_state"][0])]
        mismatched["current_state"][0]["citations"] = [
            packet["approved"][1]["record"]["record_id"]
        ]
        mismatched["pain_points"] = []
        mismatched["opportunities"] = []
        mismatched["hypotheses"] = []
        mismatched["next_best_actions"] = []
        self.assertFalse(
            validate_decision_model_output(
                mismatched,
                evidence_packet=packet,
                expected_query_type=plan["query_type"],
            ).valid
        )

        cited_records = set(valid["current_state"][0]["citations"])
        cited_evidence_ids = {
            evidence_id
            for lane in ("approved", "candidate", "external_public")
            for item in packet[lane]
            if item["record"]["record_id"] in cited_records
            for evidence_id in item["record"]["evidence_ids"]
        }
        unrelated_evidence_ids = [
            evidence_id
            for lane in ("approved", "candidate", "external_public")
            for item in packet[lane]
            if item["record"]["record_id"] not in cited_records
            for evidence_id in item["record"]["evidence_ids"]
            if evidence_id not in cited_evidence_ids
        ]
        self.assertTrue(unrelated_evidence_ids)
        unrelated_extra = deepcopy(valid)
        unrelated_extra["current_state"][0]["evidence_ids"].append(
            unrelated_evidence_ids[0]
        )
        self.assertFalse(
            validate_decision_model_output(
                unrelated_extra,
                evidence_packet=packet,
                expected_query_type=plan["query_type"],
            ).valid
        )

        self.assertTrue(packet["candidate"])
        candidate_only_action = deepcopy(valid)
        candidate_only_action["next_best_actions"] = [
            {
                "action": "Act only on candidate evidence.",
                "basis_citations": [
                    packet["candidate"][0]["record"]["record_id"]
                ],
            }
        ]
        self.assertFalse(
            validate_decision_model_output(
                candidate_only_action,
                evidence_packet=packet,
                expected_query_type=plan["query_type"],
            ).valid
        )

        patterns_only = deepcopy(valid)
        patterns_only["current_state"] = []
        patterns_only["patterns"] = ["A pattern without a governed conclusion."]
        patterns_only["pain_points"] = []
        patterns_only["opportunities"] = []
        patterns_only["hypotheses"] = []
        patterns_only["next_best_actions"] = []
        patterns_only["counterevidence"] = []
        patterns_only["proposed_assertions"] = []
        for query_type, question in (
            (
                "relationship_explanation",
                "Explain the governed Project Beacon relationship.",
            ),
            (
                "cross_workstream_synthesis",
                "Compare Project Beacon and Chakra across workstreams.",
            ),
        ):
            with self.subTest(patterns_only_query_type=query_type):
                route_plan = deepcopy(plan)
                route_plan["query_type"] = query_type
                route_answer = deepcopy(patterns_only)
                route_answer["query_type"] = query_type
                schema = validate_decision_model_output(
                    route_answer,
                    evidence_packet=packet,
                    expected_query_type=query_type,
                )
                self.assertTrue(schema.valid, schema.issues)
                deficiencies = evaluate_semantic_quality(
                    question=question,
                    plan=route_plan,
                    evidence_packet=packet,
                    answer=route_answer,
                )
                self.assertIn(
                    "MATERIAL_QUERY_COMPONENT_NOT_ANSWERED",
                    {deficiency.code.value for deficiency in deficiencies},
                )

    def test_W_schema_repair_that_invents_a_claim_fails_closed(self) -> None:
        factory = FakeProviderFactory(
            {
                CallPurpose.PRIMARY: [missing_schema_version],
                CallPurpose.SCHEMA_REPAIR: [repair_with_invented_claim],
            }
        )
        result, _ = self._ask(
            "Explain the governed Project Beacon relationship W.",
            query_type="relationship_explanation",
            factory=factory,
            materiality="high",
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("FAILED", result["l2_disposition"])
        self.assertEqual("INVALID_STRUCTURED_RESPONSE", result["provider_failure_code"])
        self.assertEqual([NORMAL_MODEL, NORMAL_MODEL], factory.called_models)
        self.assertEqual(["PRIMARY", "SCHEMA_REPAIR"], factory.call_purposes)
        self._assert_call_counts(result, primary=1, repair=1, escalation=0)

        debug_factory = FakeProviderFactory(
            {
                CallPurpose.PRIMARY: [response_with_unknown_debug_field],
                CallPurpose.SCHEMA_REPAIR: [repair_moves_debug_to_current_state],
            }
        )
        debug_result, _ = self._ask(
            "Explain the governed Project Beacon relationship W debug move.",
            query_type="relationship_explanation",
            factory=debug_factory,
            materiality="high",
        )
        self.assertEqual("BLOCKED", debug_result["status"])
        self.assertEqual("FAILED", debug_result["l2_disposition"])
        self.assertEqual(
            "INVALID_STRUCTURED_RESPONSE", debug_result["provider_failure_code"]
        )
        self.assertEqual([NORMAL_MODEL, NORMAL_MODEL], debug_factory.called_models)
        self.assertEqual(["PRIMARY", "SCHEMA_REPAIR"], debug_factory.call_purposes)
        self._assert_call_counts(debug_result, primary=1, repair=1, escalation=0)

    def test_X_high_materiality_lexicon_selects_openai_high_tier(self) -> None:
        high_questions = (
            "Recommend a budget allocation for governed Project Beacon.",
            "Recommend a team and resource allocation for governed Project Beacon.",
            "Provide a client-facing recommendation for governed Project Beacon.",
        )
        for question in high_questions:
            with self.subTest(question=question):
                factory = FakeProviderFactory()
                result, _ = self._ask(
                    question,
                    query_type="opportunity_nba",
                    factory=factory,
                )
                if result["status"] == "BLOCKED":
                    self.assertEqual(
                        "RETRIEVAL_COVERAGE_INSUFFICIENT",
                        result["terminal_outcome"],
                    )
                    self.assertEqual([], factory.called_models)
                    continue
                self.assertEqual("COMPLETED", result["status"])
                run_dir = self.runtime / "runs" / result["run_id"]
                self.assertEqual(
                    "high",
                    read_json(run_dir / "l2_execution_decision.json")["materiality"],
                )
                self.assertEqual(
                    "enhanced", result["selected_model_profile"]
                )
                self.assertEqual([HIGH_MODEL], factory.called_models)
                self._assert_call_counts(
                    result, primary=1, repair=0, escalation=0
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
