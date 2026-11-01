"""
Wave 6 Acceptance Tests:
Shift-Left Gate C Adaptive Recovery, Ambiguity Detector & Attribution Calibration
"""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.ambiguity_core.detector_d1_speaker import SpeakerAttributionDetector
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import (
    evaluate_gate_c_attribution_anomalies,
    execute_upstream_attribution_recovery,
)
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.candidate_scorer import calibrate_scorer_weights_from_ledger
from dual_pipeline.speaker_attribution_core.confidence_router import WordConfidenceRouter
from dual_pipeline.speaker_memory import SpeakerMemory
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def _unit_vec(idx: int, dim: int = 256) -> list[float]:
    v = [0.01] * dim
    v[idx % dim] = 1.0
    return v


def test_wave6_gate_c_detects_voiceprint_contradiction_and_recovers_collapse() -> None:
    vec_alice = _unit_vec(0)
    vec_bob = _unit_vec(1)
    # 10 turns all collapsed to Alice, but turns 5..9 have Bob's 256-D voiceprint!
    turns = []
    turn_embeddings = {}
    for i in range(10):
        tid = f"turn-{i:03d}"
        turns.append(
            ForensicTurnSignal(
                turn_id=tid,
                span=SampleSpan(start_sample=i * 16000 * 3, end_sample=(i + 1) * 16000 * 3 - 100),
                transcript_verbatim=f"Discussion point number {i} on architecture.",
                speaker_cluster_ids=("Alice Smith",),
                identity_state="enrollment_matched",
                roster_person_id="Alice Smith",
                identity_evidence_id="ev-1",
            )
        )
        turn_embeddings[tid] = vec_alice if i < 5 else vec_bob

    env = ForensicSignalEnvelope(
        recording_id="rec-w6",
        signal_digest="a" * 64,
        canonical_sample_rate_hz=16000,
        timeline_length_samples=10 * 16000 * 3,
        source_hashes=("b" * 64,),
        turns=tuple(turns),
    )
    meta = {
        "roster": ["Alice Smith (PartnerAdvisory)", "Bob Jones (Apex Global)"],
        "enrolled_centroids": {
            "Alice Smith": vec_alice,
            "Bob Jones": vec_bob,
        },
        "turn_embeddings": turn_embeddings,
    }

    findings = evaluate_gate_c_attribution_anomalies(env, metadata=meta)
    codes = {f.code for f in findings}
    assert "SINGLE_SPEAKER_DOMINANCE_COLLAPSE" in codes
    assert "ACOUSTIC_VOICEPRINT_CONTRADICTION" in codes

    # Now run adaptive Gate C recovery via UpstreamAudioQAGatekeeper
    gatekeeper = UpstreamAudioQAGatekeeper()
    rep, recovered_env = gatekeeper.evaluate_with_adaptive_recovery(env, metadata=meta)
    assert rep.verdict == "PASS"
    recovered_speakers = [t.roster_person_id for t in recovered_env.turns]
    assert recovered_speakers.count("Alice Smith") == 5
    assert recovered_speakers.count("Bob Jones") == 5


def test_wave6_d1_detector_uses_candidate_scorer_posteriors(tmp_path: Path) -> None:
    mem = SpeakerMemory(memory_path=tmp_path / "speaker_memory.json")
    mem.record_attribution(
        name="Alice Smith",
        utterance="We completed the agrivoltaics GIS pipeline deployment.",
        topics=["agrivoltaics", "GIS"],
    )
    detector = SpeakerAttributionDetector()
    turns = [
        {
            "speaker": "Conference Room Mic",
            "text": "Let us review the agrivoltaics GIS pipeline deployment.",
            "start_ms": 0,
            "end_ms": 4000,
        }
    ]
    records = detector.detect(
        turns,
        known_attendees=["Alice Smith", "Bob Jones", "Carol White"],
        metadata={"speaker_memory": mem},
    )
    assert len(records) == 1
    cands = records[0].candidates
    assert cands[0].text == "Alice Smith"
    # Verify non-flat posterior (Alice Smith has higher confidence than Bob/Carol)
    assert cands[0].confidence > cands[1].confidence


def test_wave6_auto_speaker_memory_learning_and_router_calibration(tmp_path: Path) -> None:
    from dual_pipeline.hitl_governance import HITLDecisionStore
    mem_path = tmp_path / "speaker_memory.json"
    mem = SpeakerMemory(memory_path=mem_path)
    tx = (
        "Speaker 1: We delivered the Unifier integration and completed the rollout.\n"
        "Speaker 2: We verified the GIS schema and validated all endpoints.\n"
    )
    meta = {
        "roster": ["Alice Smith (PartnerAdvisory)", "Bob Jones (Apex Global)"],
        "u0_attendees_confirmed": True,
        "hitl_verified": True,
        "speaker_memory": mem,
        "voiceprint_matches": {
            "Speaker 1": ("Alice Smith", 0.93),
            "Speaker 2": ("Bob Jones", 0.91),
        },
    }
    store = HITLDecisionStore(ledger_path=tmp_path / "ledger.json")
    engine = AudioSpeakerAttributionEngine(decision_store=store)
    res = engine.analyze_and_resolve(tx, meta)
    assert res.speaker_map.get("Speaker 1") == "Alice Smith"
    assert res.speaker_map.get("Speaker 2") == "Bob Jones"

    # Verify SpeakerMemory auto-learned from the hitl_verified transcript
    reloaded = SpeakerMemory(memory_path=mem_path)
    alice_prof = reloaded.get_profile("Alice Smith")
    assert alice_prof is not None
    assert alice_prof.confirmed_turn_count >= 1

    # Verify blocked when diarization_collapsed=True
    mem_collapsed = SpeakerMemory(memory_path=tmp_path / "collapsed_mem.json")
    learned_collapsed = engine._maybe_auto_learn_speaker_memory(
        res.resolved_transcript,
        res.speaker_map,
        {"hitl_verified": True, "diarization_collapsed": True, "speaker_memory": mem_collapsed},
    )
    assert learned_collapsed == 0

    # Verify scorer weight calibration & WordConfidenceRouter calibration
    weights = calibrate_scorer_weights_from_ledger(
        [{"outcome_type": "AFFIRMED", "rationale": "Acoustic voiceprint match"}]
    )
    assert weights["voiceprint"] > 0.50

    router = WordConfidenceRouter()
    cal = router.calibrate_from_hitl_outcomes(
        [{"outcome_type": "OVERRIDDEN"}, {"outcome_type": "OVERRIDDEN"}, {"outcome_type": "AFFIRMED"}]
    )
    assert cal["straight_through_floor"] > 0.90
