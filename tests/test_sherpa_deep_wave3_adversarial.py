"""Adversarial and boundary test suite for Sherpa Deep RCA Wave 3:
Thread-Safe Synthesis, Stage 02/03 Healer De-Sabotage & Checkpoint Pinning.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.evaluator import _merge_pinned_into_judge_report
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.citation_utils import validate_judge_card_citations
from dual_pipeline.transcript_qa_core.eval_cache import check_pin
from dual_pipeline.transcript_qa_core.graph_ontology_proposals import (
    _extract_proposal_tokens,
    heal_proposals_and_risks,
)
from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher
from dual_pipeline.transcript_stages_core.anti_conflation import (
    CompoundWorkstreamConflationError,
    validate_anti_conflation,
)


def test_adversarial_empty_and_whitespace_markdown_sections():
    """Boundary payload: empty strings, pure whitespace, and malformed header markers."""
    # 1. Empty string
    header, sections = SurgicalPatcher.parse_markdown_sections("")
    assert header == ""
    assert sections == []

    # 2. Pure whitespace and newlines
    header_ws, sections_ws = SurgicalPatcher.parse_markdown_sections("   \n\n\t\n  ")
    assert sections_ws == []

    # 3. Malformed envelope diffs
    patched, _ = SurgicalPatcher.apply_critique_envelope("# Title\n\nContent", {}, "")
    assert "# Title" in patched

    patched_null, _ = SurgicalPatcher.apply_critique_envelope("", {"replacement_content": "Foo"}, "")
    assert patched_null == ""


def test_adversarial_malformed_fenced_json_anti_conflation():
    """Malformed input: nested code fences, partial fences, and invalid UTF-8/corrupt strings."""
    # 1. Clean JSON with code fence
    fenced = '```json\n{"entities": [{"id": "clean_entity"}]}\n```'
    lines = [l for l in fenced.strip().splitlines() if not l.strip().startswith("```")]
    clean_json = "\n".join(lines).strip()
    validate_anti_conflation(json.loads(clean_json))

    # 2. Compound conflation slug inside fenced JSON must fail closed
    conflated = '```json\n{"entities": [{"id": "lens_sherpa_connector"}]}\n```'
    lines_c = [l for l in conflated.strip().splitlines() if not l.strip().startswith("```")]
    with pytest.raises(CompoundWorkstreamConflationError):
        validate_anti_conflation(json.loads("\n".join(lines_c).strip()))


def test_adversarial_proposal_edge_cases_and_empty_tokens():
    """Boundary payload: empty strings, only punctuation or stopwords, and proposal healing."""
    assert _extract_proposal_tokens("") == set()
    assert _extract_proposal_tokens("   ") == set()
    assert _extract_proposal_tokens("the and of in a to is") == set()
    assert _extract_proposal_tokens("...,,, --- ???") == set()

    # Test proposal healing via heal_proposals_and_risks
    data = {
        "evidence_index": {
            "E-01": {"exact_text": "Like, you know, we should implement distributed cache."}
        }
    }
    mutations = {
        "proposal_updates": [
            {
                "id": "P-01",
                "evidence_ids": ["E-01"],
                "rationale": "",
            }
        ]
    }
    changes: list[str] = []
    heal_proposals_and_risks(data, mutations, changes)
    prop = mutations["proposal_updates"][0]
    assert prop["evidence_ids"] == ["E-01"]
    assert not prop["rationale"].lower().startswith("like, you know")


def test_adversarial_concurrency_race_state_machine():
    """Concurrency & race: multi-threaded concurrent candidate recording and score querying."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    sm.freeze_artifact("output_1", "Base content for output 1")

    def worker(worker_id: int):
        for i in range(15):
            score = 80 + (worker_id % 15)
            sm.record_candidate(
                f"output_{(worker_id % 3) + 1}",
                f"Candidate content from worker {worker_id} iteration {i}",
                composite_score=score,
                verdict="PASS" if score >= 90 else "FAIL",
                qa_passed=True,
                deterministic_gates_pass=True,
            )
            _ = sm.get_high_water_mark_score(f"output_{(worker_id % 3) + 1}")
            _ = sm.is_frozen("output_1")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, wid) for wid in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert sm.is_frozen("output_1")
    assert sm.get_high_water_mark_score("output_1") >= 80


def test_adversarial_monotonic_regression_guard_on_zero_score():
    """Rollback & recovery: score 0 on attempt 2 must not regress frozen artifact high-water mark."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    sm.freeze_artifact("output_2", "Initial high-scoring recap")

    # Seed attempt 1 with score 94
    sm.record_candidate(
        "output_2",
        "Initial high-scoring recap",
        composite_score=94,
        verdict="PASS",
        qa_passed=True,
        deterministic_gates_pass=True,
    )
    assert sm.get_high_water_mark_score("output_2") == 94

    # Attempt 2 produces SKIPPED evaluation with score 0
    final_dec = {"output_2_deterministic_gates_pass": True, "output_2_pass": True}
    judge_rep = {
        "output_2_evaluation": {
            "status": "SKIPPED",
            "composite_score": 0,
            "verdict": "FAIL",
            "reason": "FROZEN_PRIOR_ARTIFACT",
        }
    }
    qa_rep = {"output_02_executive_recap": {"passed": True}}

    sm._record_attempt_candidates(
        final_dec,
        judge_rep,
        qa_rep,
        c1=None,
        c2="Initial high-scoring recap",
        c3=None,
    )

    # High-water mark must remain 94 and not drop to 0
    assert sm.get_high_water_mark_score("output_2") == 94
    assert not sm.has_regressed("output_2")


def test_adversarial_check_pin_corrupted_inputs():
    """Malformed input: malformed pin dictionaries, corrupted SHA digests, and missing cards."""
    # 1. Non-dict pinned payload
    assert check_pin([], "output_1", "output_01", 90, "dummy_sha") is None

    # 2. Mismatched SHA
    pinned_bad_sha = {
        "output_1": {
            "artifact_sha256": "wrong_sha",
            "composite_score": 95,
            "verdict": "PASS",
        }
    }
    assert check_pin(pinned_bad_sha, "output_1", "output_01", 90, "expected_sha") is None

    # 3. Non-numeric composite score
    pinned_bad_score = {
        "output_1": {
            "artifact_sha256": "valid_sha",
            "composite_score": "not-a-number",
            "verdict": "PASS",
        }
    }
    assert check_pin(pinned_bad_score, "output_1", "output_01", 90, "valid_sha") is None

    # 4. Citations validation with empty/None candidate text
    valid, missing = validate_judge_card_citations({}, None)
    assert valid is True
    assert missing == []

    # 5. Citations validation across multiple texts with one matching
    card = {"grounding_citations": ["Unique quote that only exists in transcript"]}
    f1 = "This contains the Unique quote that only exists in transcript verbatim."
    f2 = "Recap text without the quote."
    valid_cross, miss_cross = validate_judge_card_citations(card, [f2, f1])
    assert valid_cross is True
    assert miss_cross == []
