#!/usr/bin/env python3
"""Wave 0 characterization tests for the one-shot query L1 baseline."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.core import digest
from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query import (
    build_query_plan,
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
    coverage_complete,
)
from dual_pipeline.query_router import QueryDestination, route_query

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "evals" / "suites" / "query-l1-agentic-baseline-e1.json"
FIXTURE_ACTIVE = {
    "graph_snapshot": "wave-00-deterministic-fixture",
    "graph_sha256": "0" * 64,
}
AUTHORIZATION_SCOPE_DIGEST = "2" * 64


def _load_suite() -> dict[str, Any]:
    value = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("Wave 0 suite must be a JSON object")
    return value


def _route_artifact(case: dict[str, Any]) -> dict[str, Any]:
    request_id = f"wave-00-{case['case_id']}"
    run_id = f"run-{case['case_id']}"
    decision = route_query(
        case["question"],
        query_type_override=case.get("query_type_override"),
    )
    return decision.to_artifact(
        request_id=request_id,
        run_id=run_id,
        trace_id=digest(["trace", case["case_id"]])[:32],
        decided_at="2026-08-06T00:00:00Z",
    )


def _plan_and_manifest(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    route = _route_artifact(case)
    query_type = case["expected_query_type"]
    graph_utility = route["destination"] == QueryDestination.GRAPH_EVIDENCE_UTILITY.value
    plan = build_query_plan(
        question=case["question"],
        query_type=query_type,
        request_id=f"wave-00-{case['case_id']}",
        run_id=f"run-{case['case_id']}",
        current_trace_id=digest(["trace", case["case_id"]])[:32],
        l2_config=load_l2_config(),
        provider_egress_allowed=False,
        destination_decision=route,
        assurance_profile=None if graph_utility else "standard",
    )
    manifest = compile_query_coverage_manifest(
        question=case["question"],
        validated_request_digest=digest(["validated-request", case["case_id"]]),
        plan=plan,
        active=FIXTURE_ACTIVE,
        authorization_scope_digest=AUTHORIZATION_SCOPE_DIGEST,
    )
    return plan, manifest


def _single_requirement_manifest(case: dict[str, Any]) -> dict[str, Any]:
    requirement = {
        "requirement_id": f"requirement-{case['case_id']}",
        "requirement_label": case["requirement_label"],
        "requirement_kind": "relationship"
        if case["failure_family"] == "reverse_only_path"
        else "evidence",
        "allowed_lanes": case["allowed_lanes"],
        "missing_evidence_disposition": "explicit_gap",
        "materiality": "high",
    }
    body = {
        "artifact_type": "query-coverage-manifest",
        "manifest_id": f"manifest-{case['case_id']}",
        "run_id": f"run-{case['case_id']}",
        "validated_request_digest": digest(["request", case["case_id"]]),
        "query_plan_digest": digest(["plan", case["case_id"]]),
        "graph_snapshot": FIXTURE_ACTIVE["graph_snapshot"],
        "graph_sha256": FIXTURE_ACTIVE["graph_sha256"],
        "authorization_scope_digest": AUTHORIZATION_SCOPE_DIGEST,
        "requirements": [requirement],
    }
    body["canonical_digest"] = {"value": digest(body)}
    return body


def _report(case: dict[str, Any]) -> dict[str, Any]:
    manifest = _single_requirement_manifest(case)
    rows = case.get("packet_rows", [])
    packet_body = {
        "graph_snapshot": FIXTURE_ACTIVE["graph_snapshot"],
        "allowed_lanes": case.get("packet_allowed_lanes", case["allowed_lanes"]),
        "approved": rows if "approved" in case.get("packet_allowed_lanes", case["allowed_lanes"]) else [],
        "candidate": rows if "candidate" in case.get("packet_allowed_lanes", case["allowed_lanes"]) else [],
        "external_public": [],
        "counterevidence": case.get("counterevidence", []),
    }
    packet_body["packet_digest"] = digest(packet_body)
    return build_retrieval_evidence_report(
        coverage_manifest=manifest,
        packet=packet_body,
        authorization_scope_digest=AUTHORIZATION_SCOPE_DIGEST,
        graph_sha256=FIXTURE_ACTIVE["graph_sha256"],
        view_authorized=True,
    )


class L1QueryBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite = _load_suite()

    def test_suite_has_complete_case_taxonomy_and_nonvacuous_expectations(self) -> None:
        self.assertGreaterEqual(len(self.suite["query_type_cases"]), 9)
        self.assertGreaterEqual(len(self.suite["coverage_failure_cases"]), 10)
        self.assertEqual(
            {case["expected_query_type"] for case in self.suite["query_type_cases"]},
            {
                "exact_lookup",
                "current_state_corroboration",
                "project_status_current",
                "project_status_history",
                "relationship_explanation",
                "cross_workstream_synthesis",
                "gap_risk_inconsistency",
                "opportunity_nba",
                "candidate_promotion_review",
            },
        )
        for case in self.suite["query_type_cases"]:
            self.assertTrue(case["question"].strip())
            self.assertTrue(case["expected_destination"])
            self.assertTrue(case["expected_terminal_class"])
        for case in self.suite["coverage_failure_cases"]:
            self.assertTrue(case["requirement_label"])
            self.assertTrue(case["expected_outcome"])
            self.assertTrue(case["baseline_terminal_class"])
            self.assertTrue(case["future_controller_terminal_class"])

    def test_every_query_type_reproduces_route_plan_and_manifest_bindings(self) -> None:
        for case in self.suite["query_type_cases"]:
            with self.subTest(case_id=case["case_id"]):
                route = _route_artifact(case)
                plan, manifest = _plan_and_manifest(case)
                self.assertEqual(case["expected_destination"], route["destination"])
                self.assertEqual(case["expected_query_type"], route["query_type"])
                self.assertEqual(case["expected_query_type"], plan["query_type"])
                self.assertEqual(plan["canonical_digest"]["value"], manifest["query_plan_digest"])
                self.assertEqual(FIXTURE_ACTIVE["graph_sha256"], manifest["graph_sha256"])
                self.assertEqual(AUTHORIZATION_SCOPE_DIGEST, manifest["authorization_scope_digest"])
                self.assertGreaterEqual(len(manifest["requirements"]), 1)

    def test_critical_failure_families_have_expected_one_shot_outcomes(self) -> None:
        expected_families = {
            "complete_first_pass",
            "recoverable_miss",
            "true_gap",
            "irrelevant_lexical_overlap",
            "unauthorized_evidence",
            "reverse_only_path",
            "conflict",
            "candidate_lane",
            "cache_replay",
            "no_progress",
        }
        actual_families = {case["failure_family"] for case in self.suite["coverage_failure_cases"]}
        self.assertEqual(expected_families, actual_families)
        for case in self.suite["coverage_failure_cases"]:
            with self.subTest(case_id=case["case_id"]):
                report = _report(case)
                outcome = report["requirement_outcomes"][0]
                self.assertEqual(case["expected_outcome"], outcome["outcome"])
                if "expected_reason_code" in case:
                    self.assertEqual(case["expected_reason_code"], outcome["reason_code"])
                if "expected_lane" in case:
                    self.assertEqual(case["expected_lane"], case["allowed_lanes"][0])
                if case["failure_family"] in {
                    "complete_first_pass",
                    "candidate_lane",
                    "cache_replay",
                }:
                    self.assertTrue(coverage_complete(report))
                else:
                    self.assertFalse(coverage_complete(report))

    def test_cache_replay_is_byte_equal_and_mutated_bindings_are_detectable(self) -> None:
        case = next(case for case in self.suite["coverage_failure_cases"] if case["failure_family"] == "cache_replay")
        first = _report(case)
        second = _report(case)
        self.assertEqual(first, second)
        self.assertEqual(digest(first), digest(second))
        mutated = dict(first)
        mutated["authorization_scope_digest"] = "f" * 64
        self.assertNotEqual(digest(first), digest(mutated))

    def test_baseline_is_provider_free_and_graph_read_only(self) -> None:
        self.assertEqual(0, self.suite["thresholds"]["maximum_provider_calls"])
        self.assertEqual(0, self.suite["thresholds"]["maximum_graph_mutations"])
        for case in self.suite["query_type_cases"]:
            plan, manifest = _plan_and_manifest(case)
            self.assertFalse(plan["provider_egress_allowed"])
            self.assertEqual(FIXTURE_ACTIVE["graph_sha256"], manifest["graph_sha256"])


if __name__ == "__main__":
    unittest.main()
