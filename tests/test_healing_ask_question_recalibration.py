"""Wave 3 Unit & Integration Tests: ask_user_question Required Format & Post-Learning Confidence Recalibration."""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.healing_policy import (
    AMBIGUITY_MARGIN_THRESHOLD,
    MIN_TOP_CONFIDENCE_FLOOR,
    HealingActionClassification,
    arbitrate_decision_margin,
    load_healing_policy_config,
)
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore


def test_arbitrate_decision_margin_emits_validated_ask_user_question_payload(tmp_path: Path) -> None:
    """Verify ambiguous healing decisions emit a 100% contract-valid ask_user_question payload."""
    policy_cfg = load_healing_policy_config()
    assert policy_cfg["healing_governance"]["ambiguity_margin_threshold"] == 0.20
    assert AMBIGUITY_MARGIN_THRESHOLD == 0.20
    assert MIN_TOP_CONFIDENCE_FLOOR == 0.65

    db_path = tmp_path / "learning.sqlite"
    dec = arbitrate_decision_margin(
        "dec_ambiguous_repair_01",
        [("STRIP_WHITESPACE_OR_PAD", 0.68), ("NORMALIZE_DATE_FORMAT", 0.55)],
        question="Which repair strategy should be applied to the transcript header?",
        category="healing_policy",
        healing_db_path=db_path,
    )
    assert dec.hitl_required is True
    assert dec.action == HealingActionClassification.ESCALATE_HITL
    assert dec.question_payload is not None
    assert dec.ask_user_question_tool_call is not None

    questions_list = dec.ask_user_question_tool_call["questions"]
    assert len(questions_list) == 1
    q_item = questions_list[0]
    assert q_item["is_multi_select"] is False
    assert len(q_item["options"]) == 2
    assert q_item["options"][0].startswith("(Recommended) ")
    assert "(Confidence:" in q_item["options"][0]
    assert "Tier:" in q_item["options"][0]

    val_res = validate_question_payload(
        question=dec.question_payload["question"],
        options=dec.question_payload["options"],
        metadata=dec.question_payload["metadata"],
    )
    assert val_res.is_valid is True, f"Validation failed: {val_res.errors}"


def test_post_learning_recalibration_shifts_confidences_and_reranks_recommended(tmp_path: Path) -> None:
    """Verify offline system learning recalibrates confidences and re-ranks (Recommended) in QuestionBuilder, healing_policy, and HitlArbiterAgent."""
    runtime_root = tmp_path / ".local"
    ledger_dir = runtime_root / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    db_path = ledger_dir / "learning.sqlite"
    hitl_ledger_path = ledger_dir / "hitl_decision_ledger.json"
    proposals_dir = tmp_path / "proposals"
    learned_store_path = runtime_root / "learned_hitl_option_store.json"

    h_store = HealingDecisionStore(db_path=db_path)
    # Record 5 successful repairs for Surgical appendix repair and 4 failures for Full section regeneration
    for i in range(5):
        h_store.record_decision({
            "run_id": f"run-succ-{i}",
            "subsystem": "surgical_remediator",
            "component": "SurgicalRemediator",
            "scope": "output_2",
            "repair_class": "Surgical appendix repair",
            "action_taken": "Surgically remediated appendix",
            "outcome_status": "HEALED",
            "confidence_top": 0.92,
            "confidence_second": 0.08,
            "ambiguity_margin": 0.84,
        })
    for i in range(4):
        h_store.record_decision({
            "run_id": f"run-fail-{i}",
            "subsystem": "surgical_remediator",
            "component": "SurgicalRemediator",
            "scope": "output_2",
            "repair_class": "Full section regeneration",
            "action_taken": "Attempted full section regeneration",
            "outcome_status": "UNREPAIRABLE",
            "confidence_top": 0.45,
            "confidence_second": 0.40,
            "ambiguity_margin": 0.05,
            "hitl_escalated": True,
        })

    engine = ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=proposals_dir)
    recal_res = engine.recalibrate_post_learning_confidences(
        hitl_ledger_path=hitl_ledger_path,
        healing_db_path=db_path,
        output_store_path=learned_store_path,
    )
    assert recal_res["status"] == "RECALIBRATED"
    assert learned_store_path.is_file()
    assert recal_res["total_learned_options"] >= 2

    # 1. Verify QuestionBuilder re-ranks 'Surgical appendix repair' ahead of 'Full section regeneration'
    # even when 'Full section regeneration' had a slightly higher initial prior (0.60 vs 0.56)
    qb = QuestionBuilder(
        question="Which remediation path should be applied to the executive recap appendix?",
        metadata={
            "category": "healing_policy",
            "ledger_path": str(hitl_ledger_path),
            "healing_db_path": str(db_path),
            "learned_option_store_path": str(learned_store_path),
        },
    )
    qb.add_option(
        label="Full section regeneration",
        plain_english_summary="Regenerate the entire executive recap document from scratch.",
        impact_statement="Replaces all sections and increases processing latency.",
        confidence_score=0.60,
        recommended=True,
    )
    qb.add_option(
        label="Surgical appendix repair",
        plain_english_summary="Apply targeted appendix floor remediation while keeping other sections intact.",
        impact_statement="Repairs only the appendix section and preserves verified narrative content.",
        confidence_score=0.56,
        recommended=False,
    )
    payload = qb.build()
    assert payload.metadata.get("post_learning_recalibrated") is True
    assert payload.options[0]["label"] == "Surgical appendix repair"
    assert payload.options[0]["recommended"] is True
    assert payload.options[1]["recommended"] is False
    assert payload.display_options[0].startswith("(Recommended) Surgical appendix repair")
    assert payload.options[0]["confidence_score"] > payload.options[1]["confidence_score"]

    # 2. Verify arbitrate_decision_margin recalibrates from learning and flips top_option_key
    arb_dec = arbitrate_decision_margin(
        "dec_post_learning_01",
        [("Full section regeneration", 0.60), ("Surgical appendix repair", 0.56)],
        question="Which remediation path should be applied to the executive recap appendix?",
        category="healing_policy",
        ledger_path=hitl_ledger_path,
        healing_db_path=db_path,
        calibrate_from_learning=True,
    )
    assert arb_dec.top_option_key == "Surgical appendix repair"
    assert arb_dec.top_confidence > arb_dec.second_confidence

    # 3. Verify HitlArbiterAgent recalibrates options and emits a valid ask_user_question card
    arbiter = HitlArbiterAgent(ledger_path=hitl_ledger_path, healing_db_path=db_path)
    agent_res = arbiter.arbitrate_decision(
        decision_id="dec_agent_recal_01",
        category="healing_policy",
        question="Which remediation path should be applied to the executive recap appendix?",
        options=[
            {"key": "Full section regeneration", "label": "Full section regeneration", "confidence_score": 0.58},
            {"key": "Surgical appendix repair", "label": "Surgical appendix repair", "confidence_score": 0.56},
        ],
    )
    assert agent_res["top_option"]["label"] == "Surgical appendix repair"
    if agent_res["should_surface"]:
        card = agent_res["atomic_card"]
        assert card["ask_user_question"] is not None
        assert card["options"][0].startswith("(Recommended) Surgical appendix repair")
        val = validate_question_payload(
            question=card["question_payload"]["question"],
            options=card["question_payload"]["options"],
        )
        assert val.is_valid is True
