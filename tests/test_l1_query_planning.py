#!/usr/bin/env python3
"""Wave 3 deterministic query planning and shadow-action tests."""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from dual_pipeline.core import digest
from dual_pipeline.l1_control import load_policy, validate_action_chain
from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query import build_query_plan, compile_query_coverage_manifest
from dual_pipeline.query_planning import (
    RequirementQueryError,
    classify_refinability,
    compile_query_plan_candidate,
    compile_requirement_actions,
    compile_requirement_query,
)
from dual_pipeline.query_router import QueryDestination, route_query
from tools.validate_dual_pipeline_contracts import SchemaRegistry, validate_artifact

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "contracts" / "dual_pipeline" / "schemas"
POLICY_PATH = ROOT / "requirements" / "query-l1-control-policy.json"


def _policy():
    return load_policy(json.loads(POLICY_PATH.read_text(encoding="utf-8")))


def _route_plan_manifest(case: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    question = str(case["question"])
    case_id = str(case["case_id"])
    route = route_query(question, query_type_override=case.get("query_type_override"))
    route_artifact = route.to_artifact(
        request_id=f"wave3-request-{case_id}",
        run_id=f"wave3-run-{case_id}",
        trace_id=digest(["wave3-trace", case_id])[:32],
        decided_at="2026-08-06T00:00:00Z",
    )
    graph_utility = route_artifact["destination"] == QueryDestination.GRAPH_EVIDENCE_UTILITY.value
    plan = build_query_plan(
        question=question,
        query_type=str(case["expected_query_type"]),
        request_id=f"wave3-request-{case_id}",
        run_id=f"wave3-run-{case_id}",
        current_trace_id=digest(["wave3-trace", case_id])[:32],
        l2_config=load_l2_config(),
        provider_egress_allowed=False,
        destination_decision=route_artifact,
        assurance_profile=None if graph_utility else "standard",
    )
    manifest = compile_query_coverage_manifest(
        question=question,
        validated_request_digest=digest(["wave3-request", case_id]),
        plan=plan,
        active={"graph_snapshot": "wave3-fixture", "graph_sha256": "0" * 64},
        authorization_scope_digest="2" * 64,
    )
    return plan, manifest


def _suite() -> dict[str, object]:
    return json.loads(
        (ROOT / "evals" / "suites" / "query-l1-agentic-baseline-e1.json").read_text(
            encoding="utf-8"
        )
    )


def _simple_plan() -> dict[str, object]:
    return {
        "canonical_digest": {"value": "a" * 64},
        "plan_id": "wave3-plan",
        "query_type": "cross_workstream_synthesis",
        "primary_entities": ["Project Beacon", "NEE"],
        "time_horizon": "future state",
    }


def _manifest(*requirements: dict[str, object]) -> dict[str, object]:
    return {
        "canonical_digest": {"value": "b" * 64},
        "authorization_scope_digest": "c" * 64,
        "requirements": list(requirements),
    }


def _requirement(requirement_id: str, label: str, disposition: str = "explicit_gap") -> dict[str, object]:
    return {
        "requirement_id": requirement_id,
        "requirement_label": label,
        "requirement_kind": "evidence",
        "allowed_lanes": ["approved"],
        "missing_evidence_disposition": disposition,
        "materiality": "high",
    }


class L1QueryPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = _policy()
        cls.registry = SchemaRegistry(SCHEMA_ROOT)
        cls.registry.load()

    def test_golden_shadow_candidate_covers_every_baseline_query_type(self) -> None:
        cases = _suite()["query_type_cases"]
        observed_types: set[str] = set()
        for case in cases:
            with self.subTest(case=case["case_id"]):
                plan, manifest = _route_plan_manifest(case)
                observed_types.add(str(plan["query_type"]))
                result = compile_query_plan_candidate(plan, manifest, self.policy, mode="shadow")
                validate_artifact(result.plan, self.registry)
                self.assertEqual("3.0.0", result.plan["schema_version"])
                self.assertEqual("shadow", result.plan["controller"]["mode"])
                for field in (
                    "destination",
                    "query_type",
                    "materiality",
                    "assurance_profile",
                    "delivery_profile",
                    "artifact_type",
                    "l2_policy",
                    "allowed_lanes",
                    "retrieval",
                    "retrieval_mode",
                    "primary_entities",
                    "time_horizon",
                ):
                    self.assertEqual(plan[field], result.plan[field], field)
                self.assertEqual(
                    result.plan["controller"]["initial_action_ids"],
                    [action.action_id for action in result.actions],
                )
                self.assertEqual(
                    result.plan["controller"]["coverage_manifest_digest"],
                    manifest["canonical_digest"]["value"],
                )
        self.assertGreaterEqual(len(observed_types), 9)

    def test_off_mode_is_byte_for_byte_v2_fallback(self) -> None:
        case = _suite()["query_type_cases"][0]
        plan, manifest = _route_plan_manifest(case)
        before = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result = compile_query_plan_candidate(plan, manifest, self.policy, mode="off")
        after = json.dumps(result.plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(before, after)
        self.assertEqual("off", result.mode)
        self.assertEqual((), result.actions)
        self.assertNotIn("controller", result.plan)
        self.assertEqual(
            (),
            compile_requirement_actions(plan, manifest, self.policy, mode="off").actions,
        )

    def test_query_compiler_is_canonical_and_excludes_formatting(self) -> None:
        plan = _simple_plan()
        requirement = _requirement("req-format", "Create 3 slides; A2A communication boundaries")
        compiled = compile_requirement_query(requirement, plan)
        self.assertEqual("Project Beacon, NEE; future state; A2A communication boundaries", compiled.query)
        self.assertEqual(compiled.input_digest, compile_requirement_query(requirement, plan).input_digest)
        self.assertNotIn("slide", compiled.query.casefold())
        with self.assertRaisesRegex(RequirementQueryError, "L1_REQUIREMENT_QUERY_INVALID"):
            compile_requirement_query(_requirement("req-empty", "Use a table"), plan)
        with self.assertRaisesRegex(RequirementQueryError, "L1_REQUIREMENT_QUERY_INVALID"):
            compile_requirement_query(_requirement("req-long", "A" * 600), plan, max_length=128)

    def test_refinability_is_closed_and_reason_bound(self) -> None:
        self.assertTrue(classify_refinability("explicit_gap", "RETRIEVAL_COVERAGE_MISSING"))
        for outcome, reason in (
            ("covered", "RETRIEVAL_COVERAGE_MISSING"),
            ("explicit_gap", "UNAUTHORIZED_LANE"),
            ("unauthorized", "RETRIEVAL_COVERAGE_MISSING"),
            ("conflict", "RETRIEVAL_COVERAGE_MISSING"),
            ("excluded", "RETRIEVAL_COVERAGE_MISSING"),
        ):
            with self.subTest(outcome=outcome, reason=reason):
                self.assertFalse(classify_refinability(outcome, reason))

    def test_exact_normalized_duplicates_share_action_but_similar_text_does_not(self) -> None:
        requirements = (
            _requirement("req-1", "A2A protocol"),
            _requirement("req-2", " A2A   protocol "),
            _requirement("req-3", "Agent-to-agent protocol"),
        )
        compilation = compile_requirement_actions(_simple_plan(), _manifest(*requirements), self.policy)
        self.assertEqual(2, len(compilation.actions))
        self.assertEqual(("req-1", "req-2"), compilation.actions[0].requirement_ids)
        self.assertEqual(("req-3",), compilation.actions[1].requirement_ids)
        self.assertEqual((), compilation.omitted_requirement_ids)
        self.assertEqual(
            64,
            len(
                validate_action_chain(
                    compilation.actions,
                    parent_plan_digest="a" * 64,
                    coverage_manifest_digest="b" * 64,
                )
            ),
        )

    def test_target_cap_is_explicit_and_budget_terminal_is_preserved(self) -> None:
        requirements = tuple(_requirement(f"req-{i}", f"Requirement {i}") for i in range(1, 5))
        compilation = compile_requirement_actions(_simple_plan(), _manifest(*requirements), self.policy, max_targets=99)
        self.assertEqual(self.policy.max_actions, len(compilation.actions))
        self.assertEqual(("req-3", "req-4"), compilation.omitted_requirement_ids)
        self.assertTrue(compilation.capped)
        self.assertEqual("L1_BUDGET_EXHAUSTED", compilation.terminal_reason)

    def test_non_refinable_dispositions_never_create_actions(self) -> None:
        requirements = (
            _requirement("req-gap", "Gap", "explicit_gap"),
            _requirement("req-block", "Blocked", "block"),
            _requirement("req-hitl", "Escalate", "escalate_hitl"),
        )
        compilation = compile_requirement_actions(_simple_plan(), _manifest(*requirements), self.policy)
        self.assertEqual(("req-gap",), compilation.refinable_requirement_ids)
        self.assertEqual(("req-gap",), compilation.actions[0].requirement_ids)

    def test_bounded_mode_compiles_candidate_without_provider_or_graph_work(self) -> None:
        case = _suite()["query_type_cases"][0]
        plan, manifest = _route_plan_manifest(case)
        bounded = compile_query_plan_candidate(plan, manifest, self.policy, mode="bounded")
        self.assertEqual("bounded", bounded.mode)
        source = (ROOT / "dual_pipeline" / "query_planning.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )
        self.assertFalse(any(name.startswith(("dual_pipeline.query", "dual_pipeline.l2")) for name in imports))
        self.assertNotIn("ProviderFactory", source)
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("embedding", source.casefold())
        self.assertTrue(all(action.action_type == "requirement_hybrid_retrieval" for action in compile_query_plan_candidate(plan, manifest, self.policy).actions))

    def test_wave3_completion_receipt_is_schema_valid_and_binds_wave2(self) -> None:
        receipt = json.loads(
            (ROOT / "governance" / "dual_pipeline" / "l1_agentic" / "wave-03-completion.json").read_text(
                encoding="utf-8"
            )
        )
        validate_artifact(receipt, self.registry)
        self.assertEqual("W3", receipt["wave_id"])
        self.assertEqual(
            "e19cbc650c72a36c063e0bceeed5229a4f56f801bbdc0d5f09eb11c499abebf0",
            receipt["predecessor_receipt_digest"],
        )
        self.assertTrue(receipt["next_wave_authorized"])
        self.assertEqual(0, receipt["provider_call_count"])
        self.assertEqual(0, receipt["graph_mutation_count"])


if __name__ == "__main__":
    unittest.main()
