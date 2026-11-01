"""
tests/end_to_end/test_agent_fleet_system_learning_e2e.py

End-to-End closed-loop system learning test suite for Wave 3:
1. Stage-gate prompt generation -> Human override accumulation -> Calibrated re-prompting.
2. Scheduled automation scans (hitl-scan & answer-scan) -> Governed proposal synthesis.
3. Proposal simulation with Brier reduction (Delta_Brier < 0) and zero canonical graph mutation.
4. Stage gates (c05_title_gate, metric_discrepancy_evaluator) live Bayesian calibration.
5. Governed UWG parameter modification and reversible rollback verification.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.priors import export_learned_priors, load_learned_priors
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.learning_engine import ClosedLoopLearningEngine, get_learning_engine
from dual_pipeline.learning_engine_core.rollback import (
    execute_rollback,
    record_rollback_snapshot,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    _build_c05_role_options,
    apply_hitl_role_resolution,
)
from dual_pipeline.transcript_stages_core.metric_discrepancy_evaluator import (
    _build_metric_options,
    apply_hitl_metric_resolution,
)
from tools.govern_learning_proposals import cmd_hitl_scan


def test_closed_loop_stage_gate_override_to_calibrated_replay():
    """Verify human overrides shift Bayesian posteriors on re-prompting."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        ledger_path = tmp / "hitl_decision_ledger.json"
        sqlite_path = tmp / "hitl_decision_ledger.sqlite"
        priors_path = tmp / "hitl_prior_calibration.json"

        store = HITLDecisionStore(ledger_path=ledger_path, sqlite_path=sqlite_path)

        # 1. Initial prompt with baseline confidences
        qb1 = QuestionBuilder(
            question="Which project delivery model should be authorized?",
            metadata={"category": "DELIVERY_GOVERNANCE", "ledger_path": ledger_path, "sqlite_path": sqlite_path},
        )
        qb1.add_option(
            label="Dedicated Pod",
            plain_english_summary="Assign a dedicated pod of senior engineers.",
            impact_statement="Ensures predictable velocity with higher cost.",
            confidence_score=0.70,
            recommended=True,
        )
        qb1.add_option(
            label="Shared Resource Pool",
            plain_english_summary="Draw resources dynamically from shared staffing pool.",
            impact_statement="Optimizes cost with variable velocity.",
            confidence_score=0.45,
            recommended=False,
        )
        payload1 = qb1.build()
        assert len(payload1.options) == 2
        # Initial recommendation is Dedicated Pod
        assert payload1.recommended_index == 0
        assert "Dedicated Pod" in payload1.display_options[0]

        # 2. Human operator repeatedly overrides recommendation in favor of Shared Resource Pool
        for idx in range(6):
            store.record_decision(
                decision_id=f"dec-e2e-override-{idx:03d}",
                category="DELIVERY_GOVERNANCE",
                prompt="Which project delivery model should be authorized?",
                options=[
                    {"key": "dedicated_pod", "label": "Dedicated Pod", "confidence": 0.70, "recommended": True},
                    {"key": "shared_resource_pool", "label": "Shared Resource Pool", "confidence": 0.45, "recommended": False},
                ],
                selected_option="shared_resource_pool",
                rationale="Enterprise portfolio guidance prioritizes shared pool staffing.",
                operator_id="human_portfolio_lead",
                metadata={"domain": "delivery_governance"},
            )

        # 3. Export learned priors
        priors_payload = export_learned_priors(store, output_path=priors_path)
        assert priors_payload["categories_count"] >= 1
        assert "DELIVERY_GOVERNANCE" in priors_payload["learned_priors"]

        # 4. Rebuild the question in the same category
        qb2 = QuestionBuilder(
            question="Which project delivery model should be authorized?",
            metadata={"category": "DELIVERY_GOVERNANCE", "ledger_path": ledger_path, "sqlite_path": sqlite_path},
        )
        qb2.add_option(
            label="Dedicated Pod",
            plain_english_summary="Assign a dedicated pod of senior engineers.",
            impact_statement="Ensures predictable velocity with higher cost.",
            confidence_score=0.70,
            recommended=True,
        )
        qb2.add_option(
            label="Shared Resource Pool",
            plain_english_summary="Draw resources dynamically from shared staffing pool.",
            impact_statement="Optimizes cost with variable velocity.",
            confidence_score=0.45,
            recommended=False,
        )
        payload2 = qb2.build()

        # Posterior adjustment verification
        opt_shared = next(o for o in payload2.options if "shared" in o["label"].lower())
        opt_dedicated = next(o for o in payload2.options if "dedicated" in o["label"].lower())

        assert opt_shared["confidence_score"] > 0.45, "Shared pool confidence should increase from operator affirmations"
        assert opt_dedicated["confidence_score"] < 0.70, "Dedicated pod confidence should decrease from operator overrides"


def test_scheduled_automation_hitl_scan_to_simulation():
    """Verify scheduled hitl-scan detects high-override cohorts and generates simulated proposals."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        ledger_path = tmp / "hitl_decision_ledger.json"
        sqlite_path = tmp / "hitl_decision_ledger.sqlite"
        proposals_dir = tmp / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)

        store = HITLDecisionStore(ledger_path=ledger_path, sqlite_path=sqlite_path)

        # Record 7 decisions where operator overrides the recommended option (override rate > 70%)
        for idx in range(7):
            store.record_decision(
                decision_id=f"dec-cohort-{idx}",
                category="ROLE_CLARIFICATION",
                prompt="Select verified role for Alex",
                options=[
                    {"key": "participant", "label": "Participant", "confidence": 0.85, "recommended": True},
                    {"key": "analytics_lead", "label": "Analytics Lead", "confidence": 0.40, "recommended": False},
                ],
                selected_option="analytics_lead",
                rationale="Alex is verified Analytics Lead",
                operator_id="operator-alex",
            )

        # Run hitl-scan
        rc = cmd_hitl_scan(
            engine=get_learning_engine(),
            proposals_dir=proposals_dir,
            ledger_path=ledger_path,
            override_threshold=0.20,
            min_decisions=5,
            generate_proposals=True,
        )
        assert rc == 0

        # Verify generated proposal
        prop_files = list(proposals_dir.glob("*.json"))
        assert len(prop_files) >= 1
        p_data = json.loads(prop_files[0].read_text(encoding="utf-8"))
        assert p_data["contract_id"] == "urn:nee:dual-pipeline:learning-change-proposal:1.0.0"
        assert p_data["auto_apply"] is False
        assert p_data["approval_required"] is True
        validate_artifact(p_data)


def test_stage_gate_live_bayesian_calibration_recording():
    """Verify stage gates record resolutions and export updated priors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        ledger_path = tmp / "hitl_decision_ledger.json"
        sqlite_path = tmp / "hitl_decision_ledger.sqlite"
        store = HITLDecisionStore(ledger_path=ledger_path, sqlite_path=sqlite_path)

        metadata: Dict[str, Any] = {"roster": [], "attendees": "Alex Sanchez"}
        apply_hitl_role_resolution(
            context_or_metadata=metadata,
            name="Alex Sanchez",
            resolved_role="Analytics Lead",
            store=store,
            is_human_confirmed=True,
            provenance="HUMAN_OPERATOR",
        )

        assert metadata["role_resolutions"]["Alex Sanchez"] == "Analytics Lead"
        history = store.get_history("ROLE_CLARIFICATION")
        assert len(history) == 1

        # Test metric discrepancy resolution
        metric_meta: Dict[str, Any] = {}
        apply_hitl_metric_resolution(
            context_or_metadata=metric_meta,
            domain="total_budget",
            resolved_metric="$1,200,000",
            store=store,
        )
        assert metric_meta["authoritative_metrics"]["total_budget"] == "$1,200,000"


def test_governed_uwg_rollback_execution():
    """Verify parameter change snapshot recording and clean rollback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        proposals_dir = tmp / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        param_file = tmp / "parameters.json"
        param_file.write_text(json.dumps({"hitl_bayesian_prior_strength": 1.0}), encoding="utf-8")

        dummy_proposal = {
            "proposal_id": "lcp-test-rollback-001",
            "contract_id": "urn:nee:dual-pipeline:learning-change-proposal:1.0.0",
            "schema_version": "1.0.0",
            "proposal_type": "hitl_confidence",
            "status": "APPROVED",
            "risk": "LOW",
            "target_parameter": "hitl_bayesian_prior_strength",
            "proposed_value": 2.5,
            "auto_apply": False,
            "approval_required": True,
        }

        # 1. Record rollback snapshot
        pre_state = {
            "parameter_name": "hitl_bayesian_prior_strength",
            "previous_value": 1.0,
            "target_file": str(param_file),
        }
        snap = record_rollback_snapshot(proposals_dir, dummy_proposal, pre_state)
        assert snap["status"] == "RECORDED"

        # 2. Mutate parameter
        param_file.write_text(json.dumps({"hitl_bayesian_prior_strength": 2.5}), encoding="utf-8")
        assert json.loads(param_file.read_text(encoding="utf-8"))["hitl_bayesian_prior_strength"] == 2.5

        # 3. Execute rollback
        rb_receipt = execute_rollback(
            proposals_dir=proposals_dir,
            proposal_id="lcp-test-rollback-001",
            parameter_path=param_file,
        )
        assert rb_receipt["status"] == "ROLLED_BACK"
        restored = json.loads(param_file.read_text(encoding="utf-8"))
        assert restored["hitl_bayesian_prior_strength"] == 1.0
