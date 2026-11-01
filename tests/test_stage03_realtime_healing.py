"""
tests/test_stage03_realtime_healing.py

Unit test suite for Wave 4:
1. Real-time deterministic healing in post_process_stage03_projection (predicates, prefixes, compound attributes, aliases).
2. Temporal SSOT normalization (ISO dates -> MM/DD/YYYY in observed_at / stale_after).
3. Stage 03 Checkpoint Gatekeeper: fast deterministic Gate 3 evaluation and artifact freezing.
4. Fail-closed behavior on empty signal projection or fatal parse errors.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
from dual_pipeline.transcript_pipeline_core.stage_checkpoint import (
    evaluate_stage_03_checkpoint,
    is_fatal_stage_03_defect,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_stages_core.graph_executor import (
    post_process_stage03_projection,
)


def _build_clean_payload() -> dict:
    """Build a baseline compliant Output 03 payload passing Gate 3."""
    return {
        "schema_version": "5.7",
        "meeting_id": "test-stage03-clean",
        "classification": "internal",
        "audiences": ["nee-engineering", "partner_advisory-delivery"],
        "evidence_index": {
            "stmt-01": {
                "statement_id": "stmt-01",
                "text": "The rule authoring engine connects directly with LENS Phase 1 at offset 04:15.",
                "exact_text": "The rule authoring engine connects directly with LENS Phase 1 at offset 04:15.",
                "speaker": "harper Hoffman",
                "source_classification": "internal",
                "locator": {"kind": "timestamp", "value": "04:15"},
            },
            "stmt-02": {
                "statement_id": "stmt-02",
                "text": "PartnerAdvisory will present stabilization milestones on 09/14/2026.",
                "exact_text": "PartnerAdvisory will present stabilization milestones on 09/14/2026.",
                "speaker": "Amit Ayer",
                "source_classification": "internal",
                "locator": {"kind": "timestamp", "value": "06:30"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:rule_engine",
                    "type": "system",
                    "name": "Rule Engine",
                    "mutation": "CONFIRM",
                    "attributes": {"tier": "core", "language": "python"},
                },
                {
                    "id": "system:lens",
                    "type": "system",
                    "name": "LENS",
                    "mutation": "CONFIRM",
                    "attributes": {"runtime": "gcp_cloud_run"},
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-01",
                    "from_id": "system:rule_engine",
                    "to_id": "system:lens",
                    "relationship": "INTEGRATES_WITH",
                    "evidence_ids": ["stmt-01"],
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "decision:stabilization_freeze",
                    "status": "APPROVED",
                    "observed_at": "09/11/2026",
                    "decision_question": "Shall the core pipeline deploy under stabilization freeze?",
                    "rationale": "Alignment on Phase 1 defect burn-down before expanding scope.",
                    "evidence_ids": ["stmt-02"],
                }
            ],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [
                {
                    "risk_id": "risk:contract_schedule_drift",
                    "status": "ACTIVE",
                    "observed_at": "09/11/2026",
                    "description": "Rule authoring defect latency could impact deployment milestone.",
                    "evidence_ids": ["stmt-01"],
                }
            ],
        },
        "evidence_only_statement_ids": [],
    }


def test_is_fatal_stage_03_defect():
    assert is_fatal_stage_03_defect(["empty canonical signal projection"])
    assert is_fatal_stage_03_defect(["output 3 json parse error: Expecting value"])
    assert is_fatal_stage_03_defect(["0 mutations detected in output"])
    assert not is_fatal_stage_03_defect(["Gate 3.9: predicate domain invalid"])
    assert not is_fatal_stage_03_defect(["Gate 3.7: compound properties detected"])


def test_post_process_stage03_projection_heals_predicates_and_aliases():
    raw_payload = _build_clean_payload()
    # Introduce generic predicate and duplicate prefix
    raw_payload["graph_mutations"]["relationship_signals"][0]["relationship"] = "CONNECTED_TO"
    raw_payload["graph_mutations"]["entity_upserts"][0]["id"] = "system:system:rule_engine"
    raw_payload["graph_mutations"]["relationship_signals"][0]["from_id"] = "system:system:rule_engine"
    # Introduce compound attribute string
    raw_payload["graph_mutations"]["entity_upserts"][0]["attributes"]["packed_info"] = "env: prod, tier: critical"

    raw_json = json.dumps(raw_payload)
    forensic = "The rule authoring engine connects directly with LENS Phase 1 at offset 04:15. PartnerAdvisory will present stabilization milestones on 09/14/2026."

    healed_json = post_process_stage03_projection(
        raw_graph_json=raw_json,
        metadata={},
        forensic_transcript=forensic,
    )
    healed_data = json.loads(healed_json)

    # 1. Predicate CONNECTED_TO mapped to INTEGRATES_WITH
    rel = healed_data["graph_mutations"]["relationship_signals"][0]["relationship"]
    assert rel == "INTEGRATES_WITH"

    # 2. Duplicate prefix system:system:rule_engine normalized to system:rule_engine
    entity_id = healed_data["graph_mutations"]["entity_upserts"][0]["id"]
    assert entity_id == "system:rule_engine"

    # 3. Compound attribute unpacked into atomic keys
    attrs = healed_data["graph_mutations"]["entity_upserts"][0]["attributes"]
    assert "packed_info" not in attrs
    assert attrs.get("env") == "prod"
    assert attrs.get("tier") == "critical"


def test_deterministic_healer_output3_temporal_ssot():
    raw_payload = _build_clean_payload()
    raw_payload["graph_mutations"]["decision_updates"][0]["observed_at"] = "2026-09-11"
    raw_payload["graph_mutations"]["risk_updates"][0]["observed_at"] = "2026-09-11"

    healed_json, changes = DeterministicHealer.heal_output_3(raw_payload)
    healed_data = json.loads(healed_json)

    dec_obs = healed_data["graph_mutations"]["decision_updates"][0]["observed_at"]
    risk_obs = healed_data["graph_mutations"]["risk_updates"][0]["observed_at"]
    assert dec_obs == "09/11/2026"
    assert risk_obs == "09/11/2026"
    assert any("date" in c.lower() or "temporal" in c.lower() or "iso" in c.lower() for c in changes)


def test_stage03_checkpoint_fail_closed_on_empty():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    with pytest.raises(RuntimeError, match="Stage 03 Checkpoint Gate 3 FAIL-CLOSED"):
        evaluate_stage_03_checkpoint(
            pipeline=MagicMock(),
            canonical_projection="",
            forensic_transcript="forensic text",
            metadata={},
            state_machine=sm,
            artifacts=artifacts,
            recorder=rec,
            notify_step=notify,
            attempt=1,
            is_notes_mode=False,
        )
    assert not sm.is_frozen("output_3")


def test_stage03_checkpoint_fail_closed_on_fatal_defect():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    with pytest.raises(RuntimeError, match="Stage 03 Checkpoint Gate 3 FAIL-CLOSED"):
        evaluate_stage_03_checkpoint(
            pipeline=MagicMock(),
            canonical_projection="{invalid_json: true",
            forensic_transcript="forensic text",
            metadata={},
            state_machine=sm,
            artifacts=artifacts,
            recorder=rec,
            notify_step=notify,
            attempt=1,
            is_notes_mode=False,
        )
    assert not sm.is_frozen("output_3")


def test_stage03_checkpoint_freezes_valid_projection():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    payload = _build_clean_payload()
    raw_json = json.dumps(payload)
    forensic = "The rule authoring engine connects directly with LENS Phase 1 at offset 04:15. PartnerAdvisory will present stabilization milestones on 09/14/2026."

    res = evaluate_stage_03_checkpoint(
        pipeline=MagicMock(),
        canonical_projection=raw_json,
        forensic_transcript=forensic,
        metadata={"enforce_empty_actions": False},
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=1,
        is_notes_mode=False,
    )

    assert sm.is_frozen("output_3")
    assert artifacts["03_canonical_signal_projection.json"] == res
    assert "stage03_qa_gate3.json" in artifacts
    assert artifacts["stage03_qa_gate3.json"]["passed"] is True

    # Calling again returns frozen cached content
    res2 = evaluate_stage_03_checkpoint(
        pipeline=MagicMock(),
        canonical_projection="corrupted_content",
        forensic_transcript="forensic text",
        metadata={},
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=2,
        is_notes_mode=False,
    )
    assert res2 == res
