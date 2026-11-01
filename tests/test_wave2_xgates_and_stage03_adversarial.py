"""
Adversarial and edge-case tests for Wave 2:
- Category 1 (Invariant bypass attempt): X2 whitelist bypass ('Unassigned (Pending Owner Assignment)', 'Team', 'TBD' timing); X3 DRI mismatch between Output 02 and Output 03; unauthenticated HUMAN_OPERATOR role resolution without digest.
- Category 2 (Malformed input): Corrupted quote_pointer slice where length matches exact_text but transcript[start:end] != exact_text; ActionUpdate with 'Unassigned' owner_id.
- Category 3 (Boundary payload): Explicit empty RiskUpdate(evidence_ids=[]) preserved without fabricating ['ev-01']; missing roster attendee in Output 03 rejected instead of healed as 'PartnerAdvisory'.
- Category 4 (Exact production regression): Multi-workstream C-28 conflation across Jarvis / DocIQ and Chakra / Sherpa.
"""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from dual_pipeline.transcript_models import ActionUpdate, RiskUpdate
from dual_pipeline.transcript_stages_core.anti_conflation import (
    CompoundWorkstreamConflationError,
    validate_anti_conflation,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import reconcile_speaker_role_resolutions
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


def _base_bundle() -> tuple[str, str, Dict[str, Any]]:
    transcript = "### [00:01:00]\n\n**Alex Rivera:** We will deploy the LENS rule engine by 10/25/2026."
    quote = "We will deploy the LENS rule engine by 10/25/2026."
    start = transcript.index(quote)
    end = start + len(quote)
    recap = (
        "# Executive Recap\n\n"
        "## 1. Meeting Objective\nObjective.\n\n"
        "## 2. Action & Governance Ledger\n"
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 1 | Action | Deploy LENS rule engine | Alex Rivera | 10/25/2026 | [00:01:00] | Open |\n"
    )
    proj: Dict[str, Any] = {
        "schema_version": "5.7",
        "meeting_id": "mtg-adv-01",
        "meeting_date": "10/17/2026",
        "source_file": "01_test.md",
        "classification": "CLIENT_SAFE",
        "audiences": ["CLIENT_SAFE"],
        "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:alex_rivera",
                    "type": "PERSON",
                    "name": "Alex Rivera",
                    "description": "Engineering Lead",
                    "mutation": "CONFIRM",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        },
        "evidence_index": {
            "ev-01": {
                "text": quote,
                "exact_text": quote,
                "speaker": "Alex Rivera",
                "timestamp": "00:01:00",
                "quote_pointer": [start, end],
            }
        },
    }
    return transcript, recap, proj


@pytest.mark.parametrize(
    "bad_owner,bad_timing,expected_msg",
    [
        ("Unassigned (Pending Owner Assignment)", "10/25/2026", "not a resolved person"),
        ("Team", "10/25/2026", "not a resolved person"),
        ("Alex Rivera", "TBD", "ungrounded TBD"),
        ("Alex Rivera", "TBD - Unspecified", "ungrounded TBD"),
    ],
)
def test_x2_rejects_whitelisted_unassigned_owner_and_tbd_timing(
    bad_owner: str, bad_timing: str, expected_msg: str
) -> None:
    """Invariant bypass attempt: X2 must fail closed on 'Unassigned (Pending Owner Assignment)', 'Team', and 'TBD' timing."""
    transcript, _, proj = _base_bundle()
    bad_recap = (
        "# Executive Recap\n\n"
        "## 1. Meeting Objective\nObjective.\n\n"
        "## 2. Action & Governance Ledger\n"
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        f"| 1 | Action | Deploy LENS rule engine | {bad_owner} | {bad_timing} | [00:01:00] | Open |\n"
    )
    evaluator = XExitGatesEvaluator()
    with pytest.raises(ValueError, match=expected_msg):
        evaluator.execute(recap=bad_recap, graph_json=json.dumps(proj), metadata={}, transcript=transcript)


def test_x3_rejects_missing_roster_attendee_instead_of_fabricating_partner_advisory_entity() -> None:
    """Boundary/Bypass: missing roster attendee must fail X3 instead of being silently healed as a PartnerAdvisory employee."""
    transcript, recap, proj = _base_bundle()
    evaluator = XExitGatesEvaluator()
    with pytest.raises(ValueError, match="X3 FAIL.*Attendee 'harper Hoffman'"):
        evaluator.execute(
            recap=recap,
            graph_json=json.dumps(proj),
            metadata={"attendees": ["Alex Rivera", "harper Hoffman"]},
            transcript=transcript,
        )


def test_x3_rejects_o2_action_owner_missing_from_o3_entities() -> None:
    """Invariant bypass attempt: O2 Action Ledger owner not in O3 person entities must fail X3 DRI parity."""
    transcript, recap, proj = _base_bundle()
    recap_drift = recap.replace("| Alex Rivera |", "| Ghost Owner |")
    evaluator = XExitGatesEvaluator()
    with pytest.raises(ValueError, match="X3 FAIL.*Action Item DRI Parity.*Ghost Owner"):
        evaluator.execute(
            recap=recap_drift,
            graph_json=json.dumps(proj),
            metadata={"attendees": ["Alex Rivera"]},
            transcript=transcript,
        )


def test_x3_rejects_wrong_quote_pointer_offset_even_when_length_matches() -> None:
    """Malformed input: quote_pointer with matching length (end - start == len) but wrong offset in transcript must fail X3."""
    transcript, recap, proj = _base_bundle()
    quote = proj["evidence_index"]["ev-01"]["exact_text"]
    proj["evidence_index"]["ev-01"]["quote_pointer"] = [0, len(quote)]  # Wrong start offset, exact same length!
    evaluator = XExitGatesEvaluator()
    with pytest.raises(ValueError, match="X3 FAIL.*Quote Pointer Integrity.*slice f1_text"):
        evaluator.execute(
            recap=recap,
            graph_json=json.dumps(proj),
            metadata={"attendees": ["Alex Rivera"]},
            transcript=transcript,
        )


def test_stage03_models_reject_unassigned_action_owner_and_preserve_empty_risk_evidence() -> None:
    """Malformed/Boundary: ActionUpdate rejects 'Unassigned' owner_id; RiskUpdate(evidence_ids=[]) does not fabricate ['ev-01']."""
    with pytest.raises(ValidationError, match="not a resolved person or role"):
        ActionUpdate(action_id="act:1", owner_id="Unassigned (Pending Owner Assignment)")

    r = RiskUpdate(risk_id="risk:test", status="OPEN", description="Test risk", evidence_ids=[])
    assert r.evidence_ids == []


def test_speaker_role_reconciler_rejects_unsigned_human_operator_claim() -> None:
    """Invariant bypass attempt: claiming HUMAN_OPERATOR without digest or resolution_id must raise ValueError."""
    proj: Dict[str, Any] = {"graph_mutations": {"entity_upserts": [], "relationship_signals": []}, "evidence_index": {}}
    meta = {
        "hitl_role_resolutions": {
            "Alex Rivera": {
                "resolved_role": "VP",
                "provenance": "HUMAN_OPERATOR",
                "is_human_confirmed": True,
                # Neither digest nor resolution_id provided!
            }
        }
    }
    with pytest.raises(ValueError, match="Unauthenticated HUMAN_OPERATOR role resolution"):
        reconcile_speaker_role_resolutions(proj, meta)


@pytest.mark.parametrize(
    "conflated_text",
    [
        "# Workstream: Project Jarvis / DocIQ\n",
        "workstream:jarvis_dociq",
        "workstream:chakra/sherpa",
        "### Insights Hub & LENS Architecture",
    ],
)
def test_anti_conflation_detects_non_lens_sherpa_workstream_compounds(conflated_text: str) -> None:
    """Exact production regression: C-28 anti-conflation must reject compound workstreams beyond LENS/Sherpa."""
    with pytest.raises(CompoundWorkstreamConflationError):
        validate_anti_conflation(conflated_text, context_label="test")
