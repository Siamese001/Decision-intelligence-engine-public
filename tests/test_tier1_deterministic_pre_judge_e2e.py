"""End-to-End tests for Tier 1 Deterministic Pre-Judge Gate.

Verifies that Tier 1 deterministic gates evaluate structural, schema, and format invariants
with zero LLM tokens and block defective deliverables prior to frontier LLM dispatch.
"""
from __future__ import annotations

import json
import pytest

from dual_pipeline.llm_judge_core.heuristic_pre_judge import (
    Tier1GateReceipt,
    run_tier1_deterministic_pre_judge,
)
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge


def test_tier1_deterministic_pre_judge_output1_valid():
    valid_f1 = (
        "# Engineering Sync\n\n"
        "**Date**: 10/16/2026\n"
        "**Duration**: ~3m\n\n"
        "### Attendees\n"
        "- Alice (Engineering)\n"
        "- Bob (Product)\n\n"
        "### Forensic Transcript\n"
        "[00:00:01] Alice: Let us align on the new search index deployment and verify that all production benchmarks satisfy the latency floor before we proceed.\n"
        "[00:00:45] Bob: Agreed, the latency benchmark looks solid across the board and memory utilization is within acceptable historical boundaries for staging.\n"
        "[00:01:30] Alice: Excellent, let us ratify this deployment and notify the operations team accordingly.\n\n"
        "### Ambiguity Lock Register\n"
        "| Turn ID | Target Turn | Resolution Status | Verified Speaker | Confidence | Context Hash |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| ALR-01 | [00:00:01] | RESOLVED | Alice | HIGH (95%) | a1b2c3d4 |\n"
        "| ALR-02 | [00:00:45] | RESOLVED | Bob | HIGH (95%) | e5f6a7b8 |\n"
        "| ALR-03 | [00:01:30] | RESOLVED | Alice | HIGH (90%) | c9d0e1f2 |\n\n"
        "### Attribution Summary\n"
        "| Speaker | Organization | Turns | Share |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| Alice | Engineering | 2 | 66.7% |\n"
        "| Bob | Product | 1 | 33.3% |\n"
    )
    receipt = run_tier1_deterministic_pre_judge("output_1", valid_f1, reference_text=valid_f1)
    assert isinstance(receipt, Tier1GateReceipt)
    assert receipt.tokens_spent == 0
    assert receipt.passed is True
    assert receipt.pre_check_tier == "TIER1_FORENSIC_PASS"
    assert len(receipt.violations) == 0


def test_tier1_deterministic_pre_judge_output3_action_updates_leak():
    leaky_f3 = json.dumps(
        {
            "schema_version": "5.7",
            "entity_upserts": [
                {
                    "entity_id": "system:search_index",
                    "entity_type": "SYSTEM",
                    "name": "Search Index",
                    "description": "Core search engine",
                    "observed_at": "10/16/2026",
                }
            ],
            "relationship_signals": [
                {
                    "from_id": "system:search_index",
                    "to_id": "org:engineering",
                    "relationship": "OWNED_BY",
                    "observed_at": "10/16/2026",
                }
            ],
            "graph_mutations": {
                "action_updates": [
                    {
                        "action_id": "action:deploy_index",
                        "title": "Deploy index to staging",
                        "owner": "Alice",
                    }
                ]
            },
        }
    )
    receipt = run_tier1_deterministic_pre_judge("output_3", leaky_f3, reference_text="mock source")
    assert isinstance(receipt, Tier1GateReceipt)
    assert receipt.tokens_spent == 0
    assert receipt.passed is False
    assert receipt.pre_check_tier == "TIER1_GRAPH_BLOCKED"
    assert any("action_updates" in v.message for v in receipt.violations)


def test_orchestrator_evaluate_deliverable_blocks_on_tier1():
    judge = DecomposedMultiJudge(model="mock-tier1")
    malformed_f3 = "{ malformed json..."
    res = judge.evaluate_deliverable("output_3", malformed_f3, reference_text="mock source", mock=False)
    assert res["status"] == "FAIL"
    assert res["verdict"] == "TIER_1_BLOCKED"
    assert res["composite_score"] == 0.0
    assert "tier1_gate_receipt" in res
    assert res["tier1_gate_receipt"]["passed"] is False
    assert res["tier1_gate_receipt"]["tokens_spent"] == 0
