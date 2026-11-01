"""End-to-end integration test for post-runtime Google query expansion and evaluation loop."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.l1_feedback_loop import L1FeedbackStateMachine
from dual_pipeline.l1_semantic_evaluator import L1SemanticSufficiencyEvaluator
from dual_pipeline.query_expansion_google import GoogleQueryExpansionProvider


def test_query_expansion_post_runtime_lifecycle_e2e(tmp_path: Path):
    """E2E lifecycle: Sealed L2 observation -> Sufficiency evaluation -> Query expansion -> Re-plan payload."""
    expansion_provider = GoogleQueryExpansionProvider(similarity_threshold=0.70)

    simulated_variants = json.dumps({
        "expansions": [
            "Customer AI Bill-E self service architecture and compliance",
            "APEX.com Bill-E high bill inquiry PSC regulatory deflection",
            "Customer self-service high-bill explanation pod standards",
        ]
    })

    with patch(
        "dual_pipeline.query_expansion_google.call_gemini_generate_content",
        return_value=simulated_variants,
    ):
        evaluator = L1SemanticSufficiencyEvaluator(
            mock_mode=True,
            query_expansion_provider=expansion_provider,
        )

        plan = {
            "query_type": "opportunity_nba",
            "subquestions": ["What is the strategic objective of Bill-E under Customer AI?"],
            "evidence_requirements": ["Initial query evidence requirement"],
        }
        incomplete_answer = {
            "summary": "Bill-E is an AI project at APEX.",
        }

        # Step 1: Evaluate sufficiency
        eval_result = evaluator.evaluate_sufficiency(
            question="What is the strategic objective of Bill-E under Customer AI?",
            plan=plan,
            answer=incomplete_answer,
            sealed_l2={"status": "completed"},
        )

        assert eval_result.is_sufficient is False
        assert eval_result.replan_directive is not None

        directive = eval_result.replan_directive
        assert len(directive["suggested_query_expansion"]) >= 1

        # Step 2: Feed replan directive into feedback state machine
        fsm = L1FeedbackStateMachine(max_iterations=2)
        iter_num = fsm.start_cycle()
        assert iter_num == 0

        # Step 3: Adapt query plan with suggested expansions
        adapted_plan = L1FeedbackStateMachine.apply_replan_to_plan(plan, directive)
        assert len(adapted_plan["evidence_requirements"]) > len(plan["evidence_requirements"])
        assert any("Bill-E" in req for req in adapted_plan["evidence_requirements"])
