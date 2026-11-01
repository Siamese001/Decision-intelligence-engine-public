#!/usr/bin/env python3
"""Assurance-profile selection and native v2 plan tests."""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json
from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query import build_query_plan
from dual_pipeline.query_assurance import select_assurance_profile

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid"


class QueryAssuranceTests(unittest.TestCase):
    def test_long_operational_work_never_gets_assurance(self) -> None:
        profile = select_assurance_profile(
            destination="OPERATIONAL_WORKFLOW_HANDOFF",
            query_type="exact_lookup", materiality="high",
            delivery_profile="client_executive", artifact_type="answer",
            question="Rebuild the workbook " * 100,
        )
        self.assertIsNone(profile)

    def test_client_architecture_is_enhanced(self) -> None:
        profile = select_assurance_profile(
            destination="NEE_DECISION_INTELLIGENCE",
            query_type="cross_workstream_synthesis", materiality="high",
            delivery_profile="client_executive", artifact_type="architecture_deck",
            question="Recommend the future-state A2A architecture.",
        )
        self.assertEqual("enhanced", profile)

    def test_runtime_builds_v2_plan_without_a_legacy_adapter(self) -> None:
        destination = read_json(VALID / "query-destination-decision.json")
        plan = build_query_plan(
            question="Recommend the future-state A2A architecture.",
            query_type="cross_workstream_synthesis",
            request_id=destination["request_id"],
            run_id=destination["run_id"],
            current_trace_id=destination["trace_id"],
            l2_config=load_l2_config(),
            provider_egress_allowed=True,
            destination_decision=destination,
            assurance_profile="enhanced",
        )
        validate_artifact(plan)
        self.assertEqual("2.0.0", plan["schema_version"])
        self.assertEqual("enhanced", plan["assurance_profile"])
        self.assertNotIn("query_complexity", plan)
        self.assertNotIn("preferred_model_tier", plan)
        self.assertNotIn("com.partner_advisory.nee.legacy-read-adapter", plan["extensions"])


if __name__ == "__main__":
    unittest.main()
