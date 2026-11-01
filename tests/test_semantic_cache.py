#!/usr/bin/env python3
"""Focused semantic-cache lifecycle and both-pipeline integration coverage."""
from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from shutil import copytree

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import digest, read_json, write_json
from dual_pipeline.index import HybridRetriever
from dual_pipeline.intake import IntakeEngine
from dual_pipeline.query import QueryEngine, _cached_query_output
from dual_pipeline.release import ReleaseManager
from dual_pipeline.runtime_evidence import discover_inventory
from dual_pipeline.semantic_cache import (
    SemanticCache,
    SemanticCacheLookup,
    configure_semantic_cache,
)
from dual_pipeline.telemetry import RunRecorder
from tests.embedding_fixture import BgeM3TestBackend, MetadataOnlyBgeM3Backend

ROOT = Path(__file__).resolve().parents[1]
AUDIENCE = "internal_consulting,nee-engineering"
CORRELATION = {
    "query_id": "cache-test",
    "source_id": None,
    "candidate_id": None,
    "graph_operation_id": None,
    "hitl_item_id": None,
}


class CountingBgeM3Backend(BgeM3TestBackend):
    """Real BGE-M3 encoder with call accounting; never substitutes vectors."""

    def __init__(self) -> None:
        self.encode_count = 0
        self.encode_dense_only_count = 0

    def encode(self, text: str):
        self.encode_count += 1
        return super().encode(text)

    def encode_dense_only(self, text: str):
        self.encode_dense_only_count += 1
        return super().encode_dense_only(text)


class SemanticCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Every direct cache exercise uses an actual pinned BGE-M3 vector.
        cls.real_intent_vector = BgeM3TestBackend().encode(
            "Project Beacon semantic cache real BGE-M3 test intent"
        )["dense"]
        # Index the immutable seed once with the real encoder, then copy that
        # verified BGE-M3 generation into each isolated cache test runtime.
        # Rebuilding the same index per case was needlessly expensive and did
        # not increase test coverage.
        cls.template = tempfile.TemporaryDirectory(prefix="nee-bge-cache-template-")
        cls.template_runtime = Path(cls.template.name) / "runtime"
        template_manager = ReleaseManager(
            cls.template_runtime, ROOT / "graph" / "nee_graph.sqlite"
        )
        template_manager.bootstrap(BgeM3TestBackend())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.template.cleanup()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-semantic-cache-")
        self.runtime = Path(self.temp.name) / "runtime"
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        copytree(self.template_runtime, self.runtime)
        self.backend = CountingBgeM3Backend()
        self.manager = ReleaseManager(self.runtime, self.graph)
        self.active = self.manager.active_pair()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _intake_engine(self) -> IntakeEngine:
        return IntakeEngine(
            self.manager._resolve(self.active["graph_path"]),
            self.runtime,
            retriever=HybridRetriever(
                self.manager._resolve(self.active["sidecar_path"]), self.backend
            ),
            retrieval_audiences=["internal_consulting", "nee-engineering"],
            active_pair=self.active,
        )

    def _bound_cache(self) -> SemanticCache:
        cache = SemanticCache(self.runtime)
        binding = cache.bind_embedding_backend(self.backend)
        self.assertIn(binding["status"], {"BOUND", "REBUILT"})
        self.assertEqual("real-bge-m3", self.backend.retrieval_encoder)
        return cache

    def test_runtime_initializes_write_then_uses_one_persisted_mode(self) -> None:
        cache = SemanticCache(self.runtime)
        self.assertEqual("write", cache.mode)
        self.assertEqual("initialized", cache.config_source)
        config = read_json(self.runtime / "config" / "semantic_cache.json")
        self.assertEqual("write", config["mode"])

        configure_semantic_cache(self.runtime, "read_write")
        reopened = SemanticCache(self.runtime)
        self.assertEqual("read_write", reopened.mode)
        self.assertEqual("runtime_config", reopened.config_source)
        self.assertEqual(reopened.policy_digest, config["policy_digest"])

    def test_pipeline_mode_argument_is_an_assertion_not_a_configuration(self) -> None:
        configure_semantic_cache(self.runtime, "write")
        with self.assertRaisesRegex(
            ValueError,
            "SEMANTIC_CACHE_MODE_MISMATCH",
        ):
            QueryEngine(
                self.runtime,
                self.graph,
                self.backend,
                semantic_cache_mode="read_write",
            ).ask(
                "Project Beacon cache mode assertion",
                query_type="exact_lookup",
                audience=AUDIENCE,
            )
        self.assertEqual("write", SemanticCache(self.runtime).mode)

    def test_sealed_prefix_survives_later_review_events_and_reseal(self) -> None:
        configure_semantic_cache(self.runtime, "read_write")
        cache = self._bound_cache()
        recorder = RunRecorder(
            self.runtime,
            run_id="source-run",
            trace_id="0" * 32,
            use_case="query",
            graph_snapshot="snapshot",
        )
        recorder.record(
            stage="L0",
            event_type="route_decision",
            payload={"route": "test"},
            correlation=CORRELATION,
            route="test",
        )
        staged = cache.stage(
            context={"use_case": "query", "binding": "test"},
            intent_digest=digest("intent"),
            intent_vector=list(self.real_intent_vector),
            output={"value": "sealed"},
            outcome="X3D_ALLOW_FINISH",
            source_run_id="source-run",
            source_trace_id="0" * 32,
            replay_eligible=True,
        )
        self.assertIsNotNone(staged.entry)
        assert staged.entry is not None
        recorder.record(
            stage="L4",
            event_type="pipeline_event",
            payload={"entry_id": staged.entry.entry_id},
            correlation=CORRELATION,
        )
        seal = recorder.seal(["L0", "L4"], CORRELATION)
        activated = cache.activate(
            staged.entry,
            source_event_count=seal["event_count"],
            source_event_chain_digest=seal["event_chain_digest"],
        )
        self.assertEqual("ACTIVE", activated.status)

        recorder.record(
            stage="X2",
            event_type="pipeline_event",
            payload={"review": "later"},
            correlation=CORRELATION,
        )
        recorder.seal(["L0", "X2", "L4"], CORRELATION)
        lookup = cache.lookup(
            context={"use_case": "query", "binding": "test"},
            intent_vector=list(self.real_intent_vector),
        )
        self.assertTrue(lookup.hit)

    def test_cache_storage_errors_are_nonblocking_results(self) -> None:
        configure_semantic_cache(self.runtime, "read_write")
        cache = self._bound_cache()
        cache.cache_root.mkdir(parents=True, exist_ok=True)
        cache.path.unlink()
        cache.path.mkdir()
        lookup = cache.lookup(
            context={"use_case": "query"},
            intent_vector=list(self.real_intent_vector),
        )
        self.assertFalse(lookup.hit)
        self.assertTrue(lookup.reason.startswith("CACHE_LOOKUP_ERROR:"))
        staged = cache.stage(
            context={"use_case": "query"},
            intent_digest=digest("intent"),
            intent_vector=list(self.real_intent_vector),
            output={"value": "answer"},
            outcome="X3D_ALLOW_FINISH",
            source_run_id="run",
            source_trace_id="trace",
            replay_eligible=True,
        )
        self.assertEqual("ERROR", staged.status)

    def test_query_namespace_isolated_by_route_assurance_delivery_and_acl(self) -> None:
        cache = SemanticCache(self.runtime)
        base = {
            "use_case": "query", "cache_payload_kind": "sealed_query_output_v4",
            "destination": "NEE_DECISION_INTELLIGENCE",
            "eligibility_policy_digest": "1" * 64,
            "assurance_profile": "standard", "delivery_profile": "internal_technical",
            "artifact_type": "answer", "classification": "internal",
            "audiences": ["nee-engineering"], "allowed_lanes": ["approved"],
            "active_pair": {"generation_id": "generation-a"},
            "coverage_requirements_digest": "2" * 64,
            "l2_policy_digest": "3" * 64, "x1_policy_digest": "4" * 64,
            "provider_egress_allowed": True,
            "query_plan_version": "2.0.0",
            "l1_controller_mode": "enforced",
            "l1_controller_policy_digest": "5" * 64,
            "l1_action_policy_digest": "6" * 64,
            "l1_budget_profile": "standard",
        }
        baseline = digest(cache.compatibility(base))
        variants = {
            "destination": "GRAPH_EVIDENCE_UTILITY",
            "assurance_profile": "enhanced",
            "delivery_profile": "client_executive",
            "artifact_type": "architecture_deck",
            "classification": "confidential",
            "audiences": ["client-team"],
            "allowed_lanes": ["approved", "candidate"],
            "active_pair": {"generation_id": "generation-b"},
            "coverage_requirements_digest": "5" * 64,
            "x1_policy_digest": "6" * 64,
            "l2_policy_digest": "7" * 64,
            "provider_egress_allowed": False,
            "query_plan_version": "3.0.0",
            "l1_controller_mode": "shadow",
            "l1_controller_policy_digest": "8" * 64,
            "l1_action_policy_digest": "9" * 64,
            "l1_budget_profile": "constrained",
        }
        for field, value in variants.items():
            with self.subTest(field=field):
                changed = dict(base)
                changed[field] = value
                self.assertNotEqual(baseline, digest(cache.compatibility(changed)))

    def test_legacy_or_wrongly_bound_vectors_are_purged_before_reuse(self) -> None:
        configure_semantic_cache(self.runtime, "read_write")
        cache = self._bound_cache()
        staged = cache.stage(
            context={"use_case": "query", "binding": "legacy"},
            intent_digest=digest("legacy-intent"),
            intent_vector=list(self.real_intent_vector),
            output={"value": "legacy"},
            outcome="X3D_ALLOW_FINISH",
            source_run_id="legacy-run",
            source_trace_id="0" * 32,
            replay_eligible=True,
        )
        self.assertEqual("PENDING", staged.status)
        connection = cache._connection()
        try:
            connection.execute("DELETE FROM semantic_cache_embedding_binding")
            connection.commit()
        finally:
            connection.close()

        rebuilt = SemanticCache(self.runtime)
        result = rebuilt.bind_embedding_backend(self.backend)
        self.assertEqual("REBUILT", result["status"])
        self.assertEqual(1, result["purged_entry_count"])
        self.assertEqual(
            {"PENDING": 0, "ACTIVE": 0},
            rebuilt.status()["entry_counts"],
        )

    def test_metadata_only_double_cannot_open_or_write_the_cache(self) -> None:
        configure_semantic_cache(self.runtime, "read_write")
        cache = SemanticCache(self.runtime)
        result = cache.bind_embedding_backend(MetadataOnlyBgeM3Backend())
        self.assertEqual("REJECTED", result["status"])
        staged = cache.stage(
            context={"use_case": "query"},
            intent_digest=digest("metadata-only-intent"),
            intent_vector=list(self.real_intent_vector),
            output={"value": "answer"},
            outcome="X3D_ALLOW_FINISH",
            source_run_id="run",
            source_trace_id="trace",
            replay_eligible=True,
        )
        self.assertEqual("SKIPPED", staged.status)
        self.assertEqual("CACHE_BINDING_REJECTED_NON_BGE_ENCODER", staged.reason)

    def test_query_populates_then_replays_and_reuses_real_bge_embedding(self) -> None:
        configure_semantic_cache(self.runtime, "write")
        self.backend.encode_count = 0
        source = QueryEngine(self.runtime, self.graph, self.backend).ask(
            "Project Beacon governed record semantic cache source",
            query_type="exact_lookup",
            audience=AUDIENCE,
        )
        self.assertEqual("COMPLETED", source["status"])
        self.assertIsNotNone(source["semantic_cache"]["writeback"]["entry_id"])
        self.assertFalse(source["semantic_cache"]["lookup_hit"])
        self.assertEqual(1, self.backend.encode_count)
        self.assertTrue(Path(source["retrieval_evidence"]["report_json_path"]).is_file())
        self.assertTrue(
            Path(source["retrieval_evidence"]["report_markdown_path"]).is_file()
        )

        configure_semantic_cache(self.runtime, "read_write")
        before = self.backend.encode_count
        before_dense = self.backend.encode_dense_only_count
        replay = QueryEngine(self.runtime, self.graph, self.backend).ask(
            "Project Beacon governed records semantic cache source",
            query_type="exact_lookup",
            audience=AUDIENCE,
        )
        self.assertEqual("COMPLETED", replay["status"])
        self.assertTrue(
            replay["semantic_cache"]["lookup_hit"],
            replay["semantic_cache"],
        )
        self.assertTrue(replay["semantic_cache"]["replayed_output"])
        self.assertEqual(
            source["run_id"],
            replay["semantic_cache"]["source_run_id"],
        )
        self.assertEqual(
            source["semantic_cache"]["writeback"]["entry_id"],
            replay["semantic_cache"]["lookup_entry_id"],
        )
        self.assertEqual(0, replay["external_l2_call_count"])
        self.assertEqual(0, self.backend.encode_count - before)
        self.assertEqual(1, self.backend.encode_dense_only_count - before_dense)
        self.assertEqual(source["answer"], replay["answer"])
        self.assertEqual(
            digest(source["answer"]),
            replay["semantic_cache"]["source_answer_digest"],
        )
        self.assertEqual(
            digest(replay["answer"]),
            replay["semantic_cache"]["delivered_answer_digest"],
        )
        self.assertNotEqual(
            source["query_coverage_manifest_digest"],
            replay["query_coverage_manifest_digest"],
        )
        self.assertNotEqual(
            source["retrieval_evidence_report_digest"],
            replay["retrieval_evidence_report_digest"],
        )
        self.assertNotEqual(
            source["claim_evidence_map_digest"],
            replay["claim_evidence_map_digest"],
        )

        replay_dir = self.runtime / "runs" / replay["run_id"]
        for name in (
            "query_coverage_manifest.json",
            "connected_evidence_map.json",
            "retrieval_evidence_report.json",
            "claim_evidence_map.json",
        ):
            artifact = read_json(replay_dir / name)
            validate_artifact(artifact)
            self.assertEqual(replay["run_id"], artifact["run_id"])
        connected = read_json(replay_dir / "connected_evidence_map.json")
        lineage = connected["extensions"][
            "com.partner_advisory.nee.semantic-cache-replay"
        ]
        self.assertEqual(source["run_id"], lineage["source_run_id"])
        gates = read_json(replay_dir / "x_gates.json")
        self.assertTrue(gates["X1"]["semantic_cache_replay_valid"])
        self.assertTrue(gates["X1"]["semantic_cache_prior_output_matches"])

        inventory = discover_inventory(self.runtime)
        proof_records = {
            item["run_id"]: item
            for item in inventory["query_evidence"]
            if item["run_id"] in {source["run_id"], replay["run_id"]}
        }
        self.assertEqual(
            {"valid"},
            {
                proof_records[source["run_id"]][
                    "retrieval_report_binding_status"
                ],
                proof_records[replay["run_id"]][
                    "retrieval_report_binding_status"
                ],
            },
        )
        self.assertFalse(inventory["issues"])

    def test_legacy_query_cache_entry_without_evidence_chain_is_not_replayable(
        self,
    ) -> None:
        lookup = SemanticCacheLookup(
            mode="read",
            hit=True,
            reason="CACHE_HIT",
            output={
                "exact_retrieval": {},
                "hybrid_retrieval": {},
                "graph_retrieval": {},
                "connected_evidence_map": {},
                "graph_hydration": {},
                "evidence_packet": {},
                "answer": {},
            },
        )
        self.assertIsNone(
            _cached_query_output(
                lookup,
                active=self.active,
                plan={"retrieval_mode": "factual"},
            )
        )

    def test_intake_populates_replays_and_covers_hitl_review(self) -> None:
        configure_semantic_cache(self.runtime, "write")
        fixture = read_json(
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        source_path = self.runtime / "source.json"
        write_json(source_path, fixture)
        source = self._intake_engine().ingest(source_path)
        self.assertGreater(source["semantic_cache"]["writeback_count"], 0)

        configure_semantic_cache(self.runtime, "read_write")
        replay_fixture = deepcopy(fixture)
        replay_fixture["artifact_id"] = "evidence-envelope-replay-20260907"
        replay_fixture["meeting"]["meeting_id"] = "teams-semantic-cache-replay"
        replay_fixture["meeting"]["source_hash"] = "b" * 64
        replay_fixture["canonical_digest"]["value"] = digest(
            {
                key: value
                for key, value in replay_fixture.items()
                if key != "canonical_digest"
            }
        )
        replay_path = self.runtime / "replay.json"
        write_json(replay_path, replay_fixture)
        replay = self._intake_engine().ingest(replay_path)
        self.assertGreater(replay["semantic_cache"]["lookup_hit_count"], 0)
        self.assertEqual(0, replay["persistent_embedding_write_count"])

        decisions = self.runtime / "empty-decisions.json"
        write_json(decisions, [])
        reviewed = self._intake_engine().review(replay["run_id"], decisions)
        self.assertTrue(reviewed["hitl"]["review_applied"])
        self.assertGreater(reviewed["semantic_cache"]["writeback_count"], 0)


    def test_prune_expired_entries_cleans_only_expired(self) -> None:
        configure_semantic_cache(self.runtime, "read_write")
        cache = self._bound_cache()
        context = {
            "use_case": "query",
            "route": "exact_lookup",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "artifact_type": "decision",
            "active_pair": self.active,
        }
        res1 = cache.stage(
            context=context,
            intent_digest="a" * 64,
            intent_vector=self.real_intent_vector,
            output={"test": 1},
            outcome="C0.2_COMPARISON_COMPLETE",
            source_run_id="run-1",
            source_trace_id="trace-1",
            replay_eligible=True,
        )
        self.assertEqual("PENDING", res1.status)
        assert res1.entry is not None
        act1 = cache.activate(
            res1.entry,
            source_event_count=1,
            source_event_chain_digest="c" * 64,
        )
        self.assertEqual("ACTIVE", act1.status)

        # Manually backdate entry to be expired in the past
        import sqlite3
        conn = sqlite3.connect(cache.path)
        conn.execute(
            "UPDATE semantic_cache_entry SET expires_at='2020-01-01T00:00:00Z' WHERE entry_id=?",
            (res1.entry.entry_id,),
        )
        conn.commit()
        conn.close()

        # Stage a second fresh entry
        res2 = cache.stage(
            context=context,
            intent_digest="b" * 64,
            intent_vector=self.real_intent_vector,
            output={"test": 2},
            outcome="C0.2_COMPARISON_COMPLETE",
            source_run_id="run-2",
            source_trace_id="trace-2",
            replay_eligible=True,
        )
        # Stage opportunistically pruned the expired entry
        conn = sqlite3.connect(cache.path)
        rows = conn.execute("SELECT entry_id FROM semantic_cache_entry").fetchall()
        conn.close()
        entry_ids = [r[0] for r in rows]
        self.assertNotIn(res1.entry.entry_id, entry_ids)
        assert res2.entry is not None
        self.assertIn(res2.entry.entry_id, entry_ids)

        # Calling prune_expired directly returns 0 since no remaining expired entries
        self.assertEqual(0, cache.prune_expired())


if __name__ == "__main__":
    unittest.main()
