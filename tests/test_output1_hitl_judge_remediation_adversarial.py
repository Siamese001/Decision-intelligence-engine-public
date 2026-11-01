"""
Adversarial and edge-case tests for Output 01 HITL Judge remediation:
- Boundary payload: empty input, 0-byte transcript, single-turn micro-turn
- Malformed input: invalid timestamps, non-dict speaker_turn_patches, missing keys
- Exact production regression: Project Beacon 10-13-2026 micro-turn collapse across 00:22:12-00:25:26
- Invariant bypass attempt: unverified operator claim stripping vs cryptographically bound ALR receipt
"""
from __future__ import annotations

from typing import Any, Dict

from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.silence_gap_classifier import classify_silence_gaps


def test_boundary_empty_and_single_turn_stitching() -> None:
    """Boundary payload: empty string, whitespace-only, and single turn must not fail or drop content."""
    assert ForensicNormalizer._stitch_acoustic_micro_turns("") == ""
    assert ForensicNormalizer._stitch_acoustic_micro_turns("   \n\n  ") == "   \n\n  "
    single = "### [00:01:00]\n\n**Speaker_Beta:** Hello team."
    assert ForensicNormalizer._stitch_acoustic_micro_turns(single) == single


def test_malformed_timestamps_do_not_collapse_across_gaps() -> None:
    """Malformed input: missing or unparseable timestamps must not collapse independent turns."""
    malformed = (
        "**Cansu Miller:** First turn without timestamp.\n\n"
        "**Cansu Miller:** Short reply.\n\n"
        "### [00:05:00]\n\n**Cansu Miller:** Timed turn."
    )
    res = ForensicNormalizer._stitch_acoustic_micro_turns(malformed)
    assert "First turn without timestamp." in res
    assert "Short reply." in res
    assert "### [00:05:00]" in res


def test_micro_turn_stitching_respects_5s_max_gap_and_prevents_false_recording_gap() -> None:
    """Exact production regression: 10-13-2026 Project Beacon 00:22:12 to 00:25:26 cascade collapse."""
    raw_segment = (
        "### [00:22:12]\n\n"
        "**Cansu Miller:** I can't change it around, but that's what I was trying to help mine, basically.\n\n"
        "### [00:22:15]\n\n"
        "**Cansu Miller:** Yep, alright.\n\n"
        "### [00:23:23]\n\n"
        "**Cansu Miller:** Yeah, it's a big jump, yes.\n\n"
        "### [00:24:52]\n\n"
        "**Cansu Miller:** I mean, one second.\n\n"
        "### [00:24:53]\n\n"
        "**Cansu Miller:** If I can...\n\n"
        "### [00:24:57]\n\n"
        "**Cansu Miller:** Yeah, it's in there.\n\n"
        "### [00:25:18]\n\n"
        "**Cansu Miller:** Yeah, I did take a look at the PR size.\n\n"
        "### [00:25:26]\n\n"
        "**Speaker_Beta:** Because many of our PRs are still large."
    )
    stitched = ForensicNormalizer._stitch_acoustic_micro_turns(raw_segment)
    # 00:22:15 is within 3s of 00:22:12, so it stitches into 00:22:12
    assert "basically. Yep, alright." in stitched
    # 00:23:23 (68s gap), 00:24:52 (89s gap), and 00:25:18 (21s gap) MUST retain their timestamps!
    assert "### [00:23:23]" in stitched
    assert "### [00:24:52]" in stitched
    assert "### [00:25:18]" in stitched
    assert "### [00:25:26]" in stitched

    # 00:24:53 (1s after 00:24:52) and 00:24:57 (4s after 00:24:53) stitch into 00:24:52
    assert "I mean, one second. If I can... Yeah, it's in there." in stitched

    # Running classify_silence_gaps must NOT inject [Recording gap: 00:22:12 – 00:25:26]
    meta: Dict[str, Any] = {"provider": "offline"}
    classified, _ = classify_silence_gaps(stitched, meta)
    assert "[Recording gap: 00:22:12 – 00:25:26]" not in classified


def test_silence_gap_classifier_ignores_alr_timestamps_and_substantive_speech() -> None:
    """Ensure classify_silence_gaps does not inject recording gaps inside ALR tables or after >=15-word turns."""
    transcript_with_long_monologue = (
        "### [00:01:00]\n\n"
        "**Speaker_Beta:** This is a detailed presentation covering developer averages, AI attribution, "
        "bug classification, team totals, and pull request velocity across the entire engineering team.\n\n"
        "### [00:04:10]\n\n"
        "**Cansu Miller:** Thank you for walking through that chart.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| ID | Locator | Resolution |\n"
        "| --- | --- | --- |\n"
        "| ALR-001 | [00:01:00] | Verified |\n"
        "| ALR-002 | [00:35:00] | Verified |\n"
    )
    meta: Dict[str, Any] = {"provider": "offline"}
    classified, _ = classify_silence_gaps(transcript_with_long_monologue, meta)
    assert "[Recording gap:" not in classified


def test_trusted_c0_context_block_serializes_hitl_patches_and_speaker_map() -> None:
    """Verify build_trusted_c0_context_block includes speaker_map, speaker_turn_patches, and hitl_resolutions when verified."""
    import json
    from dual_pipeline.llm_judge_core.judges.judge_serialization import build_trusted_c0_context_block

    meta: Dict[str, Any] = {
        "attendees": {"PartnerAdvisory": ["Amit Ayer (Principal Architect)", "Speaker_Beta (Senior Consultant)"]},
        "roster": [
            {"name": "Amit Ayer", "role": "Principal Architect", "org": "PartnerAdvisory", "verified_turns": ["00:26:20"]},
            {"name": "Speaker_Beta", "role": "Senior Consultant", "org": "PartnerAdvisory"},
        ],
        "hitl_role_resolutions": {"Amit Ayer": "Principal Architect", "Speaker_Beta": "Senior Consultant"},
        "attendee_confirmation_receipt": {
            "signed_entry_id": "auth-ev-0c18fe6aa67d01e850dfdac45d83567f",
            "subject_sha256": "1f9a8d2efc8f2dc0e929f50e4746545fa57925d797ee24589face3c3f44454d8",
        },
        "speaker_map": {"Speaker 1": "Speaker_Beta", "Speaker 2": "Cansu Miller"},
        "speaker_turn_patches": [
            {"target": "number of years per developer", "replacement": "number of PRs per developer"},
            {"target": "Thanks, Emma.", "replacement": "Thanks, Amit."},
        ],
        "hitl_resolutions": {
            "ALR-001": {
                "ambiguity": "ASR Phonetic Repair",
                "locator": "[00:01:09]",
                "resolution": "years -> PRs",
                "basis": "Operator-Verified Acoustic Grounding",
            }
        },
    }
    block = build_trusted_c0_context_block(meta)
    assert '<trusted_c0_roster_receipt status="VERIFIED">' in block
    inner_json = block.split("\n", 1)[1].rsplit("\n", 1)[0]
    parsed = json.loads(inner_json)
    assert parsed["status"] == "VERIFIED"
    assert parsed["hitl_receipt_bound"] is True
    assert parsed["verified_speaker_map"] == {"Speaker 1": "Speaker_Beta", "Speaker 2": "Cansu Miller"}
    assert len(parsed["verified_speaker_and_asr_patches"]) == 2
    assert "ALR-001" in parsed["verified_alr_resolutions"]
    assert len(parsed["detailed_roster"]) == 2


def test_forensic_healer_preserves_operator_verified_when_cryptographically_bound(monkeypatch: Any) -> None:
    """Invariant bypass & verification: unbound claims are stripped, while cryptographically verified claims are preserved."""
    from dual_pipeline.transcript_qa_core import forensic_healer, gate1_alr

    alr_doc = (
        "# Forensic Transcript\n\n"
        "### [00:01:09]\n\n"
        "**Speaker_Beta:** The first metric is the number of PRs per developer.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item | Ambiguity | Locator | Resolution | Basis |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| ALR-001 | Speaker 1 | [00:01:09] | Resolved to Speaker_Beta | Operator-Verified Acoustic Profile |\n"
    )

    # 1. Unbound claim must NOT be silently stripped (no bypass), so Gate 1.7 fails closed
    unbound_meta: Dict[str, Any] = {}
    monkeypatch.setattr(
        gate1_alr,
        "verify_alr_cryptographic_receipt",
        lambda text, m, *args, **kwargs: (False, {"hitl_receipt_bound": False}, ["UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM: No valid receipt"]),
    )
    healed_unbound = forensic_healer.heal_output_1_forensic(alr_doc, metadata=unbound_meta, changes=[], audit_log=[])
    assert "Operator-Verified Acoustic Profile" in healed_unbound
    passed_g17, _, failures_g17 = gate1_alr.check_ambiguity_lock_register_and_speaker_hitl(healed_unbound, unbound_meta)
    assert passed_g17 is False
    assert any("UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM" in f for f in failures_g17)

    # 2. Cryptographically bound claim in ledger must set hitl_receipt_bound=True and preserve Operator-Verified
    bound_meta: Dict[str, Any] = {}
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
    healed_bound = forensic_healer.heal_output_1_forensic(alr_doc, metadata=bound_meta, changes=[], audit_log=[])
    assert "Operator-Verified Acoustic Profile" in healed_bound
    assert bound_meta.get("hitl_receipt_bound") is True

