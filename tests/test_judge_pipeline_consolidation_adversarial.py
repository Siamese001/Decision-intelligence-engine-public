"""
Adversarial and edge-case tests for Wave 6:
Judge Pipeline Consolidation, Appendix Floor Restoration & Evaluation Cache Pinning.

Covers >= 3 mandatory edge-case categories:
1. Boundary payload (non-frozen card with composite_score=None rejected; thin CO_LEAD speaker card rejected)
2. Malformed input (mock string bypass removed in QASwarm; corrupted turn headers rejected)
3. Invariant bypass attempt (unsigned/cross-subject HITL clarification rejected; read-only X-exit gates)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge_core.judge_clarification import _find_prior_signed_hitl_clarification
from dual_pipeline.transcript_qa_core.appendix_standard import (
    SpeakerTier,
    _audit_individual_speaker_section,
)
from dual_pipeline.transcript_qa_core.eval_pin_validator import check_pin
from dual_pipeline.transcript_qa_core.gate1 import _check_vocabulary_preservation
from dual_pipeline.transcript_qa_core.gate1_attribution import _check_participant_corporate_affiliations
from dual_pipeline.transcript_qa_core.qa_swarm import QASwarm
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


def test_qa_swarm_rejects_mock_string_bypass() -> None:
    """QASwarm must evaluate inputs deterministically without short-circuiting 'mock f1'."""
    swarm = QASwarm()
    res = swarm.evaluate_all("mock f1", "mock f2", "not valid json")
    assert res["swarm_passed"] is False
    assert res["agent_results"]["Output 3 Schema Validator"]["status"] == "FAIL"


def test_appendix_co_lead_thin_section_is_not_downgraded_to_contributor() -> None:
    """CO_LEAD speaker card with 20 words and 1 non-roll-call theme must fail C-21 word/theme floors."""
    thin_section = (
        "### **Alice Smith** (VP Engineering, Client)\n"
        "- **Architecture Strategy**: Discussed the high-level migration timeline and cloud infrastructure targets for Q4."
    )
    res = _audit_individual_speaker_section(
        thin_section,
        dur_val=30,
        speaker_tier=SpeakerTier.CO_LEAD,
    )
    assert res is not None
    _, body_words, themes, failure, _ = res
    assert body_words < 120
    assert len(themes) == 1
    assert failure is not None
    assert "GATE_2_8_THIN_SPEAKER_SECTION" in failure


def test_check_pin_rejects_non_frozen_missing_composite_score() -> None:
    """check_pin must reject a non-frozen card with composite_score=None even if verdict is PASS."""
    pinned: Dict[str, Any] = {
        "output_1": {
            "artifact_sha256": "a" * 64,
            "composite_score": None,
            "verdict": "PASS",
            "reason": "NORMAL_EVAL",
        }
    }
    assert check_pin(pinned, "output_1", "output_01", 90, "a" * 64) is None


def test_judge_clarification_rejects_unsigned_and_mismatched_subject(tmp_path: Path) -> None:
    """_find_prior_signed_hitl_clarification rejects unsigned entries and mismatched subject_sha256 even on resume."""
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    unsigned_records = [
        {
            "category": "judge_deduction_clarification",
            "selected_key": "AFFIRM_VALID",
            "prompt": "Clarify deduction on output_2_evaluation: test",
            "subject_sha256": "b" * 64,
        },
        {
            "subject_sha256": "c" * 64,
            "payload": {
                "case_ids": ["JUDGE_CLARIFICATION_OUTPUT_2"],
                "decision": "APPROVE",
                "subject_sha256": "c" * 64,
            },
            "signature": "forged_signature",
        },
    ]
    ledger_path.write_text(json.dumps(unsigned_records), encoding="utf-8")
    meta: Dict[str, Any] = {
        "hitl_ledger_path": str(ledger_path),
        "resume": True,
        "subject_sha256": "a" * 64,
    }
    found = _find_prior_signed_hitl_clarification(meta, None, "output_2")
    assert found is None


def test_x_exit_gates_persist_helpers_are_read_only(tmp_path: Path) -> None:
    """XExitGatesEvaluator._persist_healed_recap and _persist_healed_graph must not mutate files on disk."""
    f2_file = tmp_path / "02_test_executive_recap.md"
    f3_file = tmp_path / "03_test_canonical_signal_projection.json"
    f2_file.write_text("original recap", encoding="utf-8")
    f3_file.write_text('{"original": true}', encoding="utf-8")

    evaluator = XExitGatesEvaluator()
    meta = {
        "output_dir": str(tmp_path),
        "output_02_path": str(f2_file),
        "output_03_path": str(f3_file),
    }
    evaluator._persist_healed_recap("mutated recap", meta)
    evaluator._persist_healed_graph({"original": False}, meta)

    assert f2_file.read_text(encoding="utf-8") == "original recap"
    assert json.loads(f3_file.read_text(encoding="utf-8")) == {"original": True}


def test_gate1_vocabulary_and_affiliation_are_corpus_neutral() -> None:
    """Gate 1 vocabulary and corporate affiliation checks derive strictly from metadata without corpus literals."""
    passed, present, failures = _check_vocabulary_preservation(
        "Discussion of Garbaria and LAMS.",
        "Discussion of project timeline.",
        {},
    )
    assert passed is True
    assert present == {}
    assert failures == []

    affil_failures = _check_participant_corporate_affiliations(
        "**Participants:**\n- **Client**: Alice Consultant\n---\n",
        False,
        {"roster": [{"name": "Alice Consultant", "organization": "PartnerAdvisory"}]},
    )
    assert len(affil_failures) == 1
    assert "MISATTRIBUTED_CORPORATE_AFFILIATION" in affil_failures[0]
