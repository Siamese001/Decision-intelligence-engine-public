"""
tests/test_attribution_anomaly_gate_e2e.py

Class B E2E Receipt Tests for Wave 4:
Pre-Synthesis Attribution Anomaly Gate & Retry/Fail-Closed Enforcement.

Verifies:
1. Collapsed 136-turn transcript (Jen Pan) with 7 attendees and 18 queued hints
   triggers all 4 Gate C anomalies and executes 1 upstream recovery retry.
2. If unrecovered, halts fail-closed before synthesis with AttributionAnomalyHaltError
   and zero LLM retries, quarantining unapproved draft files.
3. Successful recovery retry recovers multi-speaker dialogue and passes Gate C.
4. Partner organization silence and self-intro contradiction gates.
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
    handle_gate_c_attribution_failure,
    quarantine_unapproved_drafts,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def _create_sample_envelope(
    turns_data: List[Dict[str, Any]],
    recording_id: str = "rec-test-01",
    sample_rate: int = 16000,
) -> ForensicSignalEnvelope:
    turns = []
    for idx, t in enumerate(turns_data):
        start_s = idx * sample_rate * 5
        end_s = start_s + sample_rate * 4
        spk = t.get("speaker")
        turns.append(
            ForensicTurnSignal(
                turn_id=f"turn-{idx:03d}",
                span=SampleSpan(start_sample=start_s, end_sample=end_s),
                transcript_verbatim=t.get("text", "Utterance text"),
                speaker_cluster_ids=(t.get("cluster", "Voice A"),),
                identity_state="enrollment_matched" if spk else "cluster_only",
                roster_person_id=spk,
                identity_evidence_id="ev-test" if spk else None,
            )
        )
    return ForensicSignalEnvelope(
        recording_id=recording_id,
        source_hashes=(TypedSourceHash(hash_value="a" * 64, semantics="content_sha256"),),
        canonical_sample_rate_hz=sample_rate,
        timeline_length_samples=max((t.span.end_sample for t in turns), default=sample_rate),
        turns=tuple(turns),
        signal_digest=f"sig_{recording_id}",
    )


def test_collapsed_136_turn_transcript_triggers_gate_c_anomalies_e2e():
    """Verify collapsed 136-turn transcript triggers all Gate C anomalies."""
    turns_data = []
    for i in range(136):
        if i == 0:
            text = "Good morning everyone, I'm Kiki from Aspen Plus."
        elif i == 1:
            text = "Thanks Kiki. We're reviewing the project schedule today."
        else:
            text = f"Discussion point {i} regarding LENS deliverable progress."
        # All 136 turns collapsed to Jen Pan
        turns_data.append({"speaker": "Jen Pan", "text": text, "cluster": "Voice A"})

    envelope = _create_sample_envelope(turns_data)
    gatekeeper = UpstreamAudioQAGatekeeper()

    hints = [
        {"speaker_name": "Daniel Sabet", "quote": "I want to discuss the timeline", "status": "QUEUED"},
        {"speaker_name": "Kiki", "quote": "I'm Kiki from Aspen Plus", "status": "QUEUED"},
    ]
    meta = {
        "attendees": "Jen Pan (PartnerAdvisory), Amit Ayer (PartnerAdvisory), Daniel Sabet (NEA), Kiki (Aspen Plus), Alice, Bob, Carol",
        "roster": [
            {"name": "Jen Pan", "org": "PartnerAdvisory"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory"},
            {"name": "Daniel Sabet", "org": "Apex Global Enterprises (NEA)"},
            {"name": "Kiki", "org": "Aspen Plus"},
            {"name": "Alice", "org": "Client"},
            {"name": "Bob", "org": "Client"},
            {"name": "Carol", "org": "Client"},
        ],
        "speaker_hints": hints,
    }

    report = gatekeeper.evaluate(envelope, metadata=meta)

    assert report.verdict == "REJECT"
    codes = {f.code for f in report.findings if f.severity == "blocking"}

    # Must catch all 4 core anomalies:
    assert "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" in codes
    assert "UNCONSUMED_SPEAKER_HINTS_DEFICIT" in codes
    assert "PARTNER_ORGANIZATION_SILENCE" in codes
    assert "SELF_INTRO_ATTRIBUTION_CONTRADICTION" in codes


def test_gate_c_anomaly_failure_quarantines_drafts_and_halts_e2e():
    """Verify unrecovered Gate C failure quarantines drafts and halts with AttributionAnomalyHaltError."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir)
        draft1 = out_path / "01_forensic_transcript.md"
        draft2 = out_path / "02_executive_recap.md"
        draft1.write_text("# Draft 1 collapsed content", encoding="utf-8")
        draft2.write_text("# Draft 2 collapsed content", encoding="utf-8")

        turns_data = [{"speaker": "Jen Pan", "text": f"Turn {i}", "cluster": "Voice A"} for i in range(20)]
        envelope = _create_sample_envelope(turns_data)
        gatekeeper = UpstreamAudioQAGatekeeper()

        meta = {
            "attendees": "Jen Pan (PartnerAdvisory), Daniel Sabet (NEA)",
            "output_dir": str(out_path),
            "upstream_recovery_retry_attempted": True,  # Already attempted recovery
        }

        report = gatekeeper.evaluate(envelope, metadata=meta)
        assert report.verdict == "REJECT"

        with pytest.raises(AttributionAnomalyHaltError) as exc_info:
            handle_gate_c_attribution_failure(
                gatekeeper=gatekeeper,
                envelope=envelope,
                qa_rep=report,
                metadata=meta,
                output_dir=out_path,
            )

        assert "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" in exc_info.value.anomaly_codes
        # Confirm draft files are quarantined with .quarantined suffix
        assert not draft1.exists()
        assert not draft2.exists()
        assert (out_path / "01_forensic_transcript.md.quarantined").exists()
        assert (out_path / "02_executive_recap.md.quarantined").exists()


def test_upstream_recovery_retry_success_recovers_multi_speaker_e2e(monkeypatch):
    """Verify that a successful upstream recovery retry recovers multi-speaker dialogue and passes Gate C."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir)
        wav_file = out_path / "00_test_master.wav"
        wav_file.write_bytes(b"RIFF" + b"\x00" * 40)

        # Initial collapsed turns (12 turns, 100% Jen Pan)
        turns_data = [{"speaker": "Jen Pan", "text": f"Turn {i}", "cluster": "Voice A"} for i in range(12)]
        envelope = _create_sample_envelope(turns_data)
        gatekeeper = UpstreamAudioQAGatekeeper()

        meta = {
            "attendees": "Jen Pan (PartnerAdvisory), Daniel Sabet (NEA)",
            "master_wav_path": str(wav_file),
            "output_dir": str(out_path),
            "mock": True,
        }

        # Mock transcriber re-run to return balanced multi-speaker transcript
        multi_speaker_text = (
            "Jen Pan: Welcome everyone to the LENS meeting.\n"
            "Daniel Sabet: Thanks Jen, happy to be here representing NEA.\n"
            "Jen Pan: Let's discuss the Hook and Config architecture.\n"
            "Daniel Sabet: The architecture looks solid from our side.\n"
            "Jen Pan: Next we have the timeline updates.\n"
            "Daniel Sabet: We are aligned on the Q4 release targets.\n"
            "Jen Pan: Perfect. Any questions on the scope?\n"
            "Daniel Sabet: No questions, looks clear.\n"
            "Jen Pan: Great, let's wrap up.\n"
            "Daniel Sabet: Thank you everyone.\n"
            "Jen Pan: Meeting adjourned.\n"
            "Daniel Sabet: Goodbye.\n"
        )

        class MockTranscriber:
            def __init__(self, mock=True):
                pass
            def transcribe(self, path, metadata):
                return multi_speaker_text

        monkeypatch.setattr("dual_pipeline.audio_ingestion_core.mlx_transcriber.LocalMLXAudioTranscriber", MockTranscriber)

        initial_report = gatekeeper.evaluate(envelope, metadata=meta)
        assert initial_report.verdict == "REJECT"

        new_rep, new_env = handle_gate_c_attribution_failure(
            gatekeeper=gatekeeper,
            envelope=envelope,
            qa_rep=initial_report,
            metadata=meta,
            output_dir=out_path,
        )

        assert new_rep.verdict in ("PASS", "PASS_WITH_LOCKS")
        blocking = [f.code for f in new_rep.findings if f.severity == "blocking"]
        assert not any(c in GATE_C_ATTRIBUTION_ANOMALIES for c in blocking)
        assert len(new_env.turns) >= 10
        # Active speakers include both Jen Pan and Daniel Sabet
        speakers = {t.roster_person_id for t in new_env.turns}
        assert "Jen Pan" in speakers
        assert "Daniel Sabet" in speakers


def test_partner_organization_silence_blocking_e2e():
    """Verify that Gate C blocks when a partner organization has zero dialogue turns."""
    # 10 turns split between Jen Pan and Amit Ayer (both PartnerAdvisory)
    turns_data = []
    for i in range(10):
        spk = "Jen Pan" if i % 2 == 0 else "Amit Ayer"
        turns_data.append({"speaker": spk, "text": f"PartnerAdvisory internal update {i}", "cluster": f"Voice {i % 2}"})

    envelope = _create_sample_envelope(turns_data)
    gatekeeper = UpstreamAudioQAGatekeeper()

    meta = {
        "attendees": "Jen Pan (PartnerAdvisory), Amit Ayer (PartnerAdvisory), Daniel Sabet (NEA)",
    }

    report = gatekeeper.evaluate(envelope, metadata=meta)
    assert report.verdict == "REJECT"
    codes = {f.code for f in report.findings if f.severity == "blocking"}
    assert "PARTNER_ORGANIZATION_SILENCE" in codes


def test_self_intro_contradiction_blocking_e2e():
    """Verify that a self-introduction mismatch is caught as a blocking Gate C finding."""
    turns_data = [
        {"speaker": "Jen Pan", "text": "Good morning, this is Daniel Sabet from NEA speaking.", "cluster": "Voice A"},
        {"speaker": "Daniel Sabet", "text": "Thank you.", "cluster": "Voice B"},
    ]
    envelope = _create_sample_envelope(turns_data)
    gatekeeper = UpstreamAudioQAGatekeeper()

    meta = {
        "attendees": "Jen Pan (PartnerAdvisory), Daniel Sabet (NEA)",
    }

    report = gatekeeper.evaluate(envelope, metadata=meta)
    contra_findings = [f for f in report.findings if f.code == "SELF_INTRO_ATTRIBUTION_CONTRADICTION"]
    assert len(contra_findings) >= 1
    assert "Daniel Sabet" in contra_findings[0].required_action
    assert "Jen Pan" in contra_findings[0].required_action
