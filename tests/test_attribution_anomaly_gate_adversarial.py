"""
tests/test_attribution_anomaly_gate_adversarial.py

Adversarial Edge-Case Tests for Wave 4:
Pre-Synthesis Attribution Anomaly Gate & Retry/Fail-Closed Enforcement.

Covers:
1. Exact Production Regression: 136-turn Jen Pan collapse with PartnerAdvisory + Daniel Sabet (NEA).
2. Boundary Payloads: 0-turn, 9-turn sub-threshold, 80% vs 90% boundary, solo attendee recordings.
3. Invariant Bypass Attempts: Mock reviewer environment variables and synthetic auto-pass flags.
4. Draft Quarantine & Rollback: Verification of isolation for multi-stage deliverables.
5. Blind LLM Retry Suppression: Verification of zero LLM dispatch on Gate 1.2 attribution collapse.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
    TypedSourceHash,
)
from dual_pipeline.exceptions import AttributionAnomalyHaltError
from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import (
    GATE_C_ATTRIBUTION_ANOMALIES,
    evaluate_gate_c_attribution_anomalies,
    handle_gate_c_attribution_failure,
    quarantine_unapproved_drafts,
)
from dual_pipeline.transcript_qa_core.judge_failure_diagnostics import (
    FailureType,
    JudgeFailureDiagnosticsCollector,
    classify_qa_failure,
    suggest_remediation,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def _make_envelope(turns_data: List[Dict[str, Any]], rec_id: str = "rec-adv") -> ForensicSignalEnvelope:
    turns = []
    sr = 16000
    for idx, t in enumerate(turns_data):
        s = idx * sr * 5
        e = s + sr * 4
        spk = t.get("speaker")
        turns.append(
            ForensicTurnSignal(
                turn_id=f"turn-{idx:03d}",
                span=SampleSpan(start_sample=s, end_sample=e),
                transcript_verbatim=t.get("text", "Utterance"),
                speaker_cluster_ids=(t.get("cluster", "Voice A"),),
                identity_state="enrollment_matched" if spk else "cluster_only",
                roster_person_id=spk,
                identity_evidence_id="ev-adv" if spk else None,
            )
        )
    return ForensicSignalEnvelope(
        recording_id=rec_id,
        source_hashes=(TypedSourceHash(hash_value="b" * 64, semantics="content_sha256"),),
        canonical_sample_rate_hz=sr,
        timeline_length_samples=max((t.span.end_sample for t in turns), default=sr),
        turns=tuple(turns),
        signal_digest=f"sig_{rec_id}",
    )


# --- Category 1: Exact Production Regression ---

def test_exact_production_regression_rca_scenario_fails_closed():
    """Pin the exact RCA failure scenario: 136-turn collapsed Jen Pan transcript with PartnerAdvisory + NEA."""
    turns_data = []
    for i in range(136):
        txt = "I'm Kiki from Aspen Plus." if i == 0 else f"Discussion item {i}"
        turns_data.append({"speaker": "Jen Pan", "text": txt, "cluster": "Voice A"})

    envelope = _make_envelope(turns_data, rec_id="rca-136")
    gatekeeper = UpstreamAudioQAGatekeeper()

    meta = {
        "project": "LENS",
        "meeting": "LENS Business meeting",
        "attendees": "PartnerAdvisoryTeam, Daniel Sabet (NEA)",
        "speaker_hints": [
            {"speaker_name": "Daniel Sabet", "quote": "I want to follow up", "status": "QUEUED"},
            {"speaker_name": "Jen Pan", "quote": "Discussion item 5", "status": "QUEUED"},
        ],
        "upstream_recovery_retry_attempted": True,
    }

    report = gatekeeper.evaluate(envelope, metadata=meta)
    assert report.verdict == "REJECT"

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_p = Path(tmp_dir)
        (out_p / "01_forensic_transcript.md").write_text("Collapsed Jen Pan text", encoding="utf-8")

        with pytest.raises(AttributionAnomalyHaltError) as exc_info:
            handle_gate_c_attribution_failure(
                gatekeeper=gatekeeper,
                envelope=envelope,
                qa_rep=report,
                metadata=meta,
                output_dir=out_p,
            )

        err = exc_info.value
        assert "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" in err.anomaly_codes
        assert "PARTNER_ORGANIZATION_SILENCE" in err.anomaly_codes
        assert "SELF_INTRO_ATTRIBUTION_CONTRADICTION" in err.anomaly_codes
        assert not (out_p / "01_forensic_transcript.md").exists()
        assert (out_p / "01_forensic_transcript.md.quarantined").exists()


# --- Category 2: Boundary Payloads ---

def test_boundary_zero_and_sub_threshold_turns():
    """Verify 0-turn and 9-turn envelopes do not false-positive on dominance collapse."""
    # 0 turns
    env_empty = _make_envelope([])
    findings_empty = evaluate_gate_c_attribution_anomalies(
        env_empty, metadata={"attendees": "Alice, Bob"}
    )
    assert not any(f.code == "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" for f in findings_empty)

    # 9 turns (below 10-turn minimum threshold for dominance collapse)
    turns_9 = [{"speaker": "Alice", "text": f"Turn {i}", "cluster": "Voice A"} for i in range(9)]
    env_9 = _make_envelope(turns_9)
    findings_9 = evaluate_gate_c_attribution_anomalies(
        env_9, metadata={"attendees": "Alice, Bob"}
    )
    assert not any(f.code == "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" for f in findings_9)


def test_boundary_dominance_ratio_threshold():
    """Verify exactly 8/10 (80% < 85%) passes dominance check, but 9/10 (90% >= 85%) fails."""
    # 8/10 Alice (80%)
    turns_8 = [{"speaker": "Alice" if i < 8 else "Bob", "text": f"Turn {i}", "cluster": f"Voice {i % 2}"} for i in range(10)]
    env_8 = _make_envelope(turns_8)
    findings_8 = evaluate_gate_c_attribution_anomalies(
        env_8, metadata={"attendees": "Alice, Bob"}
    )
    assert not any(f.code == "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" for f in findings_8)

    # 9/10 Alice (90% >= 85%)
    turns_9 = [{"speaker": "Alice" if i < 9 else "Bob", "text": f"Turn {i}", "cluster": f"Voice {i % 2}"} for i in range(10)]
    env_9 = _make_envelope(turns_9)
    findings_9 = evaluate_gate_c_attribution_anomalies(
        env_9, metadata={"attendees": "Alice, Bob"}
    )
    assert any(f.code == "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" for f in findings_9)


def test_boundary_single_attendee_solo_recording_not_blocked():
    """Verify solo meetings (1 person on roster) NEVER trigger dominance collapse."""
    turns_solo = [{"speaker": "Alice", "text": f"Dictation turn {i}", "cluster": "Voice A"} for i in range(25)]
    env_solo = _make_envelope(turns_solo)
    findings_solo = evaluate_gate_c_attribution_anomalies(
        env_solo, metadata={"attendees": "Alice"}
    )
    assert not any(f.code == "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" for f in findings_solo)


# --- Category 3: Invariant Bypass Attempts ---

def test_invariant_bypass_synthetic_flags_rejected():
    """Verify synthetic auto-pass flags cannot bypass Gate C anomaly gating."""
    turns_collapsed = [{"speaker": "Alice", "text": f"Turn {i}", "cluster": "Voice A"} for i in range(15)]
    envelope = _make_envelope(turns_collapsed)
    gatekeeper = UpstreamAudioQAGatekeeper()

    # Adversarial attempts to bypass gate via metadata flags
    meta = {
        "attendees": "Alice, Bob",
        "operator_verified": True,
        "auto_pass": True,
        "reviewer": "auto",
        "skip_gate_c": True,
        "bypass_attribution_sanity": True,
    }

    report = gatekeeper.evaluate(envelope, metadata=meta)
    assert report.verdict == "REJECT"
    codes = {f.code for f in report.findings if f.severity == "blocking"}
    assert "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" in codes


# --- Category 4: Draft Quarantine & Rollback ---

def test_draft_quarantine_integrity_across_deliverables():
    """Verify quarantine_unapproved_drafts renames all 01/02/03 deliverables."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        f1 = p / "01_forensic_transcript.md"
        f2 = p / "02_executive_recap.md"
        f3 = p / "03_canonical_signal_projection.json"
        keep = p / "session_manifest.json"

        f1.write_text("t1", encoding="utf-8")
        f2.write_text("t2", encoding="utf-8")
        f3.write_text("{}", encoding="utf-8")
        keep.write_text("{}", encoding="utf-8")

        quarantined = quarantine_unapproved_drafts(p)
        assert len(quarantined) == 3
        assert not f1.exists()
        assert not f2.exists()
        assert not f3.exists()
        assert keep.exists()
        assert (p / "01_forensic_transcript.md.quarantined").exists()
        assert (p / "02_executive_recap.md.quarantined").exists()
        assert (p / "03_canonical_signal_projection.json.quarantined").exists()


# --- Category 5: Blind LLM Retry Suppression ---

def test_blind_llm_retry_suppressed_on_attribution_collapse():
    """Verify judge_failure_diagnostics marks attribution collapse as llm_required=False."""
    ftype = classify_qa_failure("Gate_1_2_Attribution_Hygiene", "SINGLE_SPEAKER_COLLAPSE_DETECTED: Speaker Jen Pan accounts for 100%")
    assert ftype == FailureType.ATTRIBUTION

    remediation, llm_required = suggest_remediation("Gate_1_2_Attribution_Hygiene", "SINGLE_SPEAKER_COLLAPSE_DETECTED", ftype)
    assert llm_required is False
    assert "blind llm text retry is strictly prohibited" in remediation.lower()

    collector = JudgeFailureDiagnosticsCollector()
    collector.extract_from_qa_report({
        "output_01_forensic_transcript": {
            "gates": {
                "Gate_1_2_Attribution_Hygiene": {
                    "passed": False,
                    "failures": ["SINGLE_SPEAKER_COLLAPSE_DETECTED: Speaker 'Jen Pan' accounts for 136/136 turns"],
                }
            }
        }
    })

    assert len(collector.failures) == 1
    rec = collector.failures[0]
    assert rec.failure_type == FailureType.ATTRIBUTION.value
    assert rec.llm_required is False
    assert rec.retry_scope == "upstream_attribution"


def test_production_regression_conversational_phrases_do_not_trigger_self_intro_contradiction():
    """Verify lowercase conversational phrases after 'I'm'/'this is' do not false-positive as self-intros."""
    from dual_pipeline.canonical_pipeline.content_loader import _build_turn_signals

    utterances = [
        {"speaker": "SPEAKER_01", "start_time_sec": 0.08, "end_time_sec": 26.18, "text": "And I'm like, we are proposing two product strategists."},
        {"speaker": "SPEAKER_00", "start_time_sec": 27.71, "end_time_sec": 55.40, "text": "I'm getting confused with this. This is helpful and I'm not technical."},
        {"speaker": "SPEAKER_01", "start_time_sec": 56.00, "end_time_sec": 85.00, "text": "I'm making this work and this is documentation for the team."},
    ]
    meta = {
        "speaker_map": {"SPEAKER_01": "Taylor Mercer", "SPEAKER_00": "Amit Ayer"},
    }
    turns = _build_turn_signals(utterances, "", meta, sample_rate=16000)
    assert turns[0].span.start_sample == int(0.08 * 16000)
    assert turns[2].span.end_sample == int(85.00 * 16000)

    env = ForensicSignalEnvelope(
        recording_id="traci-amit-reg",
        source_hashes=(TypedSourceHash(hash_value="c" * 64, semantics="content_sha256"),),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=int(90.0 * 16000),
        turns=tuple(turns),
        signal_digest="sig_traci_amit",
    )
    findings = evaluate_gate_c_attribution_anomalies(env, metadata={"attendees": "Taylor Mercer, Amit Ayer"})
    assert not any(f.code == "SELF_INTRO_ATTRIBUTION_CONTRADICTION" for f in findings)

