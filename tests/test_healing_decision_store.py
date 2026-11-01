"""Wave 1 verification suite for Persistent Healing Decision Store & Unified Healer Telemetry."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    heal_disconnected_document_entities,
)
from dual_pipeline.l2_healer_service import UnifiedL2HealerService
from dual_pipeline.l2_healing_contracts import HealingScope, L2HealingRequest
from dual_pipeline.manifest_runner.remediation_healer import heal_output_3_projection
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.healing_decision_store import (
    HealingDecisionRecord,
    HealingDecisionStore,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.micro_healing_dispatcher import MicroHealingDispatcher


@pytest.fixture()
def isolated_healing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_file = tmp_path / "learning.sqlite"
    monkeypatch.setenv("NEE_TEST_LEARNING_SQLITE", str(db_file))
    return db_file


def test_healing_decision_store_crud_and_posteriors(isolated_healing_db: Path) -> None:
    store = HealingDecisionStore(db_path=isolated_healing_db)
    rec1 = store.record_decision(
        HealingDecisionRecord(
            decision_id="dec-001",
            run_id="run-1",
            subsystem="l2_healer_service",
            component="UnifiedL2HealerService:PIPELINE_A_TRANSCRIPT",
            scope="PIPELINE_A_TRANSCRIPT",
            repair_class="SURGICAL_L2_REPAIR",
            action_taken="Healed ISO date",
            outcome_status="HEALED",
            before_digest="b1",
            after_digest="a1",
            confidence_top=0.92,
            confidence_second=0.08,
            ambiguity_margin=0.84,
            hitl_escalated=False,
            invariants_preserved=True,
            defect_reasons=["ISO date found"],
            audit_trail=["dates"],
        )
    )
    assert rec1.decision_id == "dec-001"

    store.record_decisions_batch([
        {
            "decision_id": "dec-002",
            "run_id": "run-2",
            "subsystem": "l2_healer_service",
            "component": "UnifiedL2HealerService:PIPELINE_A_TRANSCRIPT",
            "scope": "PIPELINE_A_TRANSCRIPT",
            "repair_class": "SURGICAL_L2_REPAIR",
            "action_taken": "Healed speaker label",
            "outcome_status": "HEALED",
            "before_digest": "b2",
            "after_digest": "a2",
        },
        {
            "decision_id": "dec-003",
            "run_id": "run-3",
            "subsystem": "l2_healer_service",
            "component": "UnifiedL2HealerService:PIPELINE_A_TRANSCRIPT",
            "scope": "PIPELINE_A_TRANSCRIPT",
            "repair_class": "SURGICAL_L2_REPAIR",
            "action_taken": "Failed fact preservation",
            "outcome_status": "FAILED",
            "before_digest": "b3",
            "after_digest": "b3",
            "hitl_escalated": True,
            "invariants_preserved": False,
        },
    ])

    all_rows = store.list_decisions(subsystem="l2_healer_service")
    assert len(all_rows) == 3

    posteriors = store.compute_empirical_posteriors(prior_alpha=2.0, prior_beta=1.0)
    key = "l2_healer_service:SURGICAL_L2_REPAIR:PIPELINE_A_TRANSCRIPT"
    assert key in posteriors
    stats = posteriors[key]
    assert stats["total_count"] == 3
    assert stats["success_count"] == 2
    assert stats["failure_count"] == 1
    assert stats["posterior_confidence"] == round((2 + 2.0) / (3 + 3.0), 4)


def test_all_healing_subsystems_persist_to_sqlite(isolated_healing_db: Path) -> None:
    # 1. UnifiedL2HealerService
    svc = UnifiedL2HealerService(mock_mode=True)
    req_a = L2HealingRequest(
        scope=HealingScope.PIPELINE_A_TRANSCRIPT,
        candidate_artifact="2026-09-12\nSpeaker 1: Hello team.",
        failure_reasons=["ISO date", "Generic speaker"],
        allowed_entity_ids={"person:alex-sanchez"},
    )
    res_a = asyncio.run(svc.execute_healing(req_a))
    assert res_a.is_success

    # 2. DeterministicHealer mesh (Output 1 and decision answer)
    DeterministicHealer.clear_audit_log()
    h1, c1 = DeterministicHealer.heal_output_1(
        "Header 2026-10-15   \n\n\nBody line", metadata={"run_id": "run-mesh-1"}
    )
    assert c1
    ans, c_ans = DeterministicHealer.heal_decision_answer(
        {"summary": "Target date 2026-11-01   "}, defect_codes=["ISO_DATE"]
    )
    assert c_ans

    # 3. MicroHealingDispatcher
    dispatcher = MicroHealingDispatcher(max_iterations=1)
    disp_res = dispatcher.dispatch_healing(
        f1="Speaker ?: [GAP_DETECTED] Discussion text with wtf.",
        f2="Attendees: Alice Smith, Bob Jones\n[TODO] summary",
        f3={"auto_apply": True, "nodes": [{"id": "person:alice"}, {"id": "person:alice"}]},
        metadata={"run_id": "run-micro-1"},
    )
    assert disp_res.audit_trail

    # 4. SurgicalRemediator
    rem_text, did_rem, rem_audit = SurgicalRemediator.remediate_output_1(
        transcript_text="# Live Meeting Transcript\n[0:5:2] Alex: Thank you for watching.\n",
        qa_report={"output_01_forensic_transcript": {"failures": ["header syntax and timestamp"]}},
        judge_report={},
        metadata={"run_id": "run-surg-1", "date": "10/26/2026"},
    )
    assert did_rem
    assert rem_audit

    # 5. Manifest remediation_healer
    healed_f3 = heal_output_3_projection(
        f3_text='{"graph_mutations": {"entity_upserts": []}}',
        remediation_directive="Fix schema fields",
        client=None,
    )
    assert isinstance(healed_f3, dict)

    # 6. Document connectivity healer
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
    conn_changes = heal_disconnected_document_entities(proj, metadata={"run_id": "run-conn-1"})
    assert conn_changes

    # 7. SelectiveRemediationStateMachine regression & stagnation
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    sm.record_candidate("output_1", "Good content", 96, verdict="PASS")
    sm.current_attempt = 2
    sm.record_candidate("output_1", "Regressed content", 70, verdict="FAIL")
    sm._check_monotonic_regressions({"output_1": "Regressed content"})

    store = HealingDecisionStore(db_path=isolated_healing_db)
    subsystems = {row["subsystem"] for row in store.list_decisions(limit=200)}
    assert "l2_healer_service" in subsystems
    assert "deterministic_healing_mesh" in subsystems
    assert "micro_healing_dispatcher" in subsystems
    assert "surgical_remediator" in subsystems
    assert "manifest_remediation_healer" in subsystems
    assert "document_connectivity_healer" in subsystems
    assert "remediation_state_machine" in subsystems

    with GovernedStorageContext.read_context(isolated_healing_db) as conn:
        integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        assert integrity == "ok"
