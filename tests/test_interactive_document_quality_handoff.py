"""Regressions from the interactive weekly document preview."""

from __future__ import annotations

import hashlib
import json

import pytest

from dual_pipeline.canonical_pipeline.document_remediation import _record_unbound_document_patch_handoff
from dual_pipeline.document_pipeline_core.bounded_patch import (
    InvalidBoundedPatchResponseError,
    UnboundPatchScopeError,
    build_bounded_patch_request,
)
from dual_pipeline.document_pipeline_core.evaluator import evaluate_document_qa_gates
from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
from dual_pipeline.document_pipeline_core.executor import (
    _execute_bounded_patch,
    _post_process_document_projection,
    _quarantine_unsubstantiated_integrations,
)
from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY
from dual_pipeline.signal_extraction import SignalExtractor
from dual_pipeline.transcript_stages_core.graph_post_extractor import sanitize_and_validate_projection
from dual_pipeline.transcript_qa_core.graph_healer import (
    _ground_proposal_evidence_rationale,
    heal_graph_projection,
)
from dual_pipeline.transcript_qa_core.gate3 import _check_topology


def _candidate() -> dict:
    return {
        "evidence_index": {"ev-01": {"text": "The campus supplies the adjacent data center."}},
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:campus", "evidence_ids": ["ev-01"]},
                {"id": "system:data-center", "evidence_ids": ["ev-01"]},
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [{"proposal_id": "proposal:campus", "description": "Campus and data center", "evidence_ids": ["ev-01"]}],
            "risk_updates": [],
            "action_updates": [],
            "strategic_intent_signals": [],
        },
    }


def test_unbound_judge_diff_has_typed_review_handoff_without_patch(tmp_path) -> None:
    source = "The campus supplies the adjacent data center."
    candidate = json.dumps(_candidate())
    judge = {
        "status": "FAIL", "evaluation_receipt_digest": "sealed-judge-digest",
        "output_3_evaluation": {
            "verdict": "FAIL", "composite_score": 89,
            "actionable_remediation_diff": "Remove unsupported links and add capacity entities.",
        },
    }

    with pytest.raises(UnboundPatchScopeError, match="No failed existing object IDs"):
        build_bounded_patch_request(candidate, judge, source)

    attempt_dir = tmp_path / "attempts" / "attempt_1"
    attempt_dir.mkdir(parents=True)
    path = _record_unbound_document_patch_handoff(
        tmp_path, 1, source, candidate, judge, "Repair source grounding",
        "No failed existing object IDs are bound; full regeneration is prohibited",
    )
    handoff = json.loads(path.read_text())
    assert handoff["status"] == "REVIEW_REQUIRED"
    assert handoff["reason_code"] == "UNBOUND_DOCUMENT_PATCH_SCOPE"
    assert handoff["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert handoff["candidate_sha256"] == hashlib.sha256(candidate.encode()).hexdigest()
    assert handoff["judge_evaluation_digest"] == "sealed-judge-digest"
    assert handoff["judge_verdict"] == "FAIL"
    assert handoff["judge_score"] == 89
    assert not (attempt_dir / "document_patch_request.json").exists()


def test_document_healer_does_not_invent_orphan_relationship() -> None:
    data = _candidate()
    original_entities = list(data["graph_mutations"]["entity_upserts"])
    changes: list[str] = []
    heal_graph_projection(data, changes, metadata={"is_document": True})
    assert data["graph_mutations"]["relationship_signals"] == []
    assert data["graph_mutations"]["entity_upserts"] == original_entities
    assert not any("Connected orphan" in change for change in changes)


def test_document_topology_accepts_cited_standalone_entities_only() -> None:
    entities = [{"id": "system:campus", "evidence_ids": ["ev-01"]}]
    assert _check_topology(entities, [], document_evidence_index={"ev-01": {}})[0]
    assert not _check_topology(entities, [])[0]
    assert not _check_topology(entities, [], document_evidence_index={})[0]


def test_document_healer_does_not_apply_meeting_workflow_rules() -> None:
    data = _candidate()
    data["evidence_index"]["ev-01"]["text"] = (
        "A design review compares construction timing and permit exposure."
    )
    data["graph_mutations"]["proposal_updates"] = []
    heal_graph_projection(data, [], metadata={
        "is_document": True,
        "source_text": data["evidence_index"]["ev-01"]["text"],
    })
    mutations = data["graph_mutations"]
    assert mutations["proposal_updates"] == []
    assert not any(
        "jarvis" in str(item).lower()
        for item in mutations.get("strategic_intent_signals", [])
    )


def test_invalid_bounded_patch_response_is_typed_and_reviewable(tmp_path) -> None:
    source = "The campus supplies the adjacent data center."
    candidate = json.dumps(_candidate())
    judge = {
        "status": "FAIL",
        "output_3_evaluation": {
            "verdict": "FAIL",
            "failed_claims": [{
                "collection": "proposal_updates", "id": "proposal:campus",
                "reason": "Rationale is unsupported by the source.",
            }],
        },
    }
    request = build_bounded_patch_request(candidate, judge, source)

    class EmptyPatchProvider:
        def synthesize_graph(self, _system: str, _content: str) -> str:
            return json.dumps({
                "contract_version": request["contract_version"],
                "candidate_sha256": request["candidate_sha256"],
                "source_sha256": request["source_sha256"],
                "request_sha256": request["request_sha256"],
                "changes": [],
            })

    with pytest.raises(InvalidBoundedPatchResponseError, match="nonempty"):
        _execute_bounded_patch(EmptyPatchProvider(), source, {}, candidate, request)

    path = _record_unbound_document_patch_handoff(
        tmp_path, 1, source, candidate, judge, "Repair source grounding",
        "Patch changes must be nonempty", reason_code="INVALID_BOUNDED_PATCH_RESPONSE",
    )
    handoff = json.loads(path.read_text())
    assert handoff["reason_code"] == "INVALID_BOUNDED_PATCH_RESPONSE"
    assert handoff["status"] == "REVIEW_REQUIRED"


def test_proposal_fallback_rationale_contains_only_cited_source() -> None:
    proposal = {"evidence_ids": ["ev-01"]}
    evidence = {"ev-01": {"exact_text": "A 6 GW campus was announced."}}
    assert _ground_proposal_evidence_rationale(proposal, evidence) == "A 6 GW campus was announced."
    assert _ground_proposal_evidence_rationale(proposal, {}) == ""


def test_document_prompt_requires_pairwise_evidence_and_strategic_coverage() -> None:
    prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_DOCUMENT_GRAPH")
    assert "supports that exact pair of endpoints and predicate" in prompt
    assert "timing tradeoffs as strategic signals" in prompt
    assert "Do not add generic stakeholder feedback" in prompt


def test_strategic_signal_survives_promotion_with_grounding() -> None:
    candidate = _candidate()
    candidate["graph_mutations"]["strategic_intent_signals"] = [
        {"signal_id": "sig-timing", "text": "Compare published delivery dates.", "evidence_ids": ["ev-01"]},
        {"signal_id": "sig-unsupported", "text": "Unsupported comparison.", "evidence_ids": []},
    ]
    promoted, receipt = SignalExtractor().extract_and_gate(candidate)
    assert promoted["graph_mutations"]["strategic_intent_signals"] == [
        candidate["graph_mutations"]["strategic_intent_signals"][0]
    ]
    assert receipt["quarantined_signal_count"] >= 1


@pytest.mark.parametrize("raw", ["not json", "[]", '{"evidence_index": {}}'])
def test_document_extractor_rejects_invalid_or_missing_raw_projection(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid JSON|omitted"):
        _post_process_document_projection(raw, {"is_document": True}, "A sourced document.")


def test_empty_document_projection_cannot_pass_release_gate() -> None:
    empty = {
        "schema_version": "5.7", "meeting_id": "meeting-unknown",
        "classification": "internal", "audiences": ["internal_consulting"],
        "evidence_index": {}, "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": [], "relationship_signals": [],
            "decision_updates": [], "proposal_updates": [],
            "risk_updates": [], "action_updates": [],
            "strategic_intent_signals": [],
        },
    }
    report = evaluate_document_qa_gates(
        json.dumps(empty),
        "The source announces a planned power campus and dated nuclear capacity uprates.",
        {"is_document": True, "output3_only": True},
    )
    assert report["status"] == "FAIL"
    assert report["gate_3"]["document_signal_yield"] == {
        "passed": False, "evidence_count": 0, "mutation_count": 0,
    }
    assert any("source-grounded evidence" in issue for issue in report["gate_3"]["failures"])


def test_document_sanitizer_does_not_generate_proposal_from_timing_caveat() -> None:
    candidate = _candidate()
    candidate["graph_mutations"]["proposal_updates"] = []
    candidate["evidence_index"]["ev-01"]["text"] = (
        "Project Star resources are anticipated by 2029, subject to permits."
    )
    sanitized = sanitize_and_validate_projection(candidate, metadata={
        "is_document": True, "date": "10/06/2026",
    }, source_text=candidate["evidence_index"]["ev-01"]["text"])
    assert sanitized["graph_mutations"]["proposal_updates"] == []


def test_document_signal_extractor_does_not_turn_permit_caveat_into_proposal() -> None:
    candidate = _candidate()
    candidate["graph_mutations"]["proposal_updates"] = []
    candidate["evidence_index"]["ev-01"]["text"] = (
        "Project Star resources are anticipated by 2029, subject to permits."
    )
    promoted, _ = SignalExtractor().extract_and_gate(
        candidate, metadata={"is_document": True}
    )
    assert promoted["graph_mutations"]["proposal_updates"] == []


def test_document_integration_requires_explicit_cited_cue() -> None:
    candidate = _candidate()
    candidate["evidence_index"]["ev-02"] = {
        "exact_text": "Campus A connects to Data Center B."
    }
    candidate["graph_mutations"]["relationship_signals"] = [
        {"signal_id": "unsupported", "predicate": "INTEGRATES_WITH", "evidence_ids": ["ev-01"]},
        {"signal_id": "supported", "predicate": "INTEGRATES_WITH", "evidence_ids": ["ev-02"]},
    ]
    result = json.loads(_quarantine_unsubstantiated_integrations(json.dumps(candidate)))
    assert [x["signal_id"] for x in result["graph_mutations"]["relationship_signals"]] == ["supported"]


def test_document_judge_does_not_release_with_grounding_deductions() -> None:
    # 1. Below rubric floor (grounding 13 < 14) must fail
    scorecard = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 13,
        "composite_score": 93,
        "verdict": "PASS",
    }
    qualified, passed, _ = qualify_document_judge(scorecard)
    assert passed is False
    assert qualified["verdict"] == "FAIL"
    assert any("rubric floor" in f.lower() for f in qualified["quality_failures"])

    # 2. Named grounding defect must fail even if score >= 90
    scorecard_with_defect = dict(scorecard)
    scorecard_with_defect["dimension_6_evidence_grounding_and_schema"] = 16
    scorecard_with_defect["composite_score"] = 96
    scorecard_with_defect["grounding_defects"] = ["Missing citation for signal sig-01"]
    qualified_def, passed_def, _ = qualify_document_judge(scorecard_with_defect)
    assert passed_def is False
    assert qualified_def["verdict"] == "FAIL"

    # 3. Grounded run meeting floor (grounding 14, composite 94) without defects must pass
    scorecard["dimension_6_evidence_grounding_and_schema"] = 14
    scorecard["composite_score"] = 94
    _, passed, _ = qualify_document_judge(scorecard)
    assert passed is True
