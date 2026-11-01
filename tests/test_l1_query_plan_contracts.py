#!/usr/bin/env python3
"""Wave 1 contract, compatibility, and provider-boundary tests."""
from __future__ import annotations

import json
import unittest

from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query import build_query_plan
from dual_pipeline.query_router import route_query
from tools.validate_dual_pipeline_contracts import (
    ROOT,
    SchemaRegistry,
    ValidationFailure,
    load_json,
    run_validation,
    validate_artifact,
)

CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
VALID_ROOT = CONTRACT_ROOT / "fixtures" / "valid"
INVALID_ROOT = CONTRACT_ROOT / "fixtures" / "invalid"
NEW_SCHEMA_NAMES = {
    "query-plan-v3.schema.json",
    "query-retrieval-attempt.schema.json",
    "query-plan-observation.schema.json",
    "query-plan-terminal.schema.json",
    "query-l1-control-policy.schema.json",
    "query-l1-controller-eval-suite.schema.json",
    "query-l1-controller-eval-result.schema.json",
    "query-l1-controller-eval-comparison.schema.json",
    "retrieval-release-suite-v2.schema.json",
    "retrieval-release-evaluation-v2.schema.json",
    "l1-wave-receipt.schema.json",
}
NEW_VALID_NAMES = {
    "query-plan-v3.json",
    "query-retrieval-attempt.json",
    "query-plan-observation.json",
    "query-plan-terminal.json",
    "query-l1-control-policy.json",
    "query-l1-controller-eval-suite.json",
    "query-l1-controller-eval-result.json",
    "query-l1-controller-eval-comparison.json",
    "retrieval-release-suite-v2.json",
    "retrieval-release-evaluation-v2.json",
    "l1-wave-01-receipt.json",
}


class L1QueryPlanContractTests(unittest.TestCase):
    def test_closed_world_registry_and_all_wave1_fixtures_validate(self) -> None:
        report = run_validation(CONTRACT_ROOT)
        self.assertEqual("PASS", report["status"])
        self.assertGreaterEqual(report["schema_count"], 83)
        self.assertGreaterEqual(report["fixtures"]["valid_count"], 83)
        self.assertGreaterEqual(report["fixtures"]["invalid_count"], 44)
        self.assertEqual(0, report["provider_call_count"])
        self.assertEqual(0, report["graph_mutation_count"])
        self.assertTrue(NEW_SCHEMA_NAMES.issubset({path.name for path in SCHEMA_ROOT.glob("*.schema.json")}))
        self.assertTrue(NEW_VALID_NAMES.issubset({path.name for path in VALID_ROOT.glob("*.json")}))

    def test_wave1_files_have_lf_stable_bytes_and_sealed_digests(self) -> None:
        for name in sorted(NEW_SCHEMA_NAMES | NEW_VALID_NAMES):
            root = SCHEMA_ROOT if name.endswith(".schema.json") else VALID_ROOT
            path = root / name
            with self.subTest(path=name):
                self.assertNotIn(b"\r\n", path.read_bytes())
                value = load_json(path)
                if name.endswith(".json") and not name.endswith(".schema.json"):
                    registry = SchemaRegistry(SCHEMA_ROOT)
                    registry.load()
                    validate_artifact(value, registry)

    def test_checked_in_policy_is_off_and_closed(self) -> None:
        policy = json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8"))
        self.assertEqual("off", policy["mode"])
        self.assertEqual(["requirement_hybrid_retrieval", "stop"], policy["action_allowlist"])
        self.assertEqual(0, policy["provider_calls_before_l2d"])
        self.assertEqual(0, policy["graph_mutations_before_l2d"])

    def test_historical_v2_fixture_and_current_runtime_remain_v2(self) -> None:
        historical = load_json(VALID_ROOT / "query-plan-v2.json")
        self.assertEqual("urn:nee:dual-pipeline:query-plan:2.0.0", historical["contract_id"])
        self.assertEqual("2.0.0", historical["schema_version"])

        question = "Which exact governed Project Beacon records are available?"
        decision = route_query(question)
        route = decision.to_artifact(
            request_id="wave1-compat-request",
            run_id="wave1-compat-run",
            trace_id="0123456789abcdef0123456789abcdef",
            decided_at="2026-08-06T00:00:00Z",
        )
        plan = build_query_plan(
            question=question,
            query_type="exact_lookup",
            request_id="wave1-compat-request",
            run_id="wave1-compat-run",
            current_trace_id="0123456789abcdef0123456789abcdef",
            l2_config=load_l2_config(),
            provider_egress_allowed=False,
            destination_decision=route,
            assurance_profile=None,
        )
        self.assertEqual("2.0.0", plan["schema_version"])
        self.assertNotIn("controller", plan)

    def test_invalid_wave1_fixtures_are_rejected_with_stable_codes(self) -> None:
        registry = SchemaRegistry(SCHEMA_ROOT)
        registry.load()
        expected_names = {
            "query-plan-v3-provider-egress.json",
            "query-plan-v3-action-broadening.json",
            "query-retrieval-attempt-graph-drift.json",
            "query-retrieval-attempt-provider-call.json",
            "query-plan-observation-false-progress.json",
            "query-plan-terminal-incomplete-coverage.json",
            "query-l1-control-policy-bounded-default.json",
            "query-l1-controller-eval-comparison-invariant-drift.json",
            "retrieval-release-evaluation-unbound-pair.json",
            "l1-wave-receipt-failed-command-pass.json",
        }
        for name in sorted(expected_names):
            with self.subTest(path=name), self.assertRaises(ValidationFailure):
                validate_artifact(load_json(INVALID_ROOT / name), registry)

    def test_wave1_controller_artifacts_are_not_emitted_by_runtime_yet(self) -> None:
        # Wave 3 owns the provider-free candidate compiler.  The production
        # query runtime must still remain on its historical v2 write path.
        runtime_text = (ROOT / "dual_pipeline" / "query.py").read_text(encoding="utf-8")
        self.assertNotIn("urn:nee:dual-pipeline:query-plan:3.0.0", runtime_text)
        self.assertNotIn("urn:nee:dual-pipeline:query-retrieval-attempt:1.0.0", runtime_text)

    def test_wave1_completion_receipt_binds_wave0_and_authorizes_only_wave2(self) -> None:
        registry = SchemaRegistry(SCHEMA_ROOT)
        registry.load()
        receipt_path = ROOT / "governance" / "dual_pipeline" / "l1_agentic" / "wave-01-completion.json"
        receipt = load_json(receipt_path)
        validate_artifact(receipt, registry)
        self.assertEqual("W1", receipt["wave_id"])
        self.assertEqual(
            "eb30e16a16b2d98e2836df9b51b8934d10c75b1d808b18964ce740a8f874029b",
            receipt["predecessor_receipt_digest"],
        )
        self.assertTrue(receipt["next_wave_authorized"])
        self.assertEqual(0, receipt["provider_call_count"])
        self.assertEqual(0, receipt["graph_mutation_count"])


if __name__ == "__main__":
    unittest.main()
