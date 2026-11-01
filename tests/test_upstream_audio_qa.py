from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def test_upstream_qa_gatekeeper_pass():
    turn1 = ForensicTurnSignal(
        turn_id="turn-001",
        span=SampleSpan(start_sample=0, end_sample=16000),
        transcript_verbatim="We are discussing the LAMS and Unifier architecture.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
    )
    turn2 = ForensicTurnSignal(
        turn_id="turn-002",
        span=SampleSpan(start_sample=16500, end_sample=32000),
        transcript_verbatim="Agreed. The Symphony Hook and Config model will govern it.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 2",),
    )
    digest = ForensicSignalEnvelope.compute_signal_digest("rec-01", ("hash1",), (turn1, turn2))
    envelope = ForensicSignalEnvelope(
        recording_id="rec-01",
        source_hashes=("hash1",),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=32000,
        turns=(turn1, turn2),
        signal_digest=digest,
    )

    gatekeeper = UpstreamAudioQAGatekeeper()
    report = gatekeeper.evaluate(envelope)

    assert report.verdict == "PASS"
    assert "forensic_rendering" in report.authorized_tasks
    assert len(report.authorized_turn_ids) == 2


def test_upstream_qa_gatekeeper_blocks_hallucination():
    turn = ForensicTurnSignal(
        turn_id="turn-001",
        span=SampleSpan(start_sample=0, end_sample=16000),
        transcript_verbatim="Thank you for watching, visit www.example.com",
        identity_state="cluster_only",
    )
    digest = ForensicSignalEnvelope.compute_signal_digest("rec-02", ("hash2",), (turn,))
    envelope = ForensicSignalEnvelope(
        recording_id="rec-02",
        source_hashes=("hash2",),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=16000,
        turns=(turn,),
        signal_digest=digest,
    )

    gatekeeper = UpstreamAudioQAGatekeeper()
    report = gatekeeper.evaluate(envelope)

    assert report.verdict == "REJECT"
    assert len(report.authorized_tasks) == 0
    assert any(f.code == "WHISPER_SUBTITLE_HALLUCINATION" for f in report.findings)


def test_upstream_qa_gatekeeper_blocks_retrograde_timestamp_inversion():
    turn1 = ForensicTurnSignal(
        turn_id="turn-001",
        span=SampleSpan(start_sample=16000 * 10, end_sample=16000 * 20),
        transcript_verbatim="First segment at ten seconds.",
        identity_state="cluster_only",
    )
    # Retrograde jump backwards by 5 seconds (more than allowed 2s overlap)
    turn2 = ForensicTurnSignal(
        turn_id="turn-002",
        span=SampleSpan(start_sample=16000 * 15, end_sample=16000 * 25),
        transcript_verbatim="Second segment jumping backwards.",
        identity_state="cluster_only",
    )
    digest = ForensicSignalEnvelope.compute_signal_digest("rec-03", ("hash3",), (turn1, turn2))
    envelope = ForensicSignalEnvelope(
        recording_id="rec-03",
        source_hashes=("hash3",),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=16000 * 25,
        turns=(turn1, turn2),
        signal_digest=digest,
    )

    gatekeeper = UpstreamAudioQAGatekeeper()
    report = gatekeeper.evaluate(envelope)

    assert report.verdict == "REJECT"
    assert any(f.code == "RETROGRADE_TIMESTAMP_INVERSION" for f in report.findings)
