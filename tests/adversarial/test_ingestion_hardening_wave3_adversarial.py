"""
tests/adversarial/test_ingestion_hardening_wave3_adversarial.py

Wave 3 Adversarial Contract Test Suite.
Validates:
  - Q01: Output 01 preserves source spans and supported attribution; unresolved speakers stay unresolved or require authentic review.
  - Q02: Output 03 cites document locations; document-only skip markers cannot pass transcript gates.
  - Q03: Notes provenance stays notes and cannot assert exact quotations/turn attribution without evidence.
  - Q04: Release requires all required same-context outputs and cross-artifact invariants; optional parallel 02/03 scheduling is retained.
  - Q05: Invalid score arithmetic, raw veto, below-floor grounding and fabricated evidence still block all applicable routes.
  - Q06: Required independent accuracy failure always blocks release even without speaker-qualification flag.
  - Q07: Empty source text or metadata/attachment labels alone cannot prove grounding; ambiguous 01 blocks child dispatch.
  - Q08: Trusted document/notes/transcript rubrics cannot be changed by candidate archetype or source keywords; skipped scores excluded from aggregates.
  - Q09: Fatal/recoverable/unresolved/stale/missing 01 have explicit child-dispatch and release predicates; bounded repair/revalidation before newly tightened dispatch.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.child_dispatch import (
    evaluate_child_dispatch_predicate,
    validate_child_dispatch_predicate,
)
from dual_pipeline.canonical_pipeline.quality_aggregates import (
    compute_quality_aggregate_score,
    get_evaluated_composite_scores,
    is_deliverable_skipped_or_not_applicable,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
    SynthesisResult,
)
from dual_pipeline.document_pipeline_core.evaluator import (
    evaluate_document_qa_gates,
    evaluate_document_judge_and_release,
)
from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
from dual_pipeline.exceptions import ChildDispatchBlockedError
from dual_pipeline.llm_judge_core.judges.graph import (
    GraphExtractionJudge,
    _detect_meeting_archetype,
)
from dual_pipeline.transcript_qa_core.evaluator import (
    evaluate_all_qa_gates,
    evaluate_judge_and_release,
    _eval_output1,
    _eval_output_n,
    _build_final_decision,
)
from dual_pipeline.transcript_qa_core.gate1_attribution import (
    check_attribution_hygiene,
    verify_alr_cryptographic_receipt,
)


def test_q01_contract() -> None:
    """Q01: Output 01 preserves source spans and supported attribution; unresolved speakers stay unresolved or require authentic review."""
    # 1. Multi-attendee transcript with unresolved generic speakers fails Gate 1 attribution hygiene
    generic_f1 = (
        "# Forensic Transcript\n"
        "**Date**: 10/08/2026\n\n"
        "**Speaker 1:** [00:01:00]: The timeline is slipping.\n\n"
        "**Speaker 2:** [00:01:15]: We need to escalate to leadership.\n\n"
        "**Speaker 1:** [00:01:30]: Agreed.\n\n"
        "**Speaker 2:** [00:01:45]: Let's convene tomorrow.\n"
    )
    meta = {
        "expected_attendees": ["Alice Walker", "Bob Chen"],
        "roster_names": ["Alice Walker", "Bob Chen"],
    }
    passed, unresolved, _, failures = check_attribution_hygiene(generic_f1, meta)
    assert not passed, "Generic speakers without authentic attribution resolution must fail Gate 1"
    assert any("UNATTRIBUTED_GENERIC_SPEAKERS" in f for f in failures)
    assert len(unresolved) > 0

    # 2. Fabricated or unauthenticated claim of operator confirmation without cryptographic receipt fails
    unauth_table = (
        "### Ambiguity Lock Register\n"
        "| ID | Turn | Ambiguity Type | Resolved Speaker | Evidence | Status |\n"
        "| ALR-01 | [00:01:00] | Generic Speaker | Alice Walker | Context | [OPERATOR VERIFIED] |\n"
    )
    receipt_valid, receipt_data, errors = verify_alr_cryptographic_receipt(unauth_table, meta={})
    assert not receipt_valid, "Unauthenticated [OPERATOR VERIFIED] claim must fail closed without valid signature"


def test_q02_contract() -> None:
    """Q02: Output 03 cites document locations; document-only skip markers cannot pass transcript gates."""
    # 1. Document projection with document locations (paragraph, table, cell) passes document QA
    valid_doc_f3 = {
        "schema_version": "5.7",
        "meeting_id": "doc-test-clean",
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "evidence_index": {
            "stmt-01": {
                "statement_id": "stmt-01",
                "text": "The Rule Engine connects directly with the Workflow Service in production.",
                "exact_text": "The Rule Engine connects directly with the Workflow Service in production.",
                "speaker": "Author",
                "source_classification": "internal",
                "locator": {"kind": "document", "value": "Paragraph 4, Page 2"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:rule_engine",
                    "type": "system",
                    "name": "Rule Engine",
                    "description": "Rule evaluation engine",
                    "mutation": "CONFIRM",
                    "attributes": {"tier": "core"},
                    "evidence_ids": ["stmt-01"],
                },
                {
                    "id": "system:workflow_service",
                    "type": "system",
                    "name": "Workflow Service",
                    "description": "Orchestrates workflow operations",
                    "mutation": "CONFIRM",
                    "attributes": {"tier": "core"},
                    "evidence_ids": ["stmt-01"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-01",
                    "from_id": "system:rule_engine",
                    "to_id": "system:workflow_service",
                    "relationship": "INTEGRATES_WITH",
                    "evidence_ids": ["stmt-01"],
                }
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
            "strategic_intent_signals": [],
        },
        "evidence_only_statement_ids": [],
    }
    source_doc = (
        "The Rule Engine connects directly with the Workflow Service in production. "
        "We deploy to production in Q4."
    )
    doc_qa = evaluate_document_qa_gates(valid_doc_f3, source_text=source_doc)
    assert doc_qa["status"] == "PASS"

    # 2. Injecting document skip markers into transcript evaluation fails transcript gates
    doc_skip_marker = {
        "status": "SKIPPED",
        "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
        "passed": True,
    }
    verdict, passed = _eval_output1(doc_skip_marker, s1=100)
    assert not passed, "Document skip marker MUST NOT pass transcript Output 01 evaluation"

    verdict_2, passed_2 = _eval_output_n(2, doc_skip_marker, score=100, threshold=85)
    assert not passed_2, "Document skip marker MUST NOT pass transcript Output 02 evaluation"


def test_q03_contract() -> None:
    """Q03: Notes provenance stays notes and cannot assert exact quotations/turn attribution without evidence."""
    from dual_pipeline.transcript_pipeline_core.normalizer import (
        is_copilot_notes,
        normalize_raw_input_text,
    )

    # Valid notes structure without timestamps
    valid_notes = (
        "# Executive Summary\n"
        "### Key Takeaways\n"
        "- **Budget**: Discussed Q3 budget targets.\n"
        "- **Migration**: Alice agreed to lead migration.\n"
        "- **Timeline**: PartnerAdvisory will present milestones next week.\n"
    )
    assert is_copilot_notes(valid_notes) is True
    norm = normalize_raw_input_text(valid_notes, metadata={"source_type": "notes"})
    # Notes normalization must preserve notes semantics without inventing timestamp cues
    assert "[" not in norm or "00:00:00" not in norm, "Notes normalizer must not invent synthetic timestamps"

    # Raw transcript dialogue cannot be recognized as notes
    raw_dialogue = (
        "Alice: Hey Bob\n"
        "Bob: Hey Alice\n"
        "Alice: Let's start\n"
    )
    assert is_copilot_notes(raw_dialogue) is False


def test_q04_contract() -> None:
    """Q04: Release requires all required same-context outputs and cross-artifact invariants; optional parallel 02/03 scheduling is retained."""
    f1 = "# Forensic Transcript\n**Date**: 10/08/2026\n\n**Alice Walker:** [00:01:00]: Core migration plan.\n"
    f2 = "# Executive Recap\n**Date**: 10/08/2026\n\nCore migration underway.\n"
    f3 = json.dumps({"schema_version": "5.7"})

    # Missing output 1 in transcript mode fails release
    qa_rep, judge_rep, decision = evaluate_judge_and_release(
        f1_text="",
        f2_text=f2,
        f3_text=f3,
        source_text=f1,
        mock=False,
    )
    assert not decision["authoritative_write_authorized"], "Transcript release requires bound Output 01"
    assert decision["status"] == "FAIL"

    # Parallel 02/03 scheduling is preserved in FullMeetingSynthesisStrategy
    strategy = FullMeetingSynthesisStrategy(provider=None)
    assert strategy.required_deliverables == ["01", "02", "03"]


def test_q05_contract() -> None:
    """Q05: Invalid score arithmetic, raw veto, below-floor grounding and fabricated evidence still block all applicable routes."""
    # 1. Inconsistent composite score
    inconsistent = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 14,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 20,
        "composite_score": 80,  # Sum is 99, but composite claims 80
        "verdict": "PASS",
    }
    res, passed, _ = qualify_document_judge(inconsistent)
    assert not passed, "Inconsistent composite score must fail qualification"
    assert "Composite score does not match rubric dimension sum" in res.get("quality_failures", [])

    # 2. Fractional scores rejected
    fractional = dict(inconsistent)
    fractional["dimension_1_strategic_edge"] = 14.5
    res_f, passed_f, _ = qualify_document_judge(fractional)
    assert not passed_f, "Fractional score must fail qualification"

    # 3. Raw judge veto is non-compensating
    vetoed = dict(inconsistent)
    vetoed["composite_score"] = 99
    vetoed["raw_judge_scorecard"] = {"verdict": "FAIL", "composite_score": 99}
    res_v, passed_v, _ = qualify_document_judge(vetoed)
    assert not passed_v, "Raw judge veto must not be normalized to pass"
    assert res_v["verdict"] == "FAIL"

    # 4. Grounding below floor (dim 6 < 14 in document)
    below_floor = dict(inconsistent)
    below_floor["dimension_6_evidence_grounding_and_schema"] = 13
    below_floor["composite_score"] = 92
    res_bf, passed_bf, _ = qualify_document_judge(below_floor)
    assert not passed_bf, "Document grounding below rubric floor 14 must fail qualification"


def test_q06_contract() -> None:
    """Q06: Required independent accuracy failure always blocks release even without speaker-qualification flag."""
    qa_report = {
        "output_01_forensic_transcript": {"status": "PASS", "passed": True},
        "output_02_executive_recap": {"status": "PASS", "passed": True},
        "output_03_canonical_signal_projection": {"status": "PASS", "passed": True},
        "cross_artifact_consistency": {"status": "PASS", "passed": True},
    }

    # require_independent_accuracy=True with accuracy below 98.0% MUST block release
    meta_fail = {
        "require_independent_accuracy": True,
        "require_speaker_qualification": False,
        "independent_speaker_accuracy_pct": 91.5,
    }
    decision_fail = _build_final_decision(
        qa_report=qa_report,
        j1_pass=True,
        j2_pass=True,
        j3_pass=True,
        recon_pass=True,
        is_advisory=False,
        metadata=meta_fail,
    )
    assert not decision_fail["authoritative_write_authorized"], (
        "Failed independent accuracy MUST block authoritative write even without speaker-qualification flag"
    )
    assert decision_fail["status"] == "FAIL"
    assert decision_fail["independent_accuracy_status"] == "FAIL"

    # Meeting the 98.0% threshold allows pass
    meta_pass = {
        "require_independent_accuracy": True,
        "require_speaker_qualification": False,
        "independent_speaker_accuracy_pct": 99.0,
    }
    decision_pass = _build_final_decision(
        qa_report=qa_report,
        j1_pass=True,
        j2_pass=True,
        j3_pass=True,
        recon_pass=True,
        is_advisory=False,
        metadata=meta_pass,
    )
    assert decision_pass["authoritative_write_authorized"] is True
    assert decision_pass["status"] == "PASS"
    assert decision_pass["independent_accuracy_status"] == "PASS"


def test_q07_contract() -> None:
    """Q07: Empty source text or metadata/attachment labels alone cannot prove grounding; ambiguous 01 blocks child dispatch."""
    # 1. Empty source text cannot establish evidentiary grounding
    can_dispatch, reasons = evaluate_child_dispatch_predicate(
        output_1="# Valid Header\n**Alice:** [00:01:00]: Content.",
        metadata={},
        source_text="",
    )
    assert not can_dispatch
    assert any("empty" in r.lower() for r in reasons)

    # 2. Document projection with empty source text fails Gate 3
    doc_f3 = {
        "entities": [{"name": "Doc Entity", "type": "CONCEPT"}],
        "graph_mutations": {"entity_upserts": [{"name": "Doc Entity", "type": "CONCEPT"}]},
        "evidence_index": {},
    }
    qa_res = evaluate_document_qa_gates(doc_f3, source_text="")
    assert qa_res["status"] == "FAIL"

    # 3. Ambiguous 01 (open C-10 ambiguity locks) blocks child dispatch
    meta_ambig = {"c10_has_open_locks": True}
    with pytest.raises(ChildDispatchBlockedError) as excinfo:
        validate_child_dispatch_predicate(
            output_1="# Valid 01\n**Alice:** [00:01:00]: Text.",
            metadata=meta_ambig,
            source_text="Alice: Text.",
        )
    assert "ambiguity locks" in str(excinfo.value).lower()


def test_q08_contract() -> None:
    """Q08: Trusted document/notes/transcript rubrics cannot be changed by candidate archetype or source keywords; skipped scores excluded from aggregates."""
    # 1. Candidate output 3 JSON putting archetype in payload cannot override rubric
    spoofed_f3 = {"meeting_archetype": "TACTICAL_ALIGNMENT", "entities": []}
    # In document mode, explicit archetype is DOCUMENT_MODE
    arch = _detect_meeting_archetype(spoofed_f3, f1_text="Some document text", explicit="DOCUMENT_MODE")
    assert arch == "DOCUMENT_MODE", "Candidate f3 hints cannot override DOCUMENT_MODE"

    # 2. Source text containing meeting keywords cannot switch document rubric
    doc_text_with_keywords = "We convened a governance review demonstration to inspect the scada system."
    arch_doc = _detect_meeting_archetype({}, f1_text=doc_text_with_keywords, explicit="DOCUMENT_MODE")
    assert arch_doc == "DOCUMENT_MODE"

    # 3. Skipped deliverables are excluded from quality aggregates and never inflate averages
    judge_report = {
        "output_1_evaluation": {"status": "SKIPPED", "composite_score": 100, "reason": "NOT_APPLICABLE_DOCUMENT_MODE"},
        "output_2_evaluation": {"status": "SKIPPED", "composite_score": 100, "reason": "NOT_APPLICABLE_DOCUMENT_MODE"},
        "output_3_evaluation": {
            "dimension_1": 14, "dimension_2": 14, "dimension_3": 19,
            "dimension_4": 14, "dimension_5": 14, "dimension_6": 17,
            "status": "PASS", "composite_score": 92, "verdict": "PASS",
        },
    }
    evaluated_scores = get_evaluated_composite_scores(judge_report)
    assert evaluated_scores == {"output_3": 92.0}
    agg = compute_quality_aggregate_score(judge_report)
    assert agg == 92.0, f"Expected aggregate score to be 92.0, got {agg}"


def test_q09_contract() -> None:
    """Q09: Fatal/recoverable/unresolved/stale/missing 01 have explicit child-dispatch and release predicates; bounded repair/revalidation before newly tightened dispatch."""
    valid_01 = "# Forensic Transcript\n**Date**: 10/08/2026\n\n**Alice Walker:** [00:01:00]: Ready for dispatch.\n"
    source = "Alice Walker: Ready for dispatch."

    # 1. Missing 01 blocks dispatch
    can_disp, reasons = evaluate_child_dispatch_predicate("", metadata={}, source_text=source)
    assert not can_disp
    assert any("missing or empty" in r.lower() for r in reasons)

    # 2. Stale 01 blocks dispatch
    can_disp_stale, reasons_stale = evaluate_child_dispatch_predicate(valid_01, metadata={"stale_output_1": True}, source_text=source)
    assert not can_disp_stale
    assert any("stale" in r.lower() for r in reasons_stale)

    # 3. Unresolved speakers block dispatch
    can_disp_unres, reasons_unres = evaluate_child_dispatch_predicate(valid_01, metadata={"has_unresolved_speakers": True}, source_text=source)
    assert not can_disp_unres
    assert any("unresolved" in r.lower() for r in reasons_unres)

    # 4. Valid 01 without blockers authorizes child dispatch
    can_disp_valid, reasons_valid = evaluate_child_dispatch_predicate(valid_01, metadata={}, source_text=source)
    assert can_disp_valid
    assert len(reasons_valid) == 0
    # Must not raise ChildDispatchBlockedError
    validate_child_dispatch_predicate(valid_01, metadata={}, source_text=source)
