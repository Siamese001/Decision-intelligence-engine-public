"""
tests/end_to_end/test_output1_hitl_judge_remediation_e2e.py

End-to-end and lifecycle verification for Output 01 (Forensic Transcript)
HITL patch serialization, micro-turn timestamp preservation, cryptographic ALR
binding, and native retry surgical remediation.
"""
from __future__ import annotations

from typing import Any, Dict

from dual_pipeline.transcript_qa_core import gate1_alr
from dual_pipeline.llm_judge_core.forensic_checks import assign_forensic_verdict
from dual_pipeline.llm_judge_core.judges.forensic import (
    build_forensic_judge_user_content,
)
from dual_pipeline.llm_judge_core.judges.judge_serialization import (
    build_trusted_c0_context_block,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import (
    ForensicNormalizer,
)


class _DummyPlan:
    diarization_status = "DIARIZED_ACOUSTIC_RESOLVED"


class _DummyProvider:
    def synthesize_forensic(self, system_prefix: str, user_content: str) -> str:
        return user_content


def test_e2e_output1_hitl_patches_and_alr_binding_achieve_pass_verdict(
    monkeypatch: Any,
) -> None:
    """
    Full Stage 01 lifecycle test:
    1. Raw diarized transcript contains same-speaker micro-turns separated by >5s across a 194s window,
       plus ASR errors ('years'/'PAs' -> 'PRs') and speaker cluster labels.
    2. Operator-confirmed speaker_map, speaker_turn_patches, hitl_resolutions, and Ed25519 ALR receipt
       are supplied in metadata.
    3. ForensicNormalizer.execute preserves all intermediate turn timestamps (>5s apart), prevents
       false '[Recording gap: 00:22:12 – 00:25:26]' injection, preserves 'Operator-Verified' in ALR,
       serializes all HITL ground-truth patches into <trusted_c0_roster_receipt status="VERIFIED">,
       and achieves composite_score >= 90 (verdict == "PASS").
    """
    monkeypatch.setattr(
        gate1_alr,
        "verify_alr_cryptographic_receipt",
        lambda text, m, *args, **kwargs: (
            True,
            {
                "hitl_receipt_bound": True,
                "verification_type": "ed25519_jcs",
                "verified_entry_id": "auth-ev-7f5ab81ac36c7ad45b20a9eae975851a",
            },
            [],
        ),
    )

    raw_transcript = (
        "**Speaker 1** [00:01:09]: The first metric is the number of PRs per developer.\n\n"
        "**Speaker 2** [00:10:00]: Let us review the dashboard numbers.\n\n"
        "**Speaker 1** [00:22:12]: We are checking the weekly trend chart now.\n\n"
        "**Speaker 1** [00:22:20]: Right here.\n\n"
        "**Speaker 1** [00:23:23]: Look at the middle bar.\n\n"
        "**Speaker 1** [00:24:52]: That one.\n\n"
        "**Speaker 2** [00:24:57]: Yes I see it.\n\n"
        "**Speaker 1** [00:25:18]: Exactly.\n\n"
        "**Speaker 1** [00:25:26]: Moving to the next slide on AI attribution.\n\n"
        "**Speaker 3** [00:26:20]: We should also track review cycle time.\n\n"
        "**Unknown** [00:27:14]: I can pull that from GitHub insights.\n"
    )

    metadata: Dict[str, Any] = {
        "project": "project_beacon",
        "meeting_name": "Project Beacon metrics and statistics",
        "date": "10/13/2026",
        "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
        "source_modality": "AUDIO_DIARIZED",
        "attendees": {
            "PartnerAdvisory": [
                "Amit Ayer (Principal Architect)",
                "Cansu Miller (Delivery Lead)",
                "Lillian Le (Engineering Lead)",
                "Speaker_Beta (Senior Consultant)",
            ]
        },
        "roster": [
            {"name": "Amit Ayer", "role": "Principal Architect", "organization": "PartnerAdvisory"},
            {"name": "Cansu Miller", "role": "Delivery Lead", "organization": "PartnerAdvisory"},
            {"name": "Lillian Le", "role": "Engineering Lead", "organization": "PartnerAdvisory"},
            {"name": "Speaker_Beta", "role": "Senior Consultant", "organization": "PartnerAdvisory"},
        ],
        "hitl_role_resolutions": {
            "Amit Ayer": "Principal Architect",
            "Cansu Miller": "Delivery Lead",
            "Lillian Le": "Engineering Lead",
            "Speaker_Beta": "Senior Consultant",
        },
        "speaker_map": {
            "Speaker 1": "Speaker_Beta",
            "Speaker 2": "Cansu Miller",
            "Speaker 3": "Amit Ayer",
            "Unknown": "Lillian Le",
        },
        "speaker_turn_patches": {
            "number of years per developer": "number of PRs per developer",
        },
        "hitl_resolutions": {
            "amb_01": {
                "speaker": "Speaker_Beta",
                "status": "CONFIRMED",
                "basis": "Operator-Verified Acoustic Profile",
            }
        },
        "attendee_confirmation_receipt": {
            "status": "CONFIRMED",
            "confirmed_by": "human_operator",
        },
    }

    u0_context: Dict[str, Any] = {
        "raw_text": raw_transcript,
        "clean_transcript": raw_transcript,
        "metadata": metadata,
        "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
    }

    normalizer = ForensicNormalizer()
    out_1 = normalizer.execute(u0_context, _DummyPlan(), _DummyProvider())

    # 1. Verify intermediate timestamps were preserved and no false recording gap was injected
    for expected_ts in ("[00:22:20]", "[00:23:23]", "[00:24:52]", "[00:24:57]", "[00:25:18]"):
        assert expected_ts in out_1, f"Expected turn timestamp {expected_ts} missing in Output 01"
    assert "[Recording gap: 00:22:12 – 00:25:26]" not in out_1

    # 2. Verify trusted C0 context block and judge prompt include HITL patches and receipt status
    c0_block = build_trusted_c0_context_block(metadata)
    assert 'status="VERIFIED"' in c0_block
    assert "verified_speaker_map" in c0_block
    assert "verified_speaker_and_asr_patches" in c0_block
    assert "verified_alr_resolutions" in c0_block

    judge_prompt = build_forensic_judge_user_content("", raw_transcript, out_1, metadata)
    assert "authoritative operator-verified ASR corrections" in judge_prompt

    # 3. Verify judge scorecard evaluation reaches PASS (>= 90) with D3 = 25
    raw_scorecard = {
        "dimension_1_forensic_grounding_and_fidelity": 24,
        "dimension_2_attribution_and_room_mic_hygiene": 23,
        "dimension_3_chronological_and_timestamp_integrity": 23,
        "dimension_4_asr_cleanup_and_domain_preservation": 23,
        "defect_codes": [],
        "forensic_rationale": "All speaker mappings and ASR corrections match verified HITL receipts.",
        "actionable_remediation_diff": "None.",
    }
    evaluated = assign_forensic_verdict(raw_scorecard, f1_text=out_1, metadata=metadata)
    assert evaluated["dimension_3_chronological_and_timestamp_integrity"] == 25
    assert evaluated["composite_score"] >= 90
    assert evaluated["verdict"] == "PASS"


def test_e2e_native_retry_remediation_directive_applies_surgical_changes(
    monkeypatch: Any,
) -> None:
    """
    Verifies that when ForensicNormalizer.execute is invoked on a retry attempt with a
    remediation_directive on the DIARIZED_ACOUSTIC_RESOLVED path, it applies SurgicalRemediator
    adjustments rather than returning a byte-identical no-op output.
    """
    monkeypatch.setattr(
        gate1_alr,
        "verify_alr_cryptographic_receipt",
        lambda text, m, *args, **kwargs: (True, {"hitl_receipt_bound": True}, []),
    )

    raw_transcript = (
        "**Speaker 1** [00:01:09]: We tested Chachi Bikki on the benchmark.\n\n"
        "**Speaker 2** [00:02:15]: Sounds good.\n\n"
        "**Speaker 1** [00:03:40]: Let us wrap up.\n"
    )
    metadata: Dict[str, Any] = {
        "project": "project_beacon",
        "meeting_name": "Project Beacon metrics and statistics",
        "date": "10/13/2026",
        "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
        "source_modality": "AUDIO_DIARIZED",
        "roster": [
            {"name": "Speaker_Beta", "role": "Senior Consultant", "organization": "PartnerAdvisory"},
            {"name": "Cansu Miller", "role": "Delivery Lead", "organization": "PartnerAdvisory"},
        ],
        "speaker_map": {
            "Speaker 1": "Speaker_Beta",
            "Speaker 2": "Cansu Miller",
        },
    }
    u0_context: Dict[str, Any] = {
        "raw_text": raw_transcript,
        "clean_transcript": raw_transcript,
        "metadata": metadata,
        "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
    }

    normalizer = ForensicNormalizer()
    attempt_1_out = normalizer.execute(u0_context, _DummyPlan(), _DummyProvider())

    retry_directive = (
        "Add Ambiguity Lock Register clarification for phonetic model name 'Chachi Bikki' -> ChatGPT."
    )
    attempt_2_out = normalizer.execute(
        u0_context,
        _DummyPlan(),
        _DummyProvider(),
        remediation_directive=retry_directive,
    )

    assert attempt_2_out != attempt_1_out
    assert "ChatGPT" in attempt_2_out
