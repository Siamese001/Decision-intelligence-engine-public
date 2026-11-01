#!/usr/bin/env python3
"""Tests for the Unified Architecture for Skills, Agents, Sub-Agents, Hooks & Rules.

Verifies:
1. Complete repository governance conformance (AGENTS.md, rules, conformance map, hooks.json, git hooks).
2. Five Core Pillars schema definitions and characteristics.
3. Four Shared Foundations.
4. Sub-Agent contract-bound delegation and upstream return protocols.
5. Hook extension timing (PRE, POST, AROUND) and non-intrusive semantics.
6. End-to-End 7-Step Lifecycle flow progression and sequencing rules.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.unified_agent_architecture import (
    AgentDefinition,
    CrossCuttingConcern,
    ExecutionLifecycleTracker,
    HookDefinition,
    HookTiming,
    LifecycleStep,
    RuleDefinition,
    RuleType,
    SharedFoundations,
    SkillDefinition,
    SkillStatePosture,
    SubAgentDefinition,
    validate_delegation_contract,
    validate_hook_definition,
    validate_subagent_return,
    validate_unified_architecture_governance,
)

ROOT = Path(__file__).resolve().parents[1]


class UnifiedAgentArchitectureGovernanceTests(unittest.TestCase):
    def test_repository_unified_architecture_governance_passes(self) -> None:
        """Verify that AGENTS.md, rules, conformance map, hooks.json, and git hooks pass 100%."""
        errors = validate_unified_architecture_governance(ROOT)
        self.assertEqual(errors, [], f"Repository unified architecture governance failed: {errors}")

    def test_five_core_pillars_characteristics(self) -> None:
        """Verify instantiation and characteristics of the Five Core Architectural Pillars."""
        # 1. Skill (What work gets done)
        skill = SkillDefinition(
            id="skill_retrieve_docs",
            name="RetrieveDocuments",
            description="Hybrid BM25 and neural dense retrieval",
            state_posture=SkillStatePosture.STATELESS,
            model_agnostic=True,
            tool_agnostic=True,
            discoverable=True,
            composable=True,
        )
        self.assertEqual(skill.state_posture, SkillStatePosture.STATELESS)
        self.assertTrue(skill.model_agnostic)
        self.assertTrue(skill.discoverable)

        # 2. Agent (Who decides & orchestrates)
        agent = AgentDefinition(
            id="agent_planning_lead",
            role_name="PlanningAgent",
            description="High-level goal decomposition and wave orchestration",
            goal_driven=True,
            owns_strategy_and_control=True,
            maintains_context_and_state=True,
            can_delegate=True,
        )
        self.assertTrue(agent.goal_driven)
        self.assertTrue(agent.owns_strategy_and_control)
        self.assertTrue(agent.can_delegate)

        # 3. Sub-Agent (Specialized executors)
        sub_agent = SubAgentDefinition(
            id="subagent_pdf_extractor",
            parent_agent_id=agent.id,
            domain_focus="PDF and document extraction",
            lightweight_autonomy=True,
            can_have_sub_agents=True,
            returns_results_upstream=True,
            assigned_skills=[skill.id],
        )
        self.assertEqual(sub_agent.parent_agent_id, "agent_planning_lead")
        self.assertTrue(sub_agent.returns_results_upstream)
        self.assertTrue(sub_agent.can_have_sub_agents)

        # 4. Hook (When to extend)
        hook = HookDefinition(
            id="hook_latency_telemetry",
            event="around_action",
            timing=HookTiming.AROUND,
            cross_cutting_concern=CrossCuttingConcern.METRICS,
            non_intrusive=True,
            description="Measures action latency and flushes trace span",
        )
        self.assertEqual(hook.timing, HookTiming.AROUND)
        self.assertEqual(hook.cross_cutting_concern, CrossCuttingConcern.METRICS)
        self.assertTrue(hook.non_intrusive)

        # 5. Rule (What must be true)
        rule = RuleDefinition(
            id="rule_approval_before_mutation",
            rule_type=RuleType.GOVERNANCE_GUARDRAIL,
            title="Mutation approval required",
            description="Graph mutations strictly require prior bound approval.",
            enforce_pre_condition=True,
            enforce_post_condition=True,
        )
        self.assertEqual(rule.rule_type, RuleType.GOVERNANCE_GUARDRAIL)
        self.assertTrue(rule.enforce_pre_condition)

    def test_shared_foundations_specification(self) -> None:
        """Verify that all four shared foundations are modeled."""
        foundations = SharedFoundations(
            has_memory=True,
            has_knowledge_bases=True,
            has_models_and_tools=True,
            has_observability=True,
        )
        self.assertTrue(foundations.has_memory)
        self.assertTrue(foundations.has_knowledge_bases)
        self.assertTrue(foundations.has_models_and_tools)
        self.assertTrue(foundations.has_observability)

    def test_valid_delegation_contract_passes(self) -> None:
        """A complete, bounded delegation contract passes validation."""
        valid_contract = {
            "task_id": "task_extract_01",
            "parent_task_id": "task_root_orchestrator",
            "objective": "Extract facts from transcript segment",
            "scope_in": ["output/project_nexus/09-10-2026/01_transcript.md"],
            "scope_out": ["No graph mutations", "No network calls"],
            "allowed_tools": ["view_file", "replace_file_content"],
            "deliverable": {"format": "JSON", "type": "evidence_envelope"},
            "definition_of_done": "All statements validated against schema",
            "budget": {"max_turns": 4, "timeout_seconds": 120},
            "escalation_rule": "Escalate if speaker attribution confidence < 25%",
        }
        is_valid, errors = validate_delegation_contract(valid_contract)
        self.assertTrue(is_valid, f"Expected valid contract, got: {errors}")
        self.assertEqual(errors, [])

    def test_delegation_contract_missing_field_fails(self) -> None:
        """A delegation contract missing required fields fails validation."""
        invalid_contract = {
            "task_id": "task_bad_01",
            # missing parent_task_id, objective, etc.
            "budget": {"max_turns": 4},
        }
        is_valid, errors = validate_delegation_contract(invalid_contract)
        self.assertFalse(is_valid)
        self.assertTrue(any("DELEGATION_CONTRACT_MISSING_FIELD" in e for e in errors))

    def test_delegation_contract_unbounded_budget_fails(self) -> None:
        """A delegation contract with empty or unbounded budget fails."""
        invalid_budget_contract = {
            "task_id": "task_extract_02",
            "parent_task_id": "task_root",
            "objective": "Do some unbounded work",
            "scope_in": ["."],
            "scope_out": ["none"],
            "allowed_tools": ["all"],
            "deliverable": "text",
            "definition_of_done": "when finished",
            "budget": {},  # empty budget
            "escalation_rule": "never",
        }
        is_valid, errors = validate_delegation_contract(invalid_budget_contract)
        self.assertFalse(is_valid)
        self.assertTrue(any("DELEGATION_CONTRACT_UNBOUNDED_BUDGET" in e for e in errors))

    def test_subagent_return_validation(self) -> None:
        """Verify subagent return protocol validation."""
        valid_return = {
            "task_id": "task_extract_01",
            "status": "COMPLETE",
            "deliverable": {"facts_extracted": 12},
            "evidence_refs": ["01_transcript.md#L10-L40"],
            "metrics": {"turns_used": 2, "elapsed_seconds": 4.5},
        }
        is_valid, errors = validate_subagent_return(valid_return)
        self.assertTrue(is_valid, f"Expected valid return, got: {errors}")

        invalid_return = {
            "task_id": "task_extract_01",
            "status": "UNKNOWN_STATUS",  # invalid status
            "deliverable": {},
        }
        is_valid_inv, errors_inv = validate_subagent_return(invalid_return)
        self.assertFalse(is_valid_inv)
        self.assertTrue(any("SUBAGENT_RETURN_INVALID_STATUS" in e for e in errors_inv))

    def test_hook_extension_timing_validation(self) -> None:
        """Verify hook validation for timing and non-intrusiveness."""
        valid_hook = {
            "id": "test_pre_guard",
            "event": "pre_tool_call",
            "timing": "PRE",
            "cross_cutting_concern": "guardrails",
        }
        is_valid, errors = validate_hook_definition(valid_hook)
        self.assertTrue(is_valid, f"Expected valid hook, got: {errors}")

        intrusive_hook = {
            "id": "test_bad_hook",
            "timing": "AROUND",
            "modifies_core_logic": True,  # violates non-intrusive invariant
        }
        is_valid_bad, errors_bad = validate_hook_definition(intrusive_hook)
        self.assertFalse(is_valid_bad)
        self.assertTrue(any("HOOK_INTRUSIVE_VIOLATION" in e for e in errors_bad))

    def test_7_step_lifecycle_execution_flow_progression(self) -> None:
        """Verify sequential adherence to the 7-step execution lifecycle."""
        tracker = ExecutionLifecycleTracker()
        tracker.record_step(LifecycleStep.GOAL_AND_RULE_VERIFICATION)
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SUB_AGENT_DELEGATION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        tracker.record_step(LifecycleStep.HOOK_TRIGGERS)
        tracker.record_step(LifecycleStep.CONTINUOUS_RULE_VALIDATION)
        tracker.record_step(LifecycleStep.UPSTREAM_SYNTHESIS)

        is_valid, errors = tracker.validate_lifecycle_progression()
        self.assertTrue(is_valid, f"Full 7-step sequence should pass, got: {errors}")
        self.assertEqual(errors, [])

    def test_7_step_lifecycle_rejects_premature_skill_execution(self) -> None:
        """Executing skills before rule checking violates governance."""
        tracker = ExecutionLifecycleTracker()
        # Skipped rule verification, jumped straight to skill execution
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        tracker.record_step(LifecycleStep.UPSTREAM_SYNTHESIS)

        is_valid, errors = tracker.validate_lifecycle_progression()
        self.assertFalse(is_valid)
        self.assertTrue(any("LIFECYCLE_MISSING_STEP_1" in e for e in errors))

    def test_7_step_lifecycle_supports_valid_replan_feedback_loop(self) -> None:
        """Valid feedback loop flows from Step 4 back to Step 2 before re-executing."""
        tracker = ExecutionLifecycleTracker()
        tracker.record_step(LifecycleStep.GOAL_AND_RULE_VERIFICATION)
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        # Feedback loop: returns to planning (re-plan)
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        tracker.record_step(LifecycleStep.HOOK_TRIGGERS)
        tracker.record_step(LifecycleStep.CONTINUOUS_RULE_VALIDATION)
        tracker.record_step(LifecycleStep.UPSTREAM_SYNTHESIS)

        is_valid, errors = tracker.validate_lifecycle_progression()
        self.assertTrue(is_valid, f"Expected valid feedback loop, got: {errors}")

    def test_7_step_lifecycle_rejects_unplanned_reexecution(self) -> None:
        """Re-executing skills without intermediate re-planning violates feedback governance."""
        tracker = ExecutionLifecycleTracker()
        tracker.record_step(LifecycleStep.GOAL_AND_RULE_VERIFICATION)
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)  # Re-executed without re-planning!
        tracker.record_step(LifecycleStep.UPSTREAM_SYNTHESIS)

        is_valid, errors = tracker.validate_lifecycle_progression()
        self.assertFalse(is_valid)
        self.assertTrue(any("LIFECYCLE_FEEDBACK_LOOP_VIOLATION" in e for e in errors))

    def test_7_step_lifecycle_rejects_exhausted_feedback_iterations(self) -> None:
        """Exceeding max feedback loop iterations (2 replans / 3 total executions) fails."""
        tracker = ExecutionLifecycleTracker()
        tracker.record_step(LifecycleStep.GOAL_AND_RULE_VERIFICATION)
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        # Replan 1
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        # Replan 2
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        # Replan 3 (exceeds limit!)
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        tracker.record_step(LifecycleStep.UPSTREAM_SYNTHESIS)

        is_valid, errors = tracker.validate_lifecycle_progression()
        self.assertFalse(is_valid)
        self.assertTrue(any("LIFECYCLE_FEEDBACK_LOOP_EXHAUSTION" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
