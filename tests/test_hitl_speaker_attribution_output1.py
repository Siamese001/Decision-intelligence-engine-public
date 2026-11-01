"""
Tests for Output 1 Ambiguity Lock Register and detailed HITL speaker attribution.
Verifies Gate 1.7, ForensicTranscriptJudge, and format_ambiguity_lock_register.
"""

import pytest
from dual_pipeline.llm_judge_core.forensic import ForensicTranscriptJudge

from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate1_attribution import (
    check_ambiguity_lock_register_and_speaker_hitl,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import format_ambiguity_lock_register

CHAKRA_GOLD_OUTPUT_1 = """# NEA_Chakra_Operating_Model — Forensic Transcript

**Date:** 08/27/2026
**Platform Reference:** Microsoft Teams Client Working Session
**Participants:**
Cameron Mercer, Drew Oakley, Amit Ayer, Chakra Sankaraiah, Mark McCarthy

---

**Cameron Mercer (PartnerAdvisory):** [00:00:10] Good morning everyone, thanks for making time.
**Chakra Sankaraiah (NEA / Apex Global Enterprises):** [00:00:25] Morning Stefan, looking forward to reviewing the operating model.
**Drew Oakley (PartnerAdvisory):** [00:00:45] We want to make sure the team structure aligns with delivery velocity.
**Amit Ayer (PartnerAdvisory):** [00:01:05] The architecture decoupling will reduce lead times across streams.
**Mark McCarthy (NEA):** [00:01:30] Agreed, let's look at the tooling requirements.

---

## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker Attribution & Acoustic Grounding | [00:00:00–01:45:14] | stmt-spk-01 | Cameron Mercer (PartnerAdvisory), Drew Oakley (PartnerAdvisory), Amit Ayer (PartnerAdvisory), Chakra Sankaraiah (NEA) verified against attendee roster and conversational turns. | Multi-Tier Acoustic Isolation Protocol |
| 2 | Post-Meeting Debrief Attribution | [01:03:00–01:45:14] | stmt-09, stmt-10 | Jince Jacob remains in room with PartnerAdvisory team (Drew Oakley, Cameron Mercer, Amit Ayer). | Conversational Context & Roster Verification |
| 3 | Tooling Lexicon & Acronym Disambiguation | [00:18:38–01:10:00] | stmt-04, stmt-06 | Canonical technical terminology confirmed: Kiro is AWS-hosted AI IDE; WinSurf is Codeium Windsurf. | Domain Lexicon & Acronym Disambiguation |
"""


LENS_GOLD_OUTPUT_1 = """# Subject: Project LENS Catchup Working Session -- SOW 1 Observability | 09/14/2026

**Date:** 09/14/2026
**Time:** 05:02 PM - 05:39 PM ET
**Platform / Reference:** Microsoft Teams Client Working Session
**Participants:**
harper Hoffman, Brett Gavagni, Jennifer Pan, Alex Sanchez, Amit Ayer

---

## Ambiguity Lock Register
| Ambiguity ID | Spoken Text / Phrase | Context | Resolution / Forensic Binding | Confidence |
| :--- | :--- | :--- | :--- | :--- |
| ALR-01 | "lack of observ- visibility is the number one issue" [11:12] | Institutional failure mode at NEA | Brett Gavagni & harper Hoffman citing 7-year history of tools abandoned. | HIGH (99%) |
| ALR-02 | "at what point do we call SOW 1 complete" [15:12] | SOW 1 vs SOW 2 boundaries | Alex Sanchez & Jennifer Pan seeking firm acceptance criteria for SOW 1. | HIGH (98%) |
| ALR-03 | "Speaker attribution grounding" [00:00] | Attribution across 5 participants | Jennifer Pan, Alex Sanchez, Amit Ayer, harper Hoffman, Brett Gavagni grounded to dialogue turns. | HIGH (99%) |

---

## Transcript

**Jennifer Pan:** [00:00] We're focusing on quality and accuracy.
**harper Hoffman:** [00:16] Yes, reprocessing work needs visibility.
**Alex Sanchez:** [01:20] Rule validation is the key issue.
**Brett Gavagni:** [11:12] Lack of observability is the number one issue.
**Amit Ayer:** [16:40] We need one bounded, business-readable evidence record per rule decision.
"""


def test_chakra_gold_standard_alr_passes():
    """Verify Chakra gold-standard Output 1 passes Gate 1.7."""
    meta = {"roster": ["Cameron Mercer", "Drew Oakley", "Amit Ayer", "Chakra Sankaraiah", "Mark McCarthy"]}
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(CHAKRA_GOLD_OUTPUT_1, meta)
    assert passed is True, f"Expected Chakra to pass ALR check but failed: {failures}"
    assert details["present"] is True
    assert details["item_count"] >= 3
    assert details["speaker_attribution_grounded"] is True
    assert len(failures) == 0


def test_lens_gold_standard_alr_passes():
    """Verify Project Lens gold-standard Output 1 passes Gate 1.7."""
    meta = {"roster": ["harper Hoffman", "Brett Gavagni", "Jennifer Pan", "Alex Sanchez", "Amit Ayer"]}
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(LENS_GOLD_OUTPUT_1, meta)
    assert passed is True, f"Expected Lens to pass ALR check but failed: {failures}"
    assert details["present"] is True
    assert details["item_count"] >= 3
    assert details["speaker_attribution_grounded"] is True
    assert len(failures) == 0


def test_missing_alr_section_fails():
    """Verify Output 1 lacking an Ambiguity Lock Register section fails Gate 1.7."""
    text_without_alr = """# Meeting -- Forensic Transcript\n**Date:** 08/28/2026\n\n**Stefan:** [00:01] Hello.\n**Dan:** [00:05] Hi."""
    meta = {"roster": ["Stefan", "Dan"]}
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(text_without_alr, meta)
    assert passed is False
    assert details["present"] is False
    assert any("AMBIGUITY_LOCK_REGISTER_MISSING" in f for f in failures)


def test_degenerate_placeholder_alr_fails():
    """Verify degenerate single-line placeholder text fails Gate 1.7."""
    degenerate_text = (
        "# Meeting -- Forensic Transcript\n**Date:** 08/28/2026\n\n"
        "**Stefan:** [00:01] Hello.\n**Dan:** [00:05] Hi.\n\n"
        "---\n## Ambiguity Lock Register\nNo ambiguity met the HITL threshold.\n"
    )
    meta = {"roster": ["Stefan", "Dan"]}
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(degenerate_text, meta)
    assert passed is False
    assert any("AMBIGUITY_LOCK_REGISTER_DEGENERATE" in f for f in failures)


def test_alr_lacking_speaker_attribution_fails_for_multi_attendee():
    """Verify ALR lacking speaker attribution in a multi-attendee meeting fails Gate 1.7."""
    text_without_speaker_alr = (
        "# Meeting -- Forensic Transcript\n**Date:** 08/28/2026\n\n"
        "**Cameron Mercer:** [00:01] Hello.\n**Drew Oakley:** [00:05] Hi.\n\n"
        "---\n## Ambiguity Lock Register\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lexicon Ambiguity | [00:10] | stmt-01 | Kiro is AWS AI IDE | Operator Confirmed |\n"
    )
    meta = {"roster": ["Cameron Mercer", "Drew Oakley"]}
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(text_without_speaker_alr, meta)
    assert passed is False
    assert any("AMBIGUITY_LOCK_REGISTER_SPEAKER_HITL_MISSING" in f for f in failures)


def test_alr_bypass_allows_unattributed_for_single_attendee():
    """Verify bypass_speaker_hitl flag is rejected and fails closed under mandatory Stage U0 HITL."""
    text_without_speaker_alr = (
        "# Meeting -- Forensic Transcript\n**Date:** 08/28/2026\n\n"
        "**Cameron Mercer:** [00:01] Hello.\n**Drew Oakley:** [00:05] Hi.\n\n"
        "---\n## Ambiguity Lock Register\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lexicon Ambiguity | [00:10] | stmt-01 | Kiro is AWS AI IDE | Operator Confirmed |\n"
    )
    meta = {"roster": ["Cameron Mercer", "Drew Oakley"], "bypass_speaker_hitl": True}
    with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
        check_ambiguity_lock_register_and_speaker_hitl(text_without_speaker_alr, meta)


def test_format_ambiguity_lock_register_generates_gold_standard_table():
    """Verify format_ambiguity_lock_register always produces an itemized grounding table."""
    meta = {
        "roster": [{"name": "Cameron Mercer"}, {"name": "Drew Oakley"}],
        "attendees": ["Cameron Mercer", "Drew Oakley"],
    }
    speaker_map = {"Speaker A": "Cameron Mercer", "Speaker B": "Drew Oakley"}
    alr_text = format_ambiguity_lock_register(meta, speaker_map)

    assert "| Item # |" in alr_text
    assert "Speaker Attribution: Speaker A" in alr_text
    assert "Cameron Mercer" in alr_text
    assert "Speaker Attribution: Speaker B" in alr_text
    assert "Drew Oakley" in alr_text

    # Test with empty speaker_map but roster in metadata
    alr_from_roster = format_ambiguity_lock_register(meta, {})
    assert "| Item # |" in alr_from_roster
    assert "Speaker Attribution & Acoustic Grounding" in alr_from_roster
    assert "Cameron Mercer" in alr_from_roster


def test_forensic_judge_fails_output1_without_alr():
    """Verify ForensicTranscriptJudge caps dimension 2 and fails verdict when ALR is missing."""
    judge = ForensicTranscriptJudge()
    parsed_eval = judge.evaluate(
        f1_text="# Meeting -- Forensic Transcript\n**Date:** 08/28/2026\n\n**Stefan:** [00:01] Hello.\n**Dan:** [00:05] Hi.",
        raw_source_text="Stefan: Hello\nDan: Hi",
        mock=True,
    )
    # Even in mock mode, post-processing evaluates f1_text
    assert parsed_eval["verdict"] == "FAIL"
    assert parsed_eval["composite_score"] <= 68
    assert parsed_eval["dimension_2_attribution_and_room_mic_hygiene"] <= 8
    assert "AMBIGUITY_LOCK_REGISTER_DEFECT" in parsed_eval["forensic_rationale"]


def test_forensic_judge_passes_gold_standard_output1():
    """Verify ForensicTranscriptJudge passes gold-standard Output 1."""
    judge = ForensicTranscriptJudge()
    parsed_eval = judge.evaluate(
        f1_text=CHAKRA_GOLD_OUTPUT_1,
        raw_source_text=CHAKRA_GOLD_OUTPUT_1,
        mock=True,
    )
    assert parsed_eval["verdict"] == "PASS"
    assert parsed_eval["composite_score"] >= 85
    assert parsed_eval["dimension_2_attribution_and_room_mic_hygiene"] >= 20


def test_run_qa_gate_1_includes_gate_1_7():
    """Verify run_qa_gate_1 includes Gate_1_7 and enforces it."""
    meta = {
        "roster": ["Cameron Mercer", "Drew Oakley", "Amit Ayer", "Chakra Sankaraiah", "Mark McCarthy"],
        "date": "08/27/2026",
    }
    # Gold standard should pass Gate 1
    res_pass = run_qa_gate_1(CHAKRA_GOLD_OUTPUT_1, CHAKRA_GOLD_OUTPUT_1, meta)
    assert "Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL" in res_pass["gates"]
    assert res_pass["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]["passed"] is True

    # Output without ALR must fail Gate 1
    text_bad = CHAKRA_GOLD_OUTPUT_1.split("## Ambiguity Lock Register")[0]
    res_fail = run_qa_gate_1(text_bad, CHAKRA_GOLD_OUTPUT_1, meta)
    assert res_fail["status"] == "FAIL"
    assert res_fail["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]["passed"] is False
    assert any("AMBIGUITY_LOCK_REGISTER_MISSING" in f for f in res_fail["failures"])
