"""
End-to-end runtime prompt wiring and Pass 2B contract verification tests (Wave 1).
Verifies:
1. Modality-specific Stage 01 prompt resolution (_load_stage_prompt).
2. Preservation of Stage 02 system instructions, <prior_recap_candidate>, and SURGICAL patching directives under cached_content.
3. Pass 2B relative-date resolution rules, H-6 Owner Attribution Waterfall, and retry on Pass2BStructureError.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

from dual_pipeline.transcript_stages_core.constants import _load_stage_prompt
from dual_pipeline.transcript_stages_core.recap_executor import _build_remediation_block
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    ALT_ZERO_COMMITMENTS_SENTINEL,
    ZERO_COMMITMENTS_SENTINEL,
    execute_d02_3pass_dag,
    execute_pass_2b,
    validate_pass_2b_structure,
)


def test_stage_01_modality_specific_prompts_are_distinct_and_reachable() -> None:
    """Verify teams_forensic and audio_forensic load dedicated prompts instead of generic OUTPUT1_CONTRACT."""
    p_forensic = _load_stage_prompt("forensic")
    p_teams = _load_stage_prompt("teams_forensic")
    p_audio = _load_stage_prompt("audio_forensic")

    assert p_teams != p_forensic, "teams_forensic must not be shadowed by generic forensic prompt"
    assert p_audio != p_forensic, "audio_forensic must not be shadowed by generic forensic prompt"
    assert len(p_teams) > 200
    assert len(p_audio) > 200


def test_d02_cached_content_retains_stage02_system_prompt_and_surgical_remediation() -> None:
    """Verify cached_content execution retains Stage 02 system_prompt, <prior_recap_candidate>, and SURGICAL instructions."""
    stage_02_prompt = _load_stage_prompt("recap")
    meta: Dict[str, Any] = {"date": "09/23/2026", "archetype": "OPERATIONAL_STANDUP"}
    transcript = "### [00:01:10]\n\n**Alex Rivera:** I will have the 1,500 documents ready for the Wednesday leadership review."
    prior_cand = (
        "# Executive Recap\n## Meeting Objective\n Objective.\n"
        "# Action & Governance Ledger\n| # | Type | Item | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Action | Prepare 1,500 docs | Alex Rivera | 09/30/2026 | [00:01:10] | Open |\n"
        "# Appendix: Detailed Stakeholder Perspectives\n# Alex Rivera - Apex Global, Lead\n- **Theme**: Detail."
    )
    remediation_xml = _build_remediation_block(
        metadata=meta,
        forensic_transcript=transcript,
        directive="Action & Governance Ledger failed date resolution.",
        prior_candidate=prior_cand,
    )
    user_payload = f"<forensic_transcript>\n{transcript}\n</forensic_transcript>\n\n{remediation_xml}"

    captured_calls: List[Dict[str, Any]] = []

    def _spy_gemini(**kwargs: Any) -> str:
        captured_calls.append(kwargs)
        return (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | Action | Prepare 1,500 documents for review | Alex Rivera | 09/30/2026 (Wednesday Leadership Review) | [00:01:10] | Open |"
        )

    with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", side_effect=_spy_gemini):
        res = execute_pass_2b(
            model="gemini-3.8-flash",
            user_content=user_payload,
            system_prompt=stage_02_prompt,
            cached_content="cachedContents/test-cache-123",
        )

    assert "09/30/2026" in res
    assert len(captured_calls) == 1
    sent_content = captured_calls[0]["user_content"]
    # Stage 02 prompt, H-6 waterfall, prior candidate, and SURGICAL instructions must all be preserved!
    assert "H-6" in sent_content
    assert "<prior_recap_candidate>" in sent_content
    assert "SURGICAL TARGETED PATCHING INSTRUCTIONS:" in sent_content
    assert "relative dates/cadences" in sent_content


def test_pass_2b_retries_when_model_emits_unassigned_or_tbd_and_heals_on_retry() -> None:
    """Verify Pass 2B auto-retries when first attempt emits Unassigned + TBD filler row."""
    attempts: List[Dict[str, Any]] = []

    def _flaky_gemini(**kwargs: Any) -> str:
        attempts.append(kwargs)
        if len(attempts) == 1:
            return (
                "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                "| 1 | Action | Explore OCR options | Unassigned (Pending Owner Assignment) | TBD | [00:04:12] | Open |"
            )
        return (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | Action | Benchmark OCR pipeline throughput | Skyler Chen | 10/20/2026 (Friday EOD) | [00:04:12] | Open |"
        )

    with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", side_effect=_flaky_gemini):
        out = execute_pass_2b(
            model="gemini-3.8-flash",
            user_content="<forensic_transcript>### [00:04:12]\n\n**Skyler Chen:** I will benchmark OCR by Friday.</forensic_transcript>",
            system_prompt="Stage 02 System Prompt",
            cached_content="cachedContents/retry-test",
            max_retries=2,
        )

    assert len(attempts) == 2
    assert "RETRY DIRECTIVE:" in attempts[1]["user_content"]
    assert "Skyler Chen" in out
    assert "10/20/2026" in out


def test_both_zero_commitment_sentinels_pass_validation() -> None:
    """Verify both PASS_2B and recap_executor zero-commitment sentinels validate cleanly."""
    assert validate_pass_2b_structure(ZERO_COMMITMENTS_SENTINEL) is True
    assert validate_pass_2b_structure(ALT_ZERO_COMMITMENTS_SENTINEL) is True
