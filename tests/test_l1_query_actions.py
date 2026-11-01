#!/usr/bin/env python3
"""Wave 5 isolated L1 retrieval-action tests."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import digest, sha256_file
from dual_pipeline.l1_control import BudgetState, create_action, load_policy
from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query import build_query_plan
from dual_pipeline.query_l1_actions import (
    ACTION_REGISTRY,
    ActionLimits,
    execute_l1_action,
)
from dual_pipeline.query_l1_shadow import build_shadow_action
from dual_pipeline.query_planning import compile_query_plan_candidate, compile_requirement_query
from dual_pipeline.query_router import route_query

ROOT = Path(__file__).resolve().parents[1]


class CountingBackend:
    backend_id = "fixture-backend"
    model_id = "fixture-model"
    model_revision = "fixture-revision"
    model_manifest_digest = "f" * 64
    production_qualified = False
    assurance_tier = "development"

    def __init__(self) -> None:
        self.encode_count = 0
        self.verify_count = 0

    def verify_snapshot(self) -> dict[str, object]:
        self.verify_count += 1
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "manifest_digest": self.model_manifest_digest,
        }

    def encode(self, query: str) -> dict[str, object]:
        self.encode_count += 1
        return {"dense": [1.0], "sparse": {"query": 1.0}, "colbert": [[1.0]]}


class FakeRetriever:
    calls: list[dict[str, object]] = []

    def __init__(self, sidecar_path: Path, backend: CountingBackend) -> None:
        self.sidecar_path = sidecar_path
        self.backend = backend

    def retrieve_lane(self, query: str, lane: str, **kwargs: object) -> dict[str, object]:
        self.calls.append({"query": query, "lane": lane, **kwargs})
        row = {
            "record_id": f"{lane}-record",
            "lane": lane,
            "fused_rank": 1,
            "dense_rank": 1,
            "sparse_rank": 1,
            "bm25_rank": None,
            "colbert_rank": 1,
            "record": {"record_id": f"{lane}-record", "title": "controlled", "retrieval_text": "controlled"},
        }
        return {
            "lane": lane,
            "results": [row],
            "dense_candidate_count": 1,
            "sparse_candidate_count": 1,
            "bm25_candidate_count": 0,
            "colbert_candidate_count": 1,
            "candidate_union_count": 1,
            "authorized_record_count": 1,
        }


def _policy():
    return load_policy(json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8")))


def _plan(case_id: str = "actions") -> dict[str, object]:
    route = route_query("Which exact governed A2A protocol records are available?").to_artifact(
        request_id=f"wave5-request-{case_id}",
        run_id=f"wave5-run-{case_id}",
        trace_id=digest(["wave5-trace", case_id])[:32],
        decided_at="2026-08-06T00:00:00Z",
    )
    return build_query_plan(
        question="Which exact governed A2A protocol records are available?",
        query_type="exact_lookup",
        request_id=f"wave5-request-{case_id}",
        run_id=f"wave5-run-{case_id}",
        current_trace_id=digest(["wave5-trace", case_id])[:32],
        l2_config=load_l2_config(),
        provider_egress_allowed=False,
        destination_decision=route,
        assurance_profile=None,
    )


def _manifest(plan: dict[str, object], case_id: str = "actions") -> dict[str, object]:
    body = {
        "manifest_id": f"wave5-manifest-{case_id}",
        "run_id": plan["run_id"],
        "validated_request_digest": digest(["wave5-request", case_id]),
        "query_plan_digest": plan["canonical_digest"]["value"],
        "graph_snapshot": "wave5-fixture",
        "graph_sha256": "0" * 64,
        "authorization_scope_digest": "2" * 64,
        "requirements": [
            {
                "requirement_id": f"wave5-requirement-{case_id}",
                "requirement_label": "A2A protocol evidence",
                "requirement_kind": "evidence",
                "allowed_lanes": ["approved"],
                "missing_evidence_disposition": "explicit_gap",
                "materiality": "high",
            }
        ],
    }
    return create_artifact(
        "query-coverage-manifest",
        body["manifest_id"],
        **{key: value for key, value in body.items() if key != "manifest_id"},
        manifest_id=body["manifest_id"],
    )


class L1QueryActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = _policy()
        self.plan_v2 = _plan()
        self.manifest = _manifest(self.plan_v2)
        candidate = compile_query_plan_candidate(
            self.plan_v2,
            self.manifest,
            self.policy,
            mode="shadow",
            observations={
                self.manifest["requirements"][0]["requirement_id"]: (
                    "explicit_gap",
                    "RETRIEVAL_COVERAGE_MISSING",
                )
            },
        )
        self.plan = candidate.plan
        requirement = self.manifest["requirements"][0]
        self.compiled = compile_requirement_query(requirement, self.plan)
        source_action = candidate.actions[0]
        self.action, _ = build_shadow_action(
            action=source_action,
            plan_digest=self.plan["canonical_digest"]["value"],
            coverage_manifest_digest=self.manifest["canonical_digest"]["value"],
            authorization_scope_digest=self.manifest["authorization_scope_digest"],
            active_pair={
                "generation_id": "wave5-generation",
                "graph_sha256": "0" * 64,
                "sidecar_sha256": "1" * 64,
                "embedding_backend": "fixture-backend",
                "model_id": "fixture-model",
                "model_revision": "fixture-revision",
            },
            budget=BudgetState.initial(),
            graph_sha256="0" * 64,
        )
        # The shadow artifact is not used as the execution receipt; this test
        # uses the deterministic action object that rebases to the v3 plan.
        self.backend = CountingBackend()
        self.temp = tempfile.TemporaryDirectory(prefix="nee-wave5-")
        self.graph = Path(self.temp.name) / "graph.sqlite"
        self.sidecar = Path(self.temp.name) / "sidecar.sqlite"
        self.graph.write_bytes(b"wave5 graph")
        self.sidecar.write_bytes(b"wave5 sidecar")
        self.active = {
            "generation_id": "wave5-generation",
            "graph_sha256": sha256_file(self.graph),
            "sidecar_sha256": sha256_file(self.sidecar),
            "embedding_backend": "fixture-backend",
            "model_id": "fixture-model",
            "model_revision": "fixture-revision",
        }
        self.manifest["graph_sha256"] = self.active["graph_sha256"]
        # Re-seal the changed manifest and rebase the action to its digest.
        from dual_pipeline.core import seal_artifact

        self.manifest = seal_artifact({key: value for key, value in self.manifest.items() if key != "canonical_digest"})
        self.action = create_action(
            parent_plan_digest=self.plan["canonical_digest"]["value"],
            coverage_manifest_digest=self.manifest["canonical_digest"]["value"],
            authorization_scope_digest=self.manifest["authorization_scope_digest"],
            attempt_ordinal=1,
            action_type="requirement_hybrid_retrieval",
            requirement_ids=(self.manifest["requirements"][0]["requirement_id"],),
            input_digest=self.compiled.input_digest,
        )
        FakeRetriever.calls = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, **overrides):
        args = {
            "action": self.action,
            "plan": self.plan,
            "coverage_manifest": self.manifest,
            "requirement_queries": {self.compiled.requirement_id: self.compiled},
            "active_pair": self.active,
            "graph_path": self.graph,
            "sidecar_path": self.sidecar,
            "backend": self.backend,
            "policy": self.policy,
            # Wave 5 executes the W4-rebased ordinal-one action after the
            # existing attempt-zero work has been accounted.
            "budget": BudgetState(actions_used=1),
            "classification": "public",
            "audiences": ["internal_consulting"],
            "allowed_lanes": ["approved"],
            "retrieval_mode": self.plan["retrieval_mode"],
            "authorization_scope_digest": self.manifest["authorization_scope_digest"],
        }
        args.update(overrides)
        with mock.patch("dual_pipeline.query_l1_actions.validate_sidecar"), mock.patch(
            "dual_pipeline.query_l1_actions.HybridRetriever", FakeRetriever
        ):
            return execute_l1_action(**args)

    def test_closed_registry_and_stop_are_non_dispatching(self) -> None:
        self.assertEqual({"requirement_hybrid_retrieval", "stop"}, set(ACTION_REGISTRY))
        stop = create_action(
            parent_plan_digest=self.plan["canonical_digest"]["value"],
            coverage_manifest_digest=self.manifest["canonical_digest"]["value"],
            authorization_scope_digest=self.manifest["authorization_scope_digest"],
            attempt_ordinal=1,
            action_type="stop",
            requirement_ids=(self.action.requirement_ids[0],),
            input_digest=self.compiled.input_digest,
        )
        result = self._run(action=stop)
        self.assertEqual("skipped", result.status)
        self.assertEqual("STOP", result.reason_code)
        self.assertEqual(0, self.backend.encode_count)

    def test_success_is_lane_isolated_bounded_and_content_separated(self) -> None:
        result = self._run()
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.encoding_count)
        self.assertEqual(1, self.backend.encode_count)
        self.assertEqual(2, self.backend.verify_count)
        self.assertEqual(["approved-record"], result.attempt_artifact["result"]["record_ids_by_lane"]["approved"])
        self.assertIn("controlled_retrieval_artifact_digest", result.attempt_artifact["extensions"])
        self.assertEqual(0, result.attempt_artifact["provider_call_count"])
        self.assertEqual(0, result.attempt_artifact["provider_initialization_count"])
        self.assertIn("record", json.dumps(result.retrieval_artifact))
        self.assertNotIn("\"record\"", json.dumps(result.attempt_artifact))
        self.assertEqual(25, FakeRetriever.calls[0]["top_k"])
        self.assertEqual(25, FakeRetriever.calls[0]["union_limit"])
        validate_artifact(result.attempt_artifact)

    def test_binding_and_scope_drift_blocks_before_encoder(self) -> None:
        for name, override in {
            "authorization": {"authorization_scope_digest": "3" * 64},
            "mode": {"retrieval_mode": "strategic"},
            "lanes": {"allowed_lanes": ["candidate"]},
            "query": {"requirement_queries": {self.compiled.requirement_id: {"query": "drift", "input_digest": "4" * 64}}},
        }.items():
            with self.subTest(name=name):
                self.backend.encode_count = 0
                result = self._run(**override)
                self.assertEqual("blocked", result.status)
                self.assertEqual(0, self.backend.encode_count)

    def test_pre_action_budget_guard_runs_before_encoder_or_retriever(self) -> None:
        limits = ActionLimits.from_policy(self.policy, 1)
        budget = BudgetState(
            actions_used=1,
            additional_attempts_used=0,
            result_bytes=self.policy.max_result_bytes - limits.max_result_bytes_per_action + 1,
            elapsed_ms=0,
        )
        result = self._run(budget=budget)
        self.assertEqual("blocked", result.status)
        self.assertEqual("L1_BUDGET_EXHAUSTED", result.reason_code)
        self.assertEqual(0, self.backend.encode_count)
        self.assertEqual([], FakeRetriever.calls)

    def test_graph_or_backend_drift_is_fail_closed_and_never_mutates(self) -> None:
        self.graph.write_bytes(b"mutated graph")
        result = self._run()
        self.assertEqual("blocked", result.status)
        self.assertEqual("L1_INTERNAL_INVALID", result.reason_code)
        self.assertEqual(0, self.backend.encode_count)

    def test_oversized_or_duplicate_results_are_rejected(self) -> None:
        class DuplicateRetriever(FakeRetriever):
            def retrieve_lane(self, query: str, lane: str, **kwargs: object) -> dict[str, object]:
                result = super().retrieve_lane(query, lane, **kwargs)
                result["results"] = [result["results"][0], copy.deepcopy(result["results"][0])]
                return result

        with mock.patch("dual_pipeline.query_l1_actions.validate_sidecar"), mock.patch(
            "dual_pipeline.query_l1_actions.HybridRetriever", DuplicateRetriever
        ):
            result = execute_l1_action(
                self.action,
                plan=self.plan,
                coverage_manifest=self.manifest,
                requirement_queries={self.compiled.requirement_id: self.compiled},
                active_pair=self.active,
                graph_path=self.graph,
                sidecar_path=self.sidecar,
                backend=self.backend,
                policy=self.policy,
            budget=BudgetState(actions_used=1),
                classification="public",
                audiences=["internal_consulting"],
                allowed_lanes=["approved"],
                retrieval_mode=self.plan["retrieval_mode"],
                authorization_scope_digest=self.manifest["authorization_scope_digest"],
            )
        self.assertEqual("blocked", result.status)
        self.assertEqual("L1_INTERNAL_INVALID", result.reason_code)


if __name__ == "__main__":
    unittest.main()
