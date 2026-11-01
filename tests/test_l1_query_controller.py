#!/usr/bin/env python3
"""Wave 6 deterministic controller/fusion tests."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.l1_control import BudgetState, load_policy
from dual_pipeline.query_l1_controller import (
    bounded_telemetry_payload,
    build_bounded_terminal,
    build_observation_one,
    feedback_eligible,
    merge_retrieval_results,
    validate_bounded_telemetry,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 64


def _policy():
    return load_policy(json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8")))


def _row(record_id: str, lane: str, *, rank: int = 1, score: float = 1.0):
    return {
        "record_id": record_id,
        "lane": lane,
        "fused_rank": rank,
        "fused_score": score,
        "dense_score": score,
        "sparse_score": 0.0,
        "colbert_score": score,
        "record": {"record_id": record_id, "title": record_id},
    }


class L1QueryControllerTests(unittest.TestCase):
    def test_feedback_requires_bounded_nee_refinable_gap_without_conflict(self) -> None:
        report = {
            "requirement_outcomes": [
                {"requirement_id": "r1", "outcome": "explicit_gap", "reason_code": "RETRIEVAL_COVERAGE_MISSING"}
            ]
        }
        graph = {"reason_codes": ["NO_AUTHORIZED_GRAPH_RECORDS"]}
        self.assertTrue(feedback_eligible(mode="bounded", destination="NEE_DECISION_INTELLIGENCE", graph_fit_decision=graph, evidence_report=report))
        self.assertFalse(feedback_eligible(mode="shadow", destination="NEE_DECISION_INTELLIGENCE", graph_fit_decision=graph, evidence_report=report))
        conflict = {"requirement_outcomes": [{"requirement_id": "r1", "outcome": "conflict"}]}
        self.assertFalse(feedback_eligible(mode="bounded", destination="NEE_DECISION_INTELLIGENCE", graph_fit_decision=graph, evidence_report=conflict))

    def test_lane_merge_deduplicates_retains_provenance_and_caps_without_blending(self) -> None:
        initial = {
            "allowed_lanes": ["approved", "candidate"],
            "lanes": {
                "approved": {"results": [_row("shared", "approved", rank=2), _row("approved-old", "approved", rank=3)]},
                "candidate": {"results": [_row("candidate", "candidate", rank=1)]},
            },
        }
        action = {
            "lanes": {
                "approved": {"results": [_row("shared", "approved", rank=1, score=2.0), _row("approved-new", "approved", rank=2)]},
                "candidate": {"results": [_row("candidate-new", "candidate", rank=1)]},
            }
        }
        merged = merge_retrieval_results(initial_retrieval=initial, action_retrievals=[action], allowed_lanes=["approved", "candidate"], max_records=4)
        self.assertEqual(4, merged["final_record_count"])
        self.assertEqual(["shared", "approved-new"], [row["record_id"] for row in merged["lanes"]["approved"]["results"]])
        self.assertEqual(["candidate", "candidate-new"], [row["record_id"] for row in merged["lanes"]["candidate"]["results"]])
        self.assertGreaterEqual(len(merged["lanes"]["approved"]["results"][0]["l1_provenance"]), 2)
        self.assertTrue(all(row["lane"] == "approved" for row in merged["lanes"]["approved"]["results"]))

    def test_observation_and_terminal_are_contract_valid_and_no_progress_stops_l2d(self) -> None:
        plan = {"canonical_digest": {"value": "1" * 64}}
        manifest = {"canonical_digest": {"value": "2" * 64}}
        prior = {"canonical_digest": {"value": "3" * 64}, "requirement_outcomes": [{"requirement_id": "r1", "outcome": "explicit_gap"}]}
        final = {"canonical_digest": {"value": "4" * 64}, "requirement_outcomes": [{"requirement_id": "r1", "outcome": "explicit_gap"}], "selected_record_ids_by_lane": {"approved": [], "candidate": [], "external_public": []}}
        action = {"canonical_digest": {"value": "5" * 64}}
        observation = build_observation_one(plan=plan, coverage_manifest=manifest, evidence_report=final, action_attempt=action, prior_report=prior, graph_sha256=SHA)
        terminal = build_bounded_terminal(plan=plan, coverage_manifest=manifest, final_evidence_report=final, attempts=[action], observation_one=observation, budget=BudgetState(actions_used=1), graph_sha256=SHA)
        validate_artifact(observation)
        validate_artifact(terminal)
        self.assertEqual("NO_PROGRESS", terminal["terminal_reason"])
        self.assertEqual("NONE", terminal["permitted_next_stage"])

    def test_bounded_policy_must_be_explicit_candidate_policy(self) -> None:
        policy = _policy()
        self.assertEqual("off", policy.mode)
        self.assertEqual("bounded", replace(policy, mode="bounded").mode)

    def test_bounded_telemetry_is_content_safe_and_closed(self) -> None:
        terminal = {
            "canonical_digest": {"value": "6" * 64},
            "terminal_reason": "NO_PROGRESS",
            "coverage_complete": False,
            "final_evidence_report_digest": "7" * 64,
            "attempt_chain_digest": "8" * 64,
            "total_work": {"attempts": 2, "actions": 1},
            "graph_sha256_before": "9" * 64,
            "graph_sha256_after": "9" * 64,
        }
        payload = bounded_telemetry_payload(
            plan_digest="1" * 64,
            coverage_manifest_digest="2" * 64,
            terminal=terminal,
            budget=BudgetState(actions_used=1),
            target_count=1,
            newly_covered_count=0,
        )
        validate_bounded_telemetry(payload)
        self.assertNotIn("question", payload)
        unsafe = dict(payload) | {"raw_response": "do not emit"}
        with self.assertRaisesRegex(ValueError, "content-bearing"):
            validate_bounded_telemetry(unsafe)


if __name__ == "__main__":
    unittest.main()
