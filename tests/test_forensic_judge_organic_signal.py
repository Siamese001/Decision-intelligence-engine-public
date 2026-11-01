"""Unit and contract alignment tests for Forensic Judge organic signal rubric.

Verifies that forensic_checks.py deterministically enforces Intelligent Verbatim standards:
rewarding clean organic transcripts, penalizing surviving fillers/stutters/passive backchannels,
and preserving question responses.
"""
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge_core.forensic_checks import (
    assign_forensic_verdict,
    check_organic_signal_hygiene,
)
from dual_pipeline.prompts.contracts import OUTPUT1_CONTRACT


def test_forensic_judge_clean_organic_transcript_passes() -> None:
    clean_transcript = (
        "# Live Architecture Review -- HITL-Resolved Transcript\n\n"
        "**Date:** 10/14/2026\n"
        "**Participants:**\n"
        "- **PartnerAdvisory:** Amit Ayer (Principal Architect), Alex Sanchez (Tech Lead)\n"
        "- **Apex Global Enterprises:** harper Hoffman (Product Manager)\n\n"
        "---\n\n"
        "## Forensic Transcript\n\n"
        "### [00:01:00]\n\n"
        "**Alex Sanchez:** Today we are reviewing the notification architecture for Project LENS.\n\n"
        "### [00:01:05]\n\n"
        "**Alex Sanchez:** We need to ensure that notifications are dispatched with low latency across all clients.\n\n"
        "### [00:02:15]\n\n"
        "**harper Hoffman:** What about offline message queuing? Any questions on this?\n\n"
        "### [00:02:18]\n\n"
        "**Alex Sanchez:** Yeah.\n\n"
        "### [00:02:20]\n\n"
        "**Alex Sanchez:** Offline messages will be queued in SQLite before syncing to AWS S3 once network recovers.\n\n"
        "### [00:03:00]\n\n"
        "**Amit Ayer:** That I think is part of phase one. We agreed on this during the planning session.\n\n"
        "---\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | LENS Architecture | [00:01:00] | stmt-01 | Confirmed enterprise platform | Domain Glossary |\n"
        "| 2 | SQLite Sync | [00:02:20] | stmt-02 | Confirmed offline cache | Architecture Spec |\n"
        "| 3 | Operator Review | [00:03:00] | stmt-03 | Verified phase one scope | Operator Receipt |\n"
        "| 4 | Speaker Attribution: Alex Sanchez | [00:01:00] | stmt-04 | Verified Tech Lead | Directory Prior |\n"
    )

    passed, defect_codes, metrics = check_organic_signal_hygiene(clean_transcript)
    assert passed is True
    assert len(defect_codes) == 0
    assert metrics["total_disfluencies"] == 0

    base_scorecard: Dict[str, Any] = {
        "dimension_1_forensic_grounding_and_fidelity": 25,
        "dimension_2_attribution_and_room_mic_hygiene": 25,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 25,
        "defect_codes": [],
        "forensic_rationale": "High fidelity verbatim dialogue.",
    }
    evaluated = assign_forensic_verdict(base_scorecard, f1_text=clean_transcript)
    assert evaluated["dimension_4_asr_cleanup_and_domain_preservation"] == 25
    assert evaluated["verdict"] == "PASS"


def test_forensic_judge_detects_surviving_fillers_and_stutters() -> None:
    filler_transcript = (
        "## Forensic Transcript\n\n"
        "### [00:01:00]\n\n"
        "**Alex Sanchez:** Um, today we are, uh, reviewing the notification architecture.\n\n"
        "### [00:01:10]\n\n"
        "**Eduardo Juan More:** It -it -it -that is make make the changes right now, ugh.\n\n"
        "### [00:01:20]\n\n"
        "**Alex Sanchez:** Hmm, let me check the, er, logs.\n"
    )

    passed, defect_codes, metrics = check_organic_signal_hygiene(filler_transcript)
    assert passed is False
    assert "ERR_UNRESOLVED_DISFLUENCIES" in defect_codes
    assert metrics["filler_count"] >= 4
    assert metrics["stutter_count"] >= 1

    base_scorecard: Dict[str, Any] = {
        "dimension_1_forensic_grounding_and_fidelity": 25,
        "dimension_2_attribution_and_room_mic_hygiene": 25,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 25,
    }
    evaluated = assign_forensic_verdict(base_scorecard, f1_text=filler_transcript)
    assert evaluated["dimension_4_asr_cleanup_and_domain_preservation"] <= 15
    assert "ERR_UNRESOLVED_DISFLUENCIES" in evaluated["defect_codes"]
    assert "ORGANIC SIGNAL DEFECT" in evaluated["forensic_rationale"]


def test_forensic_judge_catches_passive_backchannels_while_preserving_answers() -> None:
    transcript_with_backchannel = (
        "## Forensic Transcript\n\n"
        "### [00:00:48]\n\n"
        "**Amit Ayer:** OK.\n\n"
        "### [00:01:00]\n\n"
        "**Alex Sanchez:** Today we are reviewing the notification architecture.\n\n"
        "### [00:02:00]\n\n"
        "**Alex Sanchez:** Any questions on this design?\n\n"
        "### [00:02:05]\n\n"
        "**Amit Ayer:** Yeah.\n"
    )

    passed, defect_codes, metrics = check_organic_signal_hygiene(transcript_with_backchannel)
    assert passed is False
    assert "ERR_PASSIVE_BACKCHANNELS" in defect_codes
    # Only the first "OK." should be flagged; "Yeah." directly answers the question
    assert metrics["backchannel_count"] == 1
    assert "Amit Ayer: OK." in metrics["defects"][0]


def test_prompt_contracts_and_ssot_alignment() -> None:
    d4 = next(d for d in OUTPUT1_CONTRACT.rubric_dimensions if d.key == "dimension_4_asr_cleanup_and_domain_preservation")
    assert "Cleaned disfluencies" in d4.criteria

    repo_root = Path(__file__).resolve().parent.parent

    p1 = (repo_root / "config" / "prompts" / "synthesis" / "stages" / "01_forensic_normalizer.md").read_text()
    assert "Intelligent Verbatim" in p1
    assert 'Strip non-lexical vocalized fillers ("ugh", "um"' in p1
    assert "Preserve 100% verbatim spoken dialogue including false starts, fillers" not in p1

    p2 = (repo_root / "config" / "prompts" / "synthesis" / "stages" / "01_teams_raw_normalizer.md").read_text()
    assert "Intelligent Verbatim" in p2
    assert "strip non-lexical vocalized fillers" in p2

    p3 = (repo_root / "governance" / "decision_intelligence" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md").read_text()
    assert "C-31" in p3
    assert "Intelligent Verbatim Organic Signal Invariant" in p3

    p4 = (repo_root / "governance" / "decision_intelligence" / "FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md").read_text()
    assert "Intelligent Verbatim" in p4
    assert 'surviving vocalized fillers ("ugh", "um"' in p4
