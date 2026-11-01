"""Wave 4 End-to-End Test: Closed-Loop Healing Persistence, Offline System Learning & Post-Learning Recalibration."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from dual_pipeline.calibration import (
    create_signed_calibration_approval_receipt as create_calibration_approval_receipt,
)
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    heal_disconnected_document_entities,
)
from dual_pipeline.healing_policy import arbitrate_decision_margin
from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload
from dual_pipeline.l2_healer_service import UnifiedL2HealerService
from dual_pipeline.l2_healing_contracts import HealingScope, L2HealingRequest
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_engine_core.provenance import verify_proposal_provenance
from dual_pipeline.manifest_runner.remediation_healer import heal_output_3_projection
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.micro_healing_dispatcher import MicroHealingDispatcher
from tools.govern_learning_proposals import main as govern_cli_main


def test_closed_loop_healing_to_offline_learning_and_ask_question_recalibration_e2e(
    tmp_path: Path, monkeypatch
) -> None:
    """Full E2E lifecycle: in-flight healers -> SQLite store -> offline learning proposals -> approval -> post-learning ask_user_question recalibration."""
    runtime_root = tmp_path / ".local"
    ledger_dir = runtime_root / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    db_path = ledger_dir / "learning.sqlite"
    outcome_jsonl = ledger_dir / "live_outcome_events.jsonl"
    hitl_ledger_path = ledger_dir / "hitl_decision_ledger.json"
    proposals_dir = tmp_path / "proposals"
    learned_store_path = runtime_root / "learned_hitl_option_store.json"

    monkeypatch.setenv("NEE_TEST_LEARNING_SQLITE", str(db_path))
    monkeypatch.setenv("NEE_LEARNING_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("NEE_LEARNED_HITL_OPTION_STORE", str(learned_store_path))

    # =========================================================================
    # Phase 1: Trigger all in-flight healers & outcome JSONL writers
    # =========================================================================
    # 1a. UnifiedL2HealerService (multiple runs so cohort count >= 2)
    svc = UnifiedL2HealerService(mock_mode=True)
    for idx in range(3):
        req_a = L2HealingRequest(
            scope=HealingScope.PIPELINE_A_TRANSCRIPT,
            candidate_artifact=f"2026-09-12\nSpeaker 1: Hello team {idx}.",
            failure_reasons=["ISO date", "Generic speaker"],
            allowed_entity_ids={"person:alex-sanchez"},
        )
        res_a = asyncio.run(svc.execute_healing(req_a))
        assert res_a.is_success

    # 1b. DeterministicHealer
    DeterministicHealer.clear_audit_log()
    _, c1 = DeterministicHealer.heal_output_1(
        "Header 2026-10-15   \n\n\nBody line", metadata={"run_id": "run-mesh-e2e"}
    )
    assert c1

    # 1c. MicroHealingDispatcher
    dispatcher = MicroHealingDispatcher(max_iterations=1)
    dispatcher.dispatch_healing(
        f1="Speaker ?: [GAP_DETECTED] Discussion text with wtf.",
        f2="Attendees: Alice Smith, Bob Jones\n[TODO] summary",
        f3={"auto_apply": True, "nodes": [{"id": "person:alice"}, {"id": "person:alice"}]},
        metadata={"run_id": "run-micro-e2e"},
    )

    # 1d. SurgicalRemediator
    SurgicalRemediator.remediate_output_1(
        transcript_text="# Live Meeting Transcript\n[0:5:2] Alex: Thank you for watching.\n",
        qa_report={"output_01_forensic_transcript": {"failures": ["header syntax and timestamp"]}},
        judge_report={},
        metadata={"run_id": "run-surg-e2e", "date": "10/26/2026"},
    )

    # 1e. Manifest remediation_healer
    heal_output_3_projection(
        f3_text='{"graph_mutations": {"entity_upserts": []}}',
        remediation_directive="Fix schema fields",
        client=None,
    )

    # 1f. Document connectivity healer
    proj = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "project:jarvis", "name": "Jarvis"},
                {"id": "system:snowflake", "name": "Snowflake"},
            ],
            "relationship_signals": [],
        },
        "evidence_index": {"ev-1": {"exact_text": "Jarvis uses Snowflake"}},
    }
    heal_disconnected_document_entities(proj, metadata={"run_id": "run-conn-e2e"})

    # 1g. SelectiveRemediationStateMachine regression
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    sm.record_candidate("output_1", "Good content", 96, verdict="PASS")
    sm.current_attempt = 2
    sm.record_candidate("output_1", "Regressed content", 70, verdict="FAIL")
    sm._check_monotonic_regressions({"output_1": "Regressed content"})

    # 1h. Append live_outcome_events.jsonl (recurring escalation cohort)
    outcome_events = [
        {
            "event_id": f"evt-esc-{i}",
            "run_id": f"run-esc-{i}",
            "subsystem": "l2_healer_service",
            "component": "UnifiedL2HealerService",
            "scope": "PIPELINE_A_TRANSCRIPT",
            "repair_class": "GENERIC_FIELD_CLEANUP",
            "action_taken": "ESCALATE_HITL",
            "outcome_status": "ESCALATED_HITL",
            "confidence_top": 0.56,
            "confidence_second": 0.49,
            "ambiguity_margin": 0.07,
            "hitl_escalated": True,
            "invariants_preserved": True,
            "defect_reasons": ["Margin 0.07 below 0.20 threshold"],
        }
        for i in range(3)
    ]
    with outcome_jsonl.open("w", encoding="utf-8") as fh:
        for ev in outcome_events:
            fh.write(json.dumps(ev) + "\n")

    # Independent SQLite check after Phase 1
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"
        initial_count = conn.execute("SELECT COUNT(*) FROM healing_decisions").fetchone()[0]
        assert initial_count >= 8

    # =========================================================================
    # Phase 2: Offline System Learning Cycle & CLI Verification
    # =========================================================================
    engine = ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=proposals_dir)
    cycle_res = engine.run_healing_learning_cycle(
        db_path=db_path,
        outcome_ledger_path=outcome_jsonl,
        min_occurrences=2,
        generate_proposals=True,
    )
    assert cycle_res["total_decisions_analyzed"] >= 11
    assert cycle_res["proposals_generated"] >= 2

    proposals = cycle_res["proposals"]
    for prop in proposals:
        validate_artifact(prop)
        assert prop["auto_apply"] is False
        prov_ok, prov_msg = verify_proposal_provenance(prop)
        assert prov_ok is True, prov_msg

    # Verify CLI healing-scan subcommand
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "govern_learning_proposals.py",
            "--proposals-dir",
            str(proposals_dir),
            "healing-scan",
            "--db-path",
            str(db_path),
            "--min-occurrences",
            "2",
        ],
    )
    cli_rc = govern_cli_main()
    assert cli_rc == 0

    # =========================================================================
    # Phase 3: Dry-Run Simulation & Cryptographic Human Approval Application
    # =========================================================================
    workflow_prop = next(p for p in proposals if p["proposal_type"] == "workflow")
    sim_res = engine.simulate_proposal(workflow_prop)
    assert sim_res.success is True
    assert sim_res.status == "SIMULATED_PASS"
    assert sim_res.details.get("zero_mutation_guaranteed") is True

    approval_receipt = create_calibration_approval_receipt(
        proposal=workflow_prop,
        approved_by="operator:amit.ayer",
        rationale="Approve empirically verified surgical L2 repair rule.",
    )
    apply_receipt = engine.apply_approved_proposal(workflow_prop, approval_receipt)
    assert apply_receipt["status"] == "APPLIED"
    assert learned_store_path.is_file()

    # =========================================================================
    # Phase 4: Post-Learning Confidence Recalibration & ask_user_question Check
    # =========================================================================
    # Initially ambiguous options where GENERIC_FIELD_CLEANUP is given 0.60 and SURGICAL_L2_REPAIR is given 0.56.
    # Because SURGICAL_L2_REPAIR succeeded 3/3 times and GENERIC_FIELD_CLEANUP escalated 3/3 times,
    # post-learning recalibration boosts SURGICAL_L2_REPAIR above GENERIC_FIELD_CLEANUP and places it first.
    arb_dec = arbitrate_decision_margin(
        "dec_e2e_recalibrated",
        [("GENERIC_FIELD_CLEANUP", 0.60), ("SURGICAL_L2_REPAIR", 0.56)],
        question="Which transcript repair strategy should be applied?",
        category="healing_policy",
        ledger_path=hitl_ledger_path,
        healing_db_path=db_path,
        calibrate_from_learning=True,
    )
    assert arb_dec.top_option_key == "SURGICAL_L2_REPAIR"
    assert arb_dec.top_confidence > arb_dec.second_confidence

    # Also verify HitlArbiterAgent surfaces a 100% contract-valid ask_user_question payload with recalibrated ranking
    arbiter = HitlArbiterAgent(ledger_path=hitl_ledger_path, healing_db_path=db_path)
    agent_res = arbiter.arbitrate_decision(
        decision_id="dec_e2e_agent_card",
        category="healing_policy",
        question="Which transcript repair strategy should be applied?",
        options=[
            {"key": "GENERIC_FIELD_CLEANUP", "label": "Generic field cleanup", "confidence_score": 0.62},
            {"key": "SURGICAL_L2_REPAIR", "label": "Surgical L2 repair", "confidence_score": 0.56},
        ],
    )
    assert agent_res["top_option"]["key"] == "SURGICAL_L2_REPAIR"
    if agent_res["should_surface"]:
        card = agent_res["atomic_card"]
        assert card["ask_user_question"] is not None
        assert card["options"][0].startswith("(Recommended)")
        v_res = validate_question_payload(
            question=card["question_payload"]["question"],
            options=card["question_payload"]["options"],
        )
        assert v_res.is_valid is True
