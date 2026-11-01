#!/usr/bin/env python3
"""Unit tests verifying Wave 1 Agent Opportunity Baseline Catalog and Autonomy Contract Schema.

Validates:
1. JSON Schema integrity of contracts/dual_pipeline/schemas/agent-autonomy-contract.schema.json.
2. Valid and invalid contract enforcement under Draft 2020-12 rules.
3. Integrity, tier distribution, and invariants of docs/plans/agent_opportunity_matrix.json.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "contracts" / "dual_pipeline" / "schemas" / "agent-autonomy-contract.schema.json"
MATRIX_PATH = REPO_ROOT / "docs" / "plans" / "agent_opportunity_matrix.json"


class AgentOpportunityBaselineTests(unittest.TestCase):
    """Test suite ensuring agent baseline catalog and autonomy contract schemas satisfy all Wave 1 invariants."""

    def setUp(self) -> None:
        self.assertTrue(SCHEMA_PATH.is_file(), f"Missing schema at {SCHEMA_PATH}")
        self.assertTrue(MATRIX_PATH.is_file(), f"Missing catalog at {MATRIX_PATH}")

        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.schema = json.load(f)

        with open(MATRIX_PATH, "r", encoding="utf-8") as f:
            self.matrix = json.load(f)

        self.valid_contract = {
            "contract_id": "urn:nee:dual-pipeline:agent-autonomy-contract:1.0.0",
            "schema_version": "1.0.0",
            "task_id": "task-qa-audit-001",
            "parent_task_id": "agent-supervisor-main",
            "agent_role": "TranscriptQASwarmHealer",
            "objective": "Audit Output 01 transcript for speaker attribution balance and repair failing turns",
            "scope_in": ["output/project_alpha/01_transcript.md"],
            "scope_out": ["output/project_alpha/03_projection.json"],
            "allowed_tools": ["view_file", "repair_transcript_segment"],
            "deliverable": "output/project_alpha/01_transcript_healed.md",
            "definition_of_done": "Turn share balance <= 85% and all attendees verified",
            "reasoning_tier": "MID_TIER_REASONING",
            "reasoning_effort": "medium",
            "budget": {
                "timeout_seconds": 60,
                "max_tool_calls": 10,
                "max_retries": 2,
                "token_limit": 16000,
            },
            "escalation_rule": "Escalate to operator if confidence margin delta <= 30%",
            "ambiguity_threshold": 0.30,
            "created_at": "2026-10-14T12:00:00Z",
        }

    def test_schema_is_valid_draft202012(self) -> None:
        """Schema itself must be a valid Draft 2020-12 JSON Schema."""
        validator_cls = jsonschema.validators.validator_for(self.schema)
        validator_cls.check_schema(self.schema)
        self.assertEqual(self.schema["$id"], "urn:nee:dual-pipeline:agent-autonomy-contract:1.0.0")

    def test_valid_contract_passes(self) -> None:
        """A complete, bounded autonomy contract must pass validation without errors."""
        jsonschema.validate(instance=self.valid_contract, schema=self.schema)

    def test_missing_required_field_fails(self) -> None:
        """Omitting required contract fields must fail schema validation."""
        required_fields = [
            "contract_id",
            "schema_version",
            "task_id",
            "parent_task_id",
            "agent_role",
            "objective",
            "scope_in",
            "scope_out",
            "allowed_tools",
            "deliverable",
            "definition_of_done",
            "reasoning_tier",
            "budget",
            "escalation_rule",
        ]
        for field in required_fields:
            bad_contract = dict(self.valid_contract)
            del bad_contract[field]
            with self.assertRaises(jsonschema.ValidationError, msg=f"Missing field {field} did not raise"):
                jsonschema.validate(instance=bad_contract, schema=self.schema)

    def test_invalid_reasoning_tier_fails(self) -> None:
        """Unrecognized reasoning tiers must fail validation."""
        bad_contract = dict(self.valid_contract)
        bad_contract["reasoning_tier"] = "UNRECOGNIZED_TIER"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=bad_contract, schema=self.schema)

    def test_empty_budget_fails(self) -> None:
        """A contract with an empty budget object must fail schema validation."""
        bad_contract = dict(self.valid_contract)
        bad_contract["budget"] = {}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=bad_contract, schema=self.schema)

    def test_catalog_metadata_invariants(self) -> None:
        """Catalog must contain metadata declaring 65+ active agents and 10 candidates across 10 tiers."""
        meta = self.matrix["metadata"]
        self.assertGreaterEqual(meta["total_active_agents"], 65)
        self.assertEqual(meta["total_candidate_agents"], 10)
        self.assertEqual(meta["total_architectural_tiers"], 10)

    def test_catalog_active_agents_integrity(self) -> None:
        """All active agents must have non-empty attributes and zero duplicate names."""
        agents = self.matrix["active_agents"]
        self.assertGreaterEqual(len(agents), 65)

        names = set()
        for a in agents:
            name = a["name"]
            self.assertNotIn(name, names, f"Duplicate agent name: {name}")
            names.add(name)

            self.assertTrue(a["tier"].startswith("Tier "), f"Invalid tier for {name}: {a['tier']}")
            self.assertTrue(len(a["source_file"]) > 0, f"Empty source_file for {name}")
            self.assertTrue(len(a["role_description"]) > 0, f"Empty description for {name}")
            self.assertIn(
                a["current_reasoning_tier"],
                ["FRONTIER_HIGH_REASONING", "MID_TIER_REASONING", "STANDARD_FAST", "LOCAL_METAL"],
            )
            self.assertIn(
                a["target_reasoning_tier"],
                ["FRONTIER_HIGH_REASONING", "MID_TIER_REASONING", "STANDARD_FAST", "LOCAL_METAL"],
            )
            self.assertIn(
                a["autonomy_scope"],
                ["within_run", "static", "durable_learning", "simulated"],
            )
            self.assertIsInstance(a["tools"], list)

    def test_catalog_candidates_prioritization(self) -> None:
        """All 10 candidates must be ranked 1 to 10 with verified value/effort calculations."""
        candidates = self.matrix["candidate_agents"]
        self.assertEqual(len(candidates), 10)

        ranks = [c["priority_rank"] for c in candidates]
        self.assertEqual(ranks, list(range(1, 11)), "Candidate ranks must be consecutively 1..10")

        for c in candidates:
            expected_ratio = round(c["value_score"] / c["effort_score"], 2)
            self.assertAlmostEqual(
                c["value_effort_ratio"],
                expected_ratio,
                places=2,
                msg=f"Ratio mismatch for {c['id']}",
            )
            self.assertTrue(len(c["safety_guardrail"]) > 0, f"Empty safety_guardrail for {c['id']}")


if __name__ == "__main__":
    unittest.main()
