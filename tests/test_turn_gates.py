#!/usr/bin/env python3
"""Unit tests for tools.validate_turn_gates.

Verifies mechanical pre-turn and post-turn gate enforcement:
1. Pre-turn gate evaluation across inventory, policy, hooks, and conformance map.
2. HITL 25% ambiguity margin gating (autonomous proceed vs. atomic surfacing).
3. GraphDB mutation breakdown completeness (10 required SQLite tables + hash transition).
4. Transcript synthesis output hierarchy validation (MM-DD-YYYY project subdirectories).
5. Post-turn gate integration and violation rejection.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.validate_turn_gates import (
    evaluate_hitl_ambiguity,
    validate_agent_operating_contract,
    validate_mutation_breakdown_table,
    validate_post_turn_gate,
    validate_pre_turn_gate,
    validate_scratchpad_transparency,
)

ROOT = Path(__file__).resolve().parents[1]


class TurnGatesValidationTests(unittest.TestCase):
    def test_pre_turn_gate_passes_on_current_repository(self) -> None:
        """Verify that the repository satisfies all pre-turn gate checks."""
        result = validate_pre_turn_gate(ROOT)
        self.assertEqual(
            result["status"],
            "PASS",
            f"Pre-turn gate failed with issues: {result['issues']}",
        )
        checks = result["checks"]
        self.assertEqual(checks["runtime_inventory"]["status"], "PASS")
        self.assertEqual(checks["classification_policy"]["status"], "PASS")
        self.assertEqual(checks["hooks_configuration"]["status"], "PASS")
        self.assertEqual(checks["conformance_map"]["status"], "PASS")

    def test_hitl_ambiguity_autonomous_proceed_when_delta_ge_25(self) -> None:
        """When delta >= 25%, the gate must select PROCEED_AUTONOMOUSLY."""
        options = [
            {"label": "Option A (Recommended)", "confidence_score": 0.85},
            {"label": "Option B", "confidence_score": 0.55},
        ]
        # Delta = 0.85 - 0.55 = 0.30 >= 0.25
        eval_result = evaluate_hitl_ambiguity(options)
        self.assertFalse(eval_result["requires_hitl"])
        self.assertEqual(eval_result["action"], "PROCEED_AUTONOMOUSLY")
        self.assertAlmostEqual(eval_result["margin"], 0.30)
        self.assertEqual(eval_result["top_option"]["label"], "Option A (Recommended)")

    def test_hitl_ambiguity_surface_atomic_when_delta_lt_25(self) -> None:
        """When delta < 25%, the gate must require SURFACE_HITL_ATOMIC."""
        options = [
            {"label": "Option A", "confidence_score": 0.70},
            {"label": "Option B", "confidence_score": 0.60},
        ]
        # Delta = 0.70 - 0.60 = 0.10 < 0.25
        eval_result = evaluate_hitl_ambiguity(options)
        self.assertTrue(eval_result["requires_hitl"])
        self.assertEqual(eval_result["action"], "SURFACE_HITL_ATOMIC")
        self.assertAlmostEqual(eval_result["margin"], 0.10)

    def test_mutation_breakdown_table_passes_when_all_tables_present(self) -> None:
        """Breakdown validation passes when all 10 SQLite tables and hash transition are present."""
        sample_markdown = """
### GraphDB Mutation Summary
| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |
| :--- | :--- | :--- | :--- | :--- |
| `node` | 100 | 105 | +5 | Added 5 nodes |
| `source` | 10 | 11 | +1 | Added 1 source |
| `source_version` | 10 | 11 | +1 | Added 1 version |
| `extraction_run` | 12 | 13 | +1 | Added run |
| `evidence_item` | 50 | 55 | +5 | Added evidence |
| `assertion` | 80 | 85 | +5 | Added assertions |
| `assertion_evidence` | 80 | 85 | +5 | Linked evidence |
| `search_document` | 100 | 105 | +5 | Indexed docs |
| `search_fts` | 100 | 105 | +5 | Updated FTS |
| `audit_event` | 200 | 205 | +5 | Logged audits |

Transition: graph_sha256 changed from abc to def.
"""
        eval_result = validate_mutation_breakdown_table(sample_markdown)
        self.assertEqual(eval_result["status"], "PASS")
        self.assertEqual(eval_result["missing_tables"], [])
        self.assertTrue(eval_result["has_hash_transition"])

    def test_mutation_breakdown_table_fails_when_table_missing(self) -> None:
        """Breakdown validation fails if any of the 10 required SQLite tables are missing."""
        sample_markdown = """
| SQLite Table | Baseline | Activated | Delta |
| `node` | 100 | 105 | +5 |
| `source` | 10 | 11 | +1 |
Transition: graph_sha256 updated.
"""
        eval_result = validate_mutation_breakdown_table(sample_markdown)
        self.assertEqual(eval_result["status"], "FAIL")
        self.assertIn("assertion", eval_result["missing_tables"])
        self.assertIn("search_fts", eval_result["missing_tables"])

    def test_post_turn_gate_rejects_unnecessary_hitl_interruption(self) -> None:
        """Post-turn gate fails if a turn surfaced HITL when delta was >= 25%."""
        options = [
            {"label": "Option A (Strong)", "confidence_score": 0.90},
            {"label": "Option B (Weak)", "confidence_score": 0.50},
        ]
        response_with_unnecessary_hitl = "### HITL Decision: Please choose Option A or Option B."
        result = validate_post_turn_gate(
            repo_root=ROOT,
            candidate_response=response_with_unnecessary_hitl,
            candidate_options=options,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("Must proceed autonomously" in issue for issue in result["issues"]))

    def test_post_turn_gate_passes_on_compliant_autonomous_resolution(self) -> None:
        """Post-turn gate passes when delta >= 25% and agent proceeded autonomously."""
        options = [
            {"label": "Option A (Strong)", "confidence_score": 0.90},
            {"label": "Option B (Weak)", "confidence_score": 0.50},
        ]
        response_autonomous = "Proceeded autonomously with Option A under audit receipt."
        result = validate_post_turn_gate(
            repo_root=ROOT,
            candidate_response=response_autonomous,
            candidate_options=options,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["issues"], [])

    def test_direct_answer_routes_reject_preambles_and_enforce_visible_limit(self) -> None:
        query = "Lillian Le is the strongest fit, based on the verified Project Beacon lead assignment."
        good = validate_post_turn_gate(
            repo_root=ROOT, candidate_response=query, require_scratchpad=True,
            response_route="query", word_limit=200,
        )
        self.assertEqual(good["checks"]["direct_answer"]["status"], "PASS")
        self.assertNotIn("scratchpad_transparency", good["checks"])

        bad = validate_post_turn_gate(
            repo_root=ROOT,
            candidate_response="### Agent Reasoning Scratchpad\n" + query,
            response_route="query", word_limit=200,
        )
        self.assertEqual(bad["checks"]["direct_answer"]["status"], "FAIL")

        too_long = validate_post_turn_gate(
            repo_root=ROOT, candidate_response="word " * 200,
            response_route="query", word_limit=200,
        )
        self.assertEqual(too_long["checks"]["direct_answer"]["status"], "FAIL")

        copilot = validate_post_turn_gate(
            repo_root=ROOT, candidate_response="```text\nCould we clarify the SCADA risk?\n```",
            require_scratchpad=True, response_route="copilot",
        )
        self.assertEqual(copilot["checks"]["direct_answer"]["status"], "PASS")

        legacy_copilot = validate_post_turn_gate(
            repo_root=ROOT,
            candidate_response="```text\nCould we clarify the SCADA risk?\n```\n{\"status\": \"PASS\"}",
            response_route="copilot",
        )
        self.assertEqual(legacy_copilot["checks"]["direct_answer"]["status"], "FAIL")

    def test_agent_operating_contract_concise_memory_bound(self) -> None:
        """Verify AGENTS.md adheres to concise memory guidance (<200 lines) and contains core invariants."""
        result = validate_agent_operating_contract(ROOT)
        self.assertEqual(result["status"], "PASS", f"Operating contract issues: {result['issues']}")
        self.assertLessEqual(result["line_count"], 200, "AGENTS.md must stay under 200 lines per memory guidance.")
        self.assertGreater(result["line_count"], 50)

    def test_scratchpad_transparency_validation_passes_on_compliant_response(self) -> None:
        """Verify that a compliant scratchpad response with truthful unavailable state passes turn gate validation."""
        sample_response = """
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: Unavailable / Unverified (Host Metadata Inaccessible)
- **Invocation Role**: Governance SSOT Integration
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Enforce strict prompt contract hashing.
  - *Hypotheses & Evaluation*: Evaluated PromptRegistry caching.
  - *Risk & Failure Modes*: Checked AGENTS.md line count boundary.
- **Reasoning Steps**:
  1. Register prompt contract in registry.
  2. Validate test suite execution.
- **API & Tool Invocations**:
  - Tool: `run_command` | Args: `pytest` | Status: `SUCCESS` | Outcome: `All tests passed`

Response content delivered cleanly.
"""
        eval_result = validate_scratchpad_transparency(sample_response)
        self.assertEqual(eval_result["status"], "PASS")
        self.assertTrue(eval_result["has_header"])
        self.assertTrue(eval_result["has_model_telemetry"])
        self.assertTrue(eval_result["has_cognitive_thinking"])
        self.assertTrue(eval_result["has_reasoning_steps"])
        self.assertTrue(eval_result["has_tool_invocations"])
        self.assertEqual(eval_result["issues"], [])

    def test_scratchpad_passes_with_matching_execution_evidence(self) -> None:
        """Verify that a declared approved model corroborated by execution evidence passes."""
        sample_response = """
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: gemini-3.8-flash | Google GenAI | Tier: Medium
- **Invocation Role**: Pipeline Execution
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Tested evidence binding.
  - *Hypotheses & Evaluation*: Matched dispatched model.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. Dispatch call.
- **API & Tool Invocations**:
  - Tool: `run_command` | Args: `test` | Status: `SUCCESS` | Outcome: `OK`
"""
        evidence = {
            "dispatched_model": "gemini-3.8-flash",
            "provider": "google",
            "observed_model": "gemini-3.8-flash",
        }
        eval_result = validate_scratchpad_transparency(sample_response, evidence=evidence)
        self.assertEqual(eval_result["status"], "PASS")
        self.assertEqual(eval_result["issues"], [])

    def test_scratchpad_rejects_unverified_model_without_evidence_when_metadata_inaccessible(self) -> None:
        """Verify that claiming a verified model without evidence or host metadata fails."""
        sample_response = """
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: gemini-3.8-flash | Google GenAI
- **Invocation Role**: Pipeline Execution
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Tested unverified claim.
  - *Hypotheses & Evaluation*: None.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. None.
- **API & Tool Invocations**:
  - Tool: `run_command` | Args: `test` | Status: `SUCCESS` | Outcome: `OK`
"""
        eval_result = validate_scratchpad_transparency(sample_response)
        self.assertEqual(eval_result["status"], "FAIL")
        self.assertTrue(any("unverified" in issue for issue in eval_result["issues"]))

    def test_scratchpad_rejects_approved_but_wrong_model(self) -> None:
        """Verify that declaring an approved model that differs from execution evidence fails."""
        sample_response = """
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: gemini-3.5-flash-lite | Google GenAI
- **Invocation Role**: Pipeline Execution
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Tested approved-but-wrong.
  - *Hypotheses & Evaluation*: None.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. None.
- **API & Tool Invocations**:
  - Tool: `run_command` | Args: `test` | Status: `SUCCESS` | Outcome: `OK`
"""
        evidence = {
            "dispatched_model": "gemini-3.8-flash",
            "provider": "google",
            "observed_model": "gemini-3.8-flash",
        }
        eval_result = validate_scratchpad_transparency(sample_response, evidence=evidence)
        self.assertEqual(eval_result["status"], "FAIL")
        self.assertTrue(any("Approved-but-wrong identity violation" in issue for issue in eval_result["issues"]))

    def test_scratchpad_rejects_template_placeholder(self) -> None:
        """Verify that unrendered template placeholders fail validation."""
        sample_response = """
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Active Model & Provider**: [Active Model Identifier from Host Metadata, or "Unavailable / Unverified (Host Metadata Inaccessible)"]
- **Invocation Role**: Pipeline Execution
- **Cognitive Breakdown & Thinking**:
  - *Context & Constraints*: Tested placeholder.
  - *Hypotheses & Evaluation*: None.
  - *Risk & Failure Modes*: None.
- **Reasoning Steps**:
  1. None.
- **API & Tool Invocations**:
  - Tool: `run_command` | Args: `test` | Status: `SUCCESS` | Outcome: `OK`
"""
        eval_result = validate_scratchpad_transparency(sample_response)
        self.assertEqual(eval_result["status"], "FAIL")
        self.assertTrue(any("unrendered template placeholder" in issue for issue in eval_result["issues"]))

    def test_scratchpad_transparency_validation_fails_when_pillars_missing(self) -> None:
        """Verify that omitting required pillars fails post-turn scratchpad validation."""
        incomplete_response = """
### 🧠 Agent Reasoning Scratchpad & Execution Telemetry
- **Reasoning Steps**:
  1. Do something without thinking or model telemetry.
"""
        eval_result = validate_scratchpad_transparency(incomplete_response)
        self.assertEqual(eval_result["status"], "FAIL")
        self.assertFalse(eval_result["has_model_telemetry"])
        self.assertFalse(eval_result["has_cognitive_thinking"])
        self.assertFalse(eval_result["has_tool_invocations"])
        self.assertTrue(any("Pillar 1" in issue for issue in eval_result["issues"]))
        self.assertTrue(any("Pillar 2" in issue for issue in eval_result["issues"]))
        self.assertTrue(any("Pillar 4" in issue for issue in eval_result["issues"]))

    def test_post_turn_gate_enforces_scratchpad_compliance(self) -> None:
        """Post-turn gate enforces scratchpad transparency when turn includes scratchpad or requires it."""
        bad_response = "### 🧠 Agent Reasoning Scratchpad\nJust some text without required pillars."
        result = validate_post_turn_gate(
            repo_root=ROOT,
            candidate_response=bad_response,
            require_scratchpad=True,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("scratchpad_transparency", result["checks"])
        self.assertEqual(result["checks"]["scratchpad_transparency"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
