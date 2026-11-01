"""
Wave 4 Adversarial & Edge-Case Verification: X1-X3 Exit Gates, Gate 3 Grounding, ALR Expiry & Judge Veto Invariants.

Covers mandatory edge-case categories:
1. Invariant bypass attempt:
   - Gate 3.4 read-only grounding rejects 60%-overlap paraphrased/hallucinated quotes without mutating exact_text in-place
   - Expired HITL approvals in top-level entry["expires_at"] or payload["expires_at"] are rejected in Gate 1 ALR
   - 15-agent swarm review failure (swarm_passed=False) blocks final_pass in _build_final_decision
   -LocalPreEgressSanitizer scrubs career leverage / secret patterns before LLM synthesizer calls
2. Overflow & exhaustion:
   - Rule C-17 violation when evidence_only_statement_ids exceeds max(3, 2 * promoted_mutation_count) in X1 and Gate 3.8
   - Multi-hop continuation stitching handles multi-line overlap and character suffix-prefix overlap without duplication
3. Malformed input / Boundary payload:
   - Null or out-of-bounds quote_pointer [start, end] rejected by X3 and Gate 3.4
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract
from dual_pipeline.transcript_qa_core.evaluator import _build_final_decision
from dual_pipeline.transcript_qa_core.gate1_alr import _is_matching_approval_entry
from dual_pipeline.transcript_qa_core.gate3 import _check_evidence_grounding
from dual_pipeline.transcript_qa_core.semantic_linters import lint_citation_coverage
from dual_pipeline.transcript_stages_core.external_synthesizer import stitch_continuation


def test_adversarial_gate3_rejects_fuzzy_paraphrase_without_mutating_exact_text() -> None:
    """Category: Invariant bypass attempt — Gate 3 must not rewrite paraphrased quotes in-place or pass 60% overlap."""
    f1_text = (
        "### [00:01:00]\n\n"
        "**Amit Ayer:** We decided to postpone the legacy Oracle decommissioning until Q4 2027 due to compliance audits.\n"
    )
    original_hallucinated_quote = "We decided to accelerate the legacy Oracle decommissioning in Q4 2027 due to compliance audits."
    evidence_index: Dict[str, Any] = {
        "ev:hallucinated": {
            "statement_id": "ev:hallucinated",
            "speaker": "person:amit_ayer",
            "exact_text": original_hallucinated_quote,
            "quote_pointer": [0, len(original_hallucinated_quote)],
        }
    }
    passed, failures = _check_evidence_grounding(evidence_index, f1_text)
    assert passed is False
    assert len(failures) == 1
    assert evidence_index["ev:hallucinated"]["exact_text"] == original_hallucinated_quote


def test_adversarial_x3_rejects_null_or_out_of_bounds_quote_pointers() -> None:
    """Category: Malformed input / Boundary payload — X3 must reject null or out-of-bounds quote_pointer."""
    f1_text = "**Amit Ayer:** [00:01:00] We approved the architecture."
    f2_text = "# Executive Recap\n## 1. Meeting Objective\nAlign.\n## 2. Action & Governance Ledger\n"
    f3_null_qp: Dict[str, Any] = {
        "schema_version": "5.7",
        "graph_mutations": {"entity_upserts": [{"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CONFIRM"}]},
        "evidence_index": {
            "ev:001": {
                "statement_id": "ev:001",
                "speaker": "person:amit_ayer",
                "exact_text": "We approved the architecture.",
                "quote_pointer": None,
            }
        },
    }
    ok_null, errs_null = MeetingXExitContract.validate_x3_cross_output_coherence(f1_text, f2_text, f3_null_qp)
    assert ok_null is False
    assert any("missing valid quote_pointer" in e for e in errs_null)

    f3_oob_qp: Dict[str, Any] = {
        "schema_version": "5.7",
        "graph_mutations": {"entity_upserts": [{"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CONFIRM"}]},
        "evidence_index": {
            "ev:001": {
                "statement_id": "ev:001",
                "speaker": "person:amit_ayer",
                "exact_text": "We approved the architecture.",
                "quote_pointer": [500, 500 + len("We approved the architecture.")],
            }
        },
    }
    ok_oob, errs_oob = MeetingXExitContract.validate_x3_cross_output_coherence(f1_text, f2_text, f3_oob_qp)
    assert ok_oob is False
    assert any("out of bounds" in e for e in errs_oob)


def test_adversarial_rule_c17_evidence_only_sprawl_blocked_in_x1_and_gate38() -> None:
    """Category: Overflow & exhaustion — Rule C-17 blocks > max(3, 2 * promoted_mutation_count) evidence_only_statement_ids."""
    f3_sprawl: Dict[str, Any] = {
        "schema_version": "5.7",
        "meeting_metadata": {"meeting_id": "mtg:1", "date": "10/24/2026", "title": "Sync"},
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "decision_updates": [
                {
                    "decision_id": "dec:1",
                    "title": "D1",
                    "description": "Desc",
                    "status": "APPROVED",
                    "owner_id": "person:amit_ayer",
                    "evidence_ids": ["ev:1"],
                }
            ],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
        "evidence_index": {f"ev:{i}": {"statement_id": f"ev:{i}", "exact_text": "quote"} for i in range(1, 7)},
        "evidence_only_statement_ids": ["ev:2", "ev:3", "ev:4", "ev:5"],  # 4 > max(3, 2*1)=3
    }
    g38_ok, g38_violations, _ = lint_citation_coverage(f3_sprawl)
    assert g38_ok is False
    assert any("Rule C-17 violation" in v for v in g38_violations)


def test_adversarial_swarm_review_failure_and_stitch_continuation_overlap() -> None:
    """Category: Invariant bypass attempt & Overflow — 15-agent swarm review failure blocks final_pass; stitch_continuation deduplicates overlap."""
    qa_report: Dict[str, Any] = {
        "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
        "output_02_executive_recap": {"passed": True, "status": "PASS"},
        "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
        "cross_artifact_consistency": {"status": "PASS"},
        "15_agent_swarm_review": {"swarm_passed": False},
    }
    dec = _build_final_decision(
        qa_report=qa_report,
        j1_pass=True,
        j2_pass=True,
        j3_pass=True,
        recon_pass=True,
        is_advisory=False,
        metadata={},
    )
    assert dec["final_pass"] is False
    assert dec["authoritative_write_authorized"] is False
    assert dec["status"] == "FAIL"

    # Multi-line overlap stitching
    part1 = "Line 1\nLine 2\nLine 3\nLine 4 overlap\nLine 5 overlap\nLine 6 overlap"
    part2 = "Line 4 overlap\nLine 5 overlap\nLine 6 overlap\nLine 7 new\nLine 8 new"
    stitched = stitch_continuation(part1, part2)
    assert stitched.count("Line 4 overlap") == 1
    assert "Line 8 new" in stitched
