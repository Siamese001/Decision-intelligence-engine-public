import pytest

from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
    WordSignal,
)


def test_sample_span_validation():
    span = SampleSpan(start_sample=0, end_sample=16000)
    assert span.duration_samples == 16000
    assert span.duration_seconds(16000) == 1.0

    with pytest.raises(ValueError):
        SampleSpan(start_sample=16000, end_sample=1000)  # end <= start


def test_word_signal_contract():
    word = WordSignal(
        word_id="w-01",
        verbatim_text="architecture",
        span=SampleSpan(start_sample=0, end_sample=8000),
        timing_method="forced_alignment",
        asr_model_id="whisperx-large-v3",
        token_ids=(1234, 5678),
        token_logprobs=(-0.12, -0.05),
    )
    assert word.verbatim_text == "architecture"

    # Token IDs and logprobs length mismatch fails closed
    with pytest.raises(ValueError):
        WordSignal(
            word_id="w-02",
            verbatim_text="fail",
            span=SampleSpan(start_sample=0, end_sample=1000),
            timing_method="asr",
            token_ids=(100,),
            token_logprobs=(-0.1, -0.2),
        )


def test_forensic_turn_signal_identity_invariant():
    # Attempting to assert a roster identity with identity_state="unknown" must fail closed
    with pytest.raises(ValueError) as exc:
        ForensicTurnSignal(
            turn_id="turn-01",
            span=SampleSpan(start_sample=0, end_sample=32000),
            transcript_verbatim="We need to finalize the framework.",
            identity_state="unknown",
            roster_person_id="person:tanner_stevens",  # FORBIDDEN when unknown
        )
    assert "Unknown/cluster-only turns cannot assert roster identity" in str(exc.value)

    # Valid turn with cluster-only
    valid_turn = ForensicTurnSignal(
        turn_id="turn-01",
        span=SampleSpan(start_sample=0, end_sample=32000),
        transcript_verbatim="We need to finalize the framework.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
    )
    assert valid_turn.identity_state == "cluster_only"


def test_signal_envelope_digest_and_rendering():
    turn = ForensicTurnSignal(
        turn_id="turn-01",
        span=SampleSpan(start_sample=0, end_sample=32000),
        transcript_verbatim="Hello team, welcome to the sync.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
    )
    digest = ForensicSignalEnvelope.compute_signal_digest(
        recording_id="rec-01",
        source_hashes=("sha256:abc123fake",),
        turns=(turn,),
    )

    envelope = ForensicSignalEnvelope(
        recording_id="rec-01",
        source_hashes=("sha256:abc123fake",),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=32000,
        turns=(turn,),
        signal_digest=digest,
    )

    rendered = envelope.render_verbatim_transcript()
    assert "**Speaker 1:** [00:00] Hello team, welcome to the sync." in rendered


def test_local_stt_compile_signal_envelope():
    from unittest.mock import MagicMock

    from pipeline_a_intake.local_stt import LocalSTTEngine
    from pipeline_a_intake.text_stitcher import StitchedTranscriptDelta

    mock_db = MagicMock()
    mock_db.get_evidence_by_meeting.return_value = []

    engine = LocalSTTEngine(
        db_manager=mock_db,
        mock_mode=True,
        session_id="meet_eng_qa",
    )
    delta = StitchedTranscriptDelta(
        session_id="meet_eng_qa",
        confirmed_delta="Architecture aligned for Nexus deployment.",
        full_confirmed_text="Architecture aligned for Nexus deployment.",
        speculative_preview="",
        start_ts=0.0,
        end_ts=2.5,
        speaker="Speaker 1",
    )
    engine._persist_confirmed_segment(delta, "meet_eng_qa")

    envelope = engine.compile_signal_envelope("meet_eng_qa")
    assert envelope.recording_id == "meet_eng_qa"
    assert len(envelope.turns) == 1
    assert envelope.turns[0].transcript_verbatim == "Architecture aligned for Nexus deployment."
    assert envelope.turns[0].identity_state == "cluster_only"
    assert envelope.turns[0].speaker_cluster_ids == ("Speaker 1",)


def test_transcript_pipeline_upstream_qa_rejection():
    from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
    pipeline = RawTranscriptPipeline(provider_type="offline")

    # Construct an envelope with Whisper subtitle hallucination
    turn = ForensicTurnSignal(
        turn_id="turn-001",
        span=SampleSpan(start_sample=0, end_sample=16000),
        transcript_verbatim="Thank you for watching, visit www.example.com for more",
        identity_state="cluster_only",
    )
    digest = ForensicSignalEnvelope.compute_signal_digest("rec-hallucinate", ("h1",), (turn,))
    envelope = ForensicSignalEnvelope(
        recording_id="rec-hallucinate",
        source_hashes=("h1",),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=16000,
        turns=(turn,),
        signal_digest=digest,
    )

    with pytest.raises(ValueError) as exc:
        pipeline.run(
            input_path="",
            metadata={"meeting_name": "Test"},
            signal_envelope=envelope,
        )
    assert "Upstream Audio QA Gatekeeper REJECTED signal envelope" in str(exc.value)

