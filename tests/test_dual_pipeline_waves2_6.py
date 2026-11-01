#!/usr/bin/env python3
"""Acceptance tests for NEE-KG-DUAL-PIPELINE-01 Waves 2 through 6."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli import _graph_for_intake  # noqa: E402
from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    digest,
    exact_file_copy,
    read_json,
    seal_artifact,
    sha256_file,
)
from dual_pipeline.index import (  # noqa: E402
    HybridRetriever,
    get_backend,
    validate_sidecar,
)
from dual_pipeline.intake import IntakeEngine  # noqa: E402
from dual_pipeline.query import QUERY_STAGES, QueryEngine  # noqa: E402
from dual_pipeline.release import GovernedGraphWriter, ReleaseManager  # noqa: E402
from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator  # noqa: E402
from dual_pipeline.retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder  # noqa: E402
from dual_pipeline.telemetry import RunRecorder  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402
from tests.l2_fakes import FakeProviderFactory  # noqa: E402


class DualPipelineWaves2To6Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="nee-dual-pipeline-")
        cls.runtime = Path(cls.temp.name) / "runtime"
        cls.graph = ROOT / "graph" / "nee_graph.sqlite"
        cls.baseline_hash = sha256_file(cls.graph)
        cls.backend = BgeM3TestBackend()
        cls.copilot_fixture = (
            ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "copilot-meeting.json"
        )
        cls.copilot_exact_fixture = Path(cls.temp.name) / "copilot-exact-identity.json"
        exact_copilot = read_json(cls.copilot_fixture)
        exact_copilot["speakers"][0]["role_or_org"] = "Project Beacon workstream"
        cls.copilot_exact_fixture.write_text(
            json.dumps(seal_artifact(exact_copilot), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        cls.architecture_fixture = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "architecture-diagram.json"
        )
        cls.manager = ReleaseManager(cls.runtime, cls.graph)
        cls.bootstrap_pair = cls.manager.bootstrap(cls.backend)
        active_graph = cls.manager._resolve(cls.bootstrap_pair["graph_path"])
        active_sidecar = cls.manager._resolve(cls.bootstrap_pair["sidecar_path"])
        intake = IntakeEngine(
            active_graph,
            cls.runtime,
            retriever=HybridRetriever(active_sidecar, cls.backend),
            retrieval_audiences=[
                "internal_consulting",
                "internal_working_team",
                "partner_advisory_account_leadership",
                "nee-engineering",
            ],
        )
        cls.copilot_summary = intake.ingest(cls.copilot_exact_fixture)
        cls.architecture_summary = intake.ingest(cls.architecture_fixture)
        cls.copilot_run = cls.runtime / "runs" / cls.copilot_summary["run_id"]
        cls.architecture_run = cls.runtime / "runs" / cls.architecture_summary["run_id"]
        cls.release_receipt = cls.manager.prepare(
            run_id=cls.copilot_summary["run_id"],
            approval_id="acceptance-candidate-release-approval",
            backend=cls.backend,
        )
        cls.release_receipt_path = (
            cls.runtime
            / "releases"
            / f"release-{cls.release_receipt['next_pair']['generation_id']}.json"
        )
        cls.retrieval_release_suite = AutomaticRetrievalReleaseSuiteBuilder(
            cls.runtime, cls.graph
        ).build(cls.release_receipt_path)
        cls.retrieval_release_evaluation = RetrievalReleaseEvaluator(
            cls.runtime, cls.graph
        ).evaluate(
            cls.release_receipt_path,
            Path(cls.retrieval_release_suite["path"]),
            backend=cls.backend,
        )
        if cls.retrieval_release_evaluation["evaluation"]["status"] != "PASS":
            raise AssertionError(cls.retrieval_release_evaluation["evaluation"])
        cls.activation = cls.manager.activate(cls.release_receipt_path)
        cls.query_provider_factory = FakeProviderFactory()
        with mock.patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-only-not-a-real-key"},
        ):
            cls.query_result = QueryEngine(
                cls.runtime,
                cls.graph,
                cls.backend,
                provider_factory=cls.query_provider_factory,
            ).ask(
                "Review the source-bound Project Beacon candidate promotion proposal.",
                query_type="candidate_promotion_review",
                output_dir=cls.runtime / "query_output",
                audience="internal_consulting,nee-engineering",
            )
        cls.query_run = cls.runtime / "runs" / cls.query_result["run_id"]
        cls.rollback = cls.manager.rollback(cls.release_receipt_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    # Wave 2
    def test_01_copilot_normalization_is_source_bound_and_contract_valid(self) -> None:
        bundle = read_json(self.copilot_run / "candidate_bundle.json")
        validate_artifact(bundle)
        self.assertEqual(4, len(bundle["candidates"]))
        self.assertEqual(3, len(bundle["evidence"]))
        evidence_ids = {value["evidence_id"] for value in bundle["evidence"]}
        for candidate in bundle["candidates"]:
            self.assertTrue(set(candidate["evidence_ids"]) <= evidence_ids)
            self.assertTrue(candidate["source_locator"]["value"])

    def test_02_architecture_normalization_preserves_regions_and_ambiguities(self) -> None:
        bundle = read_json(self.architecture_run / "candidate_bundle.json")
        validate_artifact(bundle)
        self.assertEqual(4, len(bundle["candidates"]))
        self.assertTrue(all(value["source_locator"]["kind"] == "region" for value in bundle["candidates"]))
        questions = [value for value in bundle["candidates"] if value["candidate_type"] == "open_question"]
        self.assertEqual(1, len(questions))
        dispositions = read_json(self.architecture_run / "assertion_dispositions.json")
        self.assertTrue(all(value["disposition"] == "QUARANTINE" for value in dispositions))

    def test_03_every_entity_mention_has_one_resolution(self) -> None:
        for run_dir in (self.copilot_run, self.architecture_run):
            bundle = read_json(run_dir / "candidate_bundle.json")
            resolutions = read_json(run_dir / "entity_resolutions.json")
            mention_ids = [
                mention["mention_id"]
                for candidate in bundle["candidates"]
                for mention in candidate["entity_mentions"]
            ]
            resolution_ids = [value["mention_id"] for value in resolutions]
            self.assertEqual(sorted(mention_ids), sorted(resolution_ids))
            self.assertEqual(len(resolution_ids), len(set(resolution_ids)))

    def test_04_entity_resolution_golden_cases_avoid_naked_fuzzy_links(self) -> None:
        resolutions = read_json(self.copilot_run / "entity_resolutions.json")
        sherpa = [value for value in resolutions if value["reported_name"] == "Project Beacon"]
        matt = [value for value in resolutions if value["reported_name"] == "Matt K"]
        self.assertTrue(sherpa)
        self.assertTrue(all(value["outcome"] == "AUTO_LINK_EXACT" for value in sherpa))
        self.assertTrue(all(value["selected_entity_id"] == "workstream_project_beacon" for value in sherpa))
        self.assertTrue(all(value["alternatives"][0]["supporting_context"] for value in sherpa))
        self.assertTrue(matt)
        self.assertTrue(all(value["outcome"] == "HITL_CONFIRM_ENTITY" for value in matt))
        self.assertTrue(all(value["selected_entity_id"] is None for value in matt))

    def test_04a_unresolved_identity_reports_confidence_and_label_options(self) -> None:
        resolutions = read_json(self.copilot_run / "entity_resolutions.json")
        matt = next(value for value in resolutions if value["reported_name"] == "Matt K")
        review = matt["extensions"]["com.partner_advisory.nee.identity-review"]
        confidence = review["confidence"]
        self.assertEqual("possible_duplicate_evidence", confidence["band"])
        self.assertEqual(0.45, confidence["thresholds"]["minimum_ranked_candidate_score"])
        self.assertEqual(
            "possible_duplicate_evidence_only", confidence["similarity_authority"]
        )
        self.assertTrue(confidence["reasons"])
        options = review["label_options"]
        self.assertTrue(any(value["decision"] == "CONFIRM" for value in options))
        self.assertFalse(
            next(value for value in options if value["decision"] == "CREATE_NEW")[
                "available"
            ]
        )
        self.assertTrue(any(value["decision"] == "HOLD" for value in options))

        bundle = read_json(self.copilot_run / "candidate_bundle.json")
        matt_candidate = next(
            candidate
            for candidate in bundle["candidates"]
            if any(mention["reported_text"] == "Matt K" for mention in candidate["entity_mentions"])
        )
        dispositions = read_json(self.copilot_run / "assertion_dispositions.json")
        disposition = next(
            value
            for value in dispositions
            if value["candidate_id"] == matt_candidate["candidate_id"]
        )
        admission = disposition["extensions"]["com.partner_advisory.nee.graph-admission-decision"]
        self.assertEqual("QUARANTINE", admission["current_disposition"])
        self.assertFalse(admission["current_graph_write_allowed"])
        self.assertFalse(admission["decision_inputs"]["identity_resolved"])
        self.assertTrue(admission["next_actions"])

        hitl = read_json(self.copilot_run / "hitl_batches.json")
        reported = [
            entity
            for batch in hitl["review_batches"]
            for item in batch["items"]
            for entity in item["reported_entities"]
            if entity["reported_name"] == "Matt K"
        ]
        self.assertTrue(reported)
        self.assertEqual(review, reported[0]["identity_review"])

        ledger_path = self.copilot_run / "ingestion_decision_ledger.json"
        english_path = self.copilot_run / "ingestion_decision_ledger.english.json"
        table_path = self.copilot_run / "ingestion_decision_ledger.md"
        self.assertTrue(ledger_path.is_file())
        self.assertTrue(english_path.is_file())
        self.assertTrue(table_path.is_file())
        ledger = read_json(ledger_path)
        self.assertEqual("ingestion_decision_ledger", ledger["ledger_type"])
        self.assertEqual(self.copilot_summary["candidate_count"], ledger["candidate_count"])
        matt_row = next(
            row
            for row in ledger["rows"]
            if row["candidate_id"] == matt_candidate["candidate_id"]
        )
        self.assertEqual("BLOCKED", matt_row["graph_admission"]["gate_checks"]["identity"])
        self.assertEqual([], matt_row["planned_graph_operations"])
        english = read_json(english_path)
        validate_artifact(english)
        self.assertEqual("derived_explanation_only", english["authority"])
        self.assertFalse(english["mutation_authority"])
        table = table_path.read_text(encoding="utf-8")
        self.assertIn("| Source decision | What was recognized |", table)
        self.assertIn("Matt K", table)
        self.assertIn("possible duplicate evidence", table)
        self.assertEqual(
            sha256_file(table_path),
            self.copilot_summary["ingestion_decision_table_sha256"],
        )
        self.assertEqual(
            sha256_file(ledger_path),
            self.copilot_summary["ingestion_decision_ledger_sha256"],
        )

    def test_05_wave2_does_not_mutate_the_seed_graph(self) -> None:
        self.assertEqual(self.baseline_hash, sha256_file(self.graph))
        self.assertEqual(self.baseline_hash, self.copilot_summary["graph_sha256_before"])
        self.assertEqual(self.baseline_hash, self.copilot_summary["graph_sha256_after"])
        comparisons = read_json(self.copilot_run / "candidate_comparison.json")[
            "comparisons"
        ].values()
        hybrid = [
            value
            for value in comparisons
            if value["comparison_mode"] == "dense_sparse_colbert"
        ]
        self.assertTrue(hybrid)
        self.assertTrue(all(value["transient_only"] for value in hybrid))
        self.assertTrue(all(value["persistent_write_count"] == 0 for value in hybrid))
        # The fixture is nee-engineering-only while the seed corpus has no
        # assertions admitted to that audience.  Token-wide audiences must not
        # let internal_consulting records influence an nee-engineering-labeled write.
        self.assertTrue(all(not value["semantic_matches"] for value in hybrid))

    # Wave 3
    def test_06_ingestion_plan_and_routes_are_complete_contract_artifacts(self) -> None:
        bundle = read_json(self.copilot_run / "candidate_bundle.json")
        plan = read_json(self.copilot_run / "ingestion_plan.json")
        routes = read_json(self.copilot_run / "route_decisions.json")
        validate_artifact(plan)
        for value in routes:
            validate_artifact(value)
        candidate_ids = {value["candidate_id"] for value in bundle["candidates"]}
        self.assertEqual(candidate_ids, {value["candidate_id"] for value in plan["candidates"]})
        self.assertEqual(candidate_ids, {value["subject_id"] for value in routes})

    def test_07_every_candidate_has_one_governed_disposition(self) -> None:
        bundle = read_json(self.copilot_run / "candidate_bundle.json")
        dispositions = read_json(self.copilot_run / "assertion_dispositions.json")
        for value in dispositions:
            validate_artifact(value)
        candidate_ids = [value["candidate_id"] for value in bundle["candidates"]]
        disposition_ids = [value["candidate_id"] for value in dispositions]
        self.assertEqual(sorted(candidate_ids), sorted(disposition_ids))
        self.assertEqual(len(disposition_ids), len(set(disposition_ids)))
        self.assertEqual({"CANDIDATE", "QUARANTINE"}, {value["disposition"] for value in dispositions})

    def test_08_hitl_is_risk_batched_not_prompted_for_every_candidate(self) -> None:
        hitl = read_json(self.copilot_run / "hitl_batches.json")
        self.assertGreater(hitl["tier_0_count"], 0)
        self.assertGreater(hitl["tier_2_count"], 0)
        review_item_count = sum(len(batch["items"]) for batch in hitl["review_batches"])
        self.assertLess(review_item_count, self.copilot_summary["candidate_count"])

    def test_09_candidate_lane_and_quarantine_postures_do_not_contaminate(self) -> None:
        dispositions = read_json(self.copilot_run / "assertion_dispositions.json")
        for value in dispositions:
            if value["disposition"] == "CANDIDATE":
                self.assertEqual("candidate", value["persistent_lane"])
                self.assertEqual("X3D_ALLOW_CANDIDATE_STAGE", value["x3_outcome"])
            if value["disposition"] == "QUARANTINE":
                self.assertEqual("none", value["persistent_lane"])
                self.assertEqual("X3Q_QUARANTINE", value["x3_outcome"])

    def test_10_change_set_is_proposal_only_until_release_approval(self) -> None:
        proposed = read_json(self.copilot_run / "graph_change_set.json")
        validate_artifact(proposed)
        self.assertEqual("proposed", proposed["status"])
        self.assertIsNone(proposed["approval_id"])
        self.assertEqual(2, len(proposed["operations"]))
        self.assertEqual(0, self.copilot_summary["graph_mutation_count"])
        with tempfile.TemporaryDirectory(prefix="nee-stale-review-") as directory:
            changed_graph = Path(directory) / "changed.sqlite"
            exact_file_copy(self.graph, changed_graph)
            connection = sqlite3.connect(changed_graph)
            try:
                connection.execute("CREATE TABLE acceptance_hash_change(value TEXT)")
                connection.commit()
            finally:
                connection.close()
            decisions = Path(directory) / "decisions.json"
            decisions.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "active graph changed"):
                IntakeEngine(changed_graph, self.runtime).review(
                    self.copilot_summary["run_id"], decisions
                )
        hitl = read_json(self.copilot_run / "hitl_batches.json")
        emitted_item = hitl["review_batches"][0]["items"][0]
        forged = read_json(
            ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "hitl-decision.json"
        )
        forged.update(
            {
                "review_item_id": "review-item-forged-binding",
                "run_id": self.copilot_summary["run_id"],
                "trace_id": self.copilot_summary["trace_id"],
                "tier": emitted_item["tier"],
                "evidence_reviewed": emitted_item["evidence_ids"],
                "before_state_digest": emitted_item["before_state_digest"],
                "proposed_after_state_digest": emitted_item["proposed_after_state_digest"],
                "affected_candidate_ids": emitted_item["candidate_ids"],
            }
        )
        forged = seal_artifact(forged)
        with tempfile.TemporaryDirectory(prefix="nee-forged-hitl-") as directory:
            decisions = Path(directory) / "decisions.json"
            decisions.write_text(json.dumps([forged]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "emitted review item"):
                IntakeEngine(self.graph, self.runtime).review(
                    self.copilot_summary["run_id"], decisions
                )

    # Wave 4
    def test_11_graph_write_and_sidecar_are_bound_to_one_new_snapshot(self) -> None:
        receipt = self.release_receipt
        self.assertEqual("PREPARED", receipt["status"])
        self.assertEqual(2, receipt["graph_write_receipt"]["operation_count"])
        self.assertEqual(
            receipt["next_pair"]["graph_sha256"],
            receipt["embedding_generation_receipt"]["graph_sha256"],
        )
        self.assertEqual(
            receipt["next_pair"]["graph_snapshot"],
            receipt["embedding_generation_receipt"]["graph_snapshot"],
        )
        self.assertFalse(receipt["production_qualified"])
        tampered_pair = dict(receipt["next_pair"])
        tampered_pair["model_revision"] = "tampered-revision"
        with self.assertRaisesRegex(
            RuntimeError, "immutable BGE-M3 model revision|model/backend binding mismatch"
        ):
            self.manager.validate_pair(tampered_pair)

    def test_12_sidecar_has_complete_dense_sparse_colbert_cardinality(self) -> None:
        pair = self.release_receipt["next_pair"]
        sidecar = self.runtime / pair["sidecar_path"]
        validation = validate_sidecar(
            sidecar,
            expected_generation_id=pair["generation_id"],
            expected_graph_snapshot=pair["graph_snapshot"],
            expected_graph_sha256=pair["graph_sha256"],
        )
        counts = validation["counts"]
        self.assertEqual(counts["records"], counts["dense"])
        self.assertEqual(counts["records"], counts["sparse_records"])
        self.assertEqual(counts["records"], counts["colbert_records"])
        self.assertEqual(
            str(self.backend.colbert_dimensions),
            validation["metadata"]["colbert_dimensions"],
        )
        self.assertGreater(self.backend.colbert_dimensions, 0)
        self.assertEqual(0, validation["lane_contamination_count"])

    def test_13_activation_and_rollback_receipts_restore_prior_pair(self) -> None:
        self.assertEqual("ACTIVE", self.activation["status"])
        self.assertEqual(
            self.release_receipt["next_pair"]["generation_id"],
            self.activation["active_pair"]["generation_id"],
        )
        self.assertEqual("ROLLED_BACK", self.rollback["status"])
        self.assertEqual(
            self.bootstrap_pair["generation_id"], self.manager.active_pair()["generation_id"]
        )

    def test_14_injected_mid_transaction_failure_leaves_no_partial_assertions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-writer-fault-") as directory:
            graph_copy = Path(directory) / "graph.sqlite"
            exact_file_copy(self.graph, graph_copy)
            approved = read_json(self.copilot_run / "graph_change_set_approved.json")
            target_ids = [value["target_id"] for value in approved["operations"]]
            with self.assertRaisesRegex(RuntimeError, "injected graph-writer failure"):
                GovernedGraphWriter(graph_copy).apply(approved, fail_after_operations=1)
            connection = sqlite3.connect(graph_copy)
            try:
                placeholders = ",".join("?" for _ in target_ids)
                count = connection.execute(
                    f"SELECT count(*) FROM assertion WHERE assertion_id IN ({placeholders})",
                    target_ids,
                ).fetchone()[0]
                self.assertEqual(0, count)
                self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                connection.close()
            tampered = json.loads(json.dumps(approved))
            tampered["operations"][0]["classification"] = "public"
            tampered = seal_artifact(tampered)
            with self.assertRaisesRegex(RuntimeError, "security or evidence binding mismatch"):
                GovernedGraphWriter(graph_copy).apply(tampered)

    def test_15_seed_graph_remains_byte_identical_after_release_lifecycle(self) -> None:
        self.assertEqual(self.baseline_hash, sha256_file(self.graph))

    # Wave 5
    def test_16_complex_query_emits_valid_plan_and_explicit_route(self) -> None:
        plan = read_json(self.query_run / "query_plan.json")
        route = read_json(self.query_run / "route_decision.json")
        validate_artifact(plan)
        validate_artifact(route)
        self.assertEqual("candidate_promotion_review", plan["query_type"])
        self.assertEqual("candidate_review", plan["retrieval_mode"])
        self.assertEqual(
            ["approved", "candidate"],
            plan["allowed_lanes"],
        )
        self.assertFalse(plan["retrieval"]["lane_blending"])
        self.assertEqual("CANDIDATE_PROMOTION_REVIEW", route["query_route"])

    def test_17_hybrid_retrieval_executes_all_three_representations_per_lane(self) -> None:
        retrieval = read_json(self.query_run / "hybrid_retrieval.json")
        self.assertFalse(retrieval["lane_blending"])
        self.assertEqual("candidate_review", retrieval["retrieval_mode"])
        self.assertEqual(
            {"approved", "candidate"},
            set(retrieval["lanes"]),
        )
        for value in retrieval["lanes"].values():
            if value["authorized_record_count"] == 0:
                self.assertEqual(0, value["dense_candidate_count"])
                self.assertEqual(0, value["sparse_candidate_count"])
                self.assertEqual(0, value["colbert_candidate_count"])
                self.assertEqual([], value["results"])
            else:
                self.assertGreater(value["dense_candidate_count"], 0)
                # Sparse/BM25 executes for every authorized lane, but a semantic
                # query is permitted to have no lexical term overlap in one lane.
                self.assertTrue(value["bm25_executed"])
                self.assertGreater(value["colbert_candidate_count"], 0)
                self.assertTrue(value["results"])

    def test_18_retrieved_records_hydrate_to_graph_paths_and_evidence(self) -> None:
        packet = read_json(self.query_run / "evidence_packet.json")
        connected_map = read_json(
            self.query_run / "connected_evidence_map.json"
        )
        validate_artifact(connected_map)
        self.assertTrue(packet["graph_paths"])
        record_ids = {
            item["record"]["record_id"]
            for lane in ("approved", "candidate", "external_public")
            for item in packet[lane]
        }
        path_ids = {value["record_id"] for value in packet["graph_paths"]}
        self.assertEqual(record_ids, path_ids)
        self.assertTrue(all(value["evidence_ids"] for value in packet["graph_paths"]))
        self.assertGreater(
            connected_map["metrics"]["navigation_record_suppression_count"],
            0,
        )
        released_graph = self.runtime / self.release_receipt["next_pair"]["graph_path"]
        connection = sqlite3.connect(released_graph)
        connection.row_factory = sqlite3.Row
        try:
            for path in connected_map["paths"]:
                row = connection.execute(
                    """SELECT a.predicate,subject.node_type AS subject_type,
                              object.node_type AS object_type
                         FROM assertion a
                         LEFT JOIN node subject
                           ON subject.node_id=a.subject_node_id
                         LEFT JOIN node object
                           ON object.node_id=a.object_node_id
                        WHERE a.assertion_id=?""",
                    (path["target_assertion_id"],),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertNotEqual("ROUTES_TO", row["predicate"])
                self.assertNotEqual("card", row["subject_type"])
                self.assertNotEqual("card", row["object_type"])
        finally:
            connection.close()

    def test_19_prompt_has_ten_slots_and_replayable_hashes(self) -> None:
        prompt = read_json(self.query_run / "prompt_assembly.json")
        self.assertEqual(
            {
                "S0",
                "D0",
                "I0",
                "C0A",
                "C0C",
                "C0W",
                "C0M",
                "G0",
                "U0",
                "R0",
            },
            set(prompt["slots"]),
        )
        self.assertEqual(
            {name: digest(value) for name, value in prompt["slots"].items()},
            prompt["slot_digests"],
        )
        self.assertEqual(
            prompt["prompt_hash"],
            digest({key: value for key, value in prompt.items() if key != "prompt_hash"}),
        )

    def test_20_answer_citations_resolve_and_candidate_hypotheses_are_labeled(self) -> None:
        gates = read_json(self.query_run / "x_gates.json")
        answer = self.query_result["answer"]
        self.assertEqual("PASS", gates["X1"]["status"])
        self.assertTrue(gates["X1"]["checks"]["citations_resolve"])
        self.assertTrue(gates["X1"]["checks"]["candidate_labels_preserved"])
        self.assertTrue(gates["X1"]["checks"]["guidance_labels_preserved"])
        self.assertTrue(gates["X1"]["checks"]["factual_claims_use_factual_records"])
        self.assertTrue(
            all(row["label"] == "GUIDANCE_NOT_FACT" for row in answer["guidance"])
        )
        self.assertTrue(all(value["hypothesis"].startswith("CANDIDATE:") for value in answer["hypotheses"]))

    def test_21_query_never_mutates_graph_truth(self) -> None:
        self.assertEqual(self.query_result["graph_sha256_before"], self.query_result["graph_sha256_after"])
        self.assertEqual(0, self.query_result["graph_mutation_count"])

    def test_22_query_learning_ledger_reconstructs_every_required_stage(self) -> None:
        first = read_json(sorted((self.query_run / "learning_events").glob("*.json"))[0])
        audit = RunRecorder(
            self.runtime,
            run_id=self.query_result["run_id"],
            trace_id=first["trace_id"],
            use_case="query",
            graph_snapshot=first["graph_snapshot"],
            model_or_index_generation=first["model_or_index_generation"],
        ).audit(QUERY_STAGES)
        self.assertEqual("PASS", audit["status"])
        self.assertFalse(audit["missing_stages"])
        self.assertEqual(0, audit["automatic_policy_change_count"])

    def test_23_otel_attributes_exclude_sensitive_raw_content(self) -> None:
        forbidden = re.compile(
            r"(?:raw|content|text|question|prompt|answer|claim|statement|transcript|quote|email|name|attendee|owner)",
            re.I,
        )
        paths = list((self.runtime / "runs").glob("*/otel/*.json"))
        self.assertTrue(paths)
        for path in paths:
            event = read_json(path)
            validate_artifact(event)
            self.assertFalse([key for key in event["attributes"] if forbidden.search(key)])

    # Wave 6
    def test_24_both_use_cases_complete_with_auditable_outcomes(self) -> None:
        self.assertEqual("COMPLETED", self.copilot_summary["status"])
        self.assertEqual("COMPLETED", self.query_result["status"])
        self.assertEqual("X3D_ALLOW_FINISH", self.query_result["x3_outcome"])
        self.assertGreater(self.copilot_summary["learning_event_count"], 0)
        self.assertGreater(self.query_result["learning_event_count"], 0)

    def test_25_operator_cli_validates_the_active_pair(self) -> None:
        active = self.manager.active_pair()
        self.assertEqual(
            (self.runtime / active["graph_path"]).resolve(),
            _graph_for_intake(self.runtime, self.graph),
        )
        self.assertNotEqual(self.graph.resolve(), _graph_for_intake(self.runtime, self.graph))
        released_graph = self.runtime / self.release_receipt["next_pair"]["graph_path"]
        reprocessed = IntakeEngine(released_graph, self.runtime).ingest(self.copilot_fixture)
        self.assertNotEqual(self.copilot_summary["run_id"], reprocessed["run_id"])
        self.assertEqual(
            self.release_receipt["next_pair"]["graph_sha256"],
            reprocessed["graph_sha256_before"],
        )
        self.assertEqual(0, reprocessed["graph_operation_count"])
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "nee.py"),
                "validate-runtime",
                "--runtime-root",
                str(self.runtime),
                "--graph",
                str(self.graph),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual("PASS", value["validation"]["status"])

    def test_26_production_backend_boundary_is_explicit(self) -> None:
        self.assertEqual("flagembedding-bge-m3-dev", self.query_result["embedding_backend"])
        self.assertFalse(self.query_result["production_qualified"])
        self.assertFalse(self.release_receipt["production_qualified"])
        with self.assertRaisesRegex(ValueError, "unsupported embedding backend"):
            get_backend("bge-m3-unsupported")
        with self.assertRaisesRegex(RuntimeError, "locally provisioned pinned model"):
            get_backend(
                "bge-m3",
                model_path=str(self.runtime / "missing-bge-m3"),
                model_revision="immutable-test-revision",
            )
        access_denied = QueryEngine(self.runtime, self.graph, self.backend).ask(
            "What are the strongest PDLC opportunities across NEA?",
            query_type="opportunity_nba",
            user_id="audience-boundary-test",
            classification="public",
            audience="not-an-authorized-record-audience",
        )
        self.assertEqual("BLOCKED", access_denied["status"])
        self.assertEqual(0, access_denied["approved_record_count"])
        self.assertEqual(0, access_denied["candidate_record_count"])
        denied_exact = read_json(
            self.runtime / "runs" / access_denied["run_id"] / "exact_retrieval.json"
        )
        self.assertEqual(0, denied_exact["anchor_count"])
        self.assertEqual([], denied_exact["entity_matches"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
