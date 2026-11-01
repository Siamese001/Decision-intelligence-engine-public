from dual_pipeline.transcript_qa_core.evaluator import evaluate_all_qa_gates
from dual_pipeline.transcript_qa_core.qa_swarm import SWARM_ROLES, QASwarm


def test_swarm_agent_roles():
    assert len(SWARM_ROLES) == 15, "Swarm must have exactly 15 defined agents"
    swarm = QASwarm()
    valid_f1 = "### [00:00:00] **Alice Smith:** Valid forensic dialogue turn content."
    valid_f2 = "## Executive Summary\nValid recap summary with more than thirty characters of detail.\n## Key Decisions\nAgreed on architecture.\n## Action Items\n- [ ] Review architecture (@owner: Alice Smith)"
    valid_f3 = '{"canonical": true, "entities": [], "assertions": []}'
    res = swarm.evaluate_all(valid_f1, valid_f2, valid_f3)
    assert res["swarm_passed"] is True
    assert len(res["agent_results"]) == 15


def test_evaluator_swarm_integration():
    res = evaluate_all_qa_gates(
        f1_text="### [00:00:00] **Alice Smith:** Valid forensic dialogue turn content.",
        f2_text="## Executive Summary\nValid executive summary content with more than thirty characters.\n## Key Decisions\nAgreed on architecture.\n## Action Items\n- [ ] Review architecture (@owner: Alice Smith)",
        f3_text='{"canonical": true, "entities": [], "assertions": []}',
        source_text="Source data",
        metadata={"is_notes": True},
    )
    assert "15_agent_swarm_review" in res
    assert res["15_agent_swarm_review"]["swarm_passed"] is True

