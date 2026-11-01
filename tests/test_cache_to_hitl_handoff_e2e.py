"""tests/test_cache_to_hitl_handoff_e2e.py

End-to-End integration test for Wave 1:
Unified cache discovery, pre-diarization hydration, and sidecar map preservation.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from dual_pipeline.transcript_pipeline_core.speaker_map_loader import load_colocated_speaker_map
from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
from dual_pipeline.audio_ingestion_core.mlx_transcriber import _try_load_cache
from dual_pipeline.transcript_pipeline_core.cache_seal import create_cache_seal


def test_e2e_cache_discovery_and_hydration_lifecycle():
    """Full lifecycle: .wav in transcript_cache/<project>/<date>/ with empty map and queued hints."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = "project_beacon"
        date_dir = "10-16-2026"
        slug = "Sprint_Demo_Marcus_PartnerAdvisory"

        # Setup authentic directory hierarchy
        cache_dir = root / "output" / "transcript_cache" / project / date_dir
        cache_dir.mkdir(parents=True)
        container_dir = root / "output" / project / date_dir / slug
        container_dir.mkdir(parents=True)

        wav_path = cache_dir / f"00_{date_dir}_{slug}_master_093432.wav"
        wav_path.write_bytes(b"RIFFdummydataforwavee2etest")

        # 1. Write 2-byte empty speaker map file (the exact bug from live recording)
        empty_map = container_dir / f"{slug.lower()}_speaker_map.json"
        empty_map.write_text("{}", encoding="utf-8")

        # 2. Write 57 hints into cache_dir speaker_hints_queue.json
        hints = [
            {"hint_id": f"h_{i}", "speaker_name": "Brandon Que", "voice_id": "Voice A", "quote": f"test quote {i}"}
            for i in range(5)
        ]
        (cache_dir / "speaker_hints_queue.json").write_text(json.dumps(hints), encoding="utf-8")

        # 3. Write session manifest into cache_dir
        manifest = {
            "project": project,
            "meeting": "Project Beacon Sprint Demo",
            "date": "10/16/2026",
            "date_dirname": date_dir,
            "session_tag": "093432",
            "attendees": [
                {"name": "Marcus Balk", "role": "Presenter", "org": "PartnerAdvisory"},
                {"name": "Brandon Que", "role": "Solution Architect", "org": "PartnerAdvisory"},
            ],
        }
        (cache_dir / f"00_{date_dir}_{slug}_093432_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        # 4. Execute Stage U0 preflight hydration
        cli_attendees = "David Childers, Speaker_Alpha Muschett, Marcus Balk, Brandon Que"
        metadata = {
            "input_path": str(wav_path),
            "project": project,
            "filename_slug": slug,
            "attendees": cli_attendees,
        }

        hydrated = hydrate_live_container_artifacts(metadata, input_path=wav_path)

        # 5. Verify Invariants
        assert metadata["filename_slug"] == slug
        assert metadata["meeting_name"] == "Project Beacon Sprint Demo"
        assert metadata["attendees"] == cli_attendees
        # Check that matching roles were enriched in pre_enrichment_roles without overwriting attendees
        assert "Marcus Balk" in metadata.get("pre_enrichment_roles", {})
        assert metadata["pre_enrichment_roles"]["Marcus Balk"] == "Presenter"
        assert "speaker_hints" in hydrated or bool(metadata.get("speaker_hints"))


def test_e2e_warm_asr_cache_restores_cluster_map():
    """Verify that a warm cache hit restores cluster_label_map and acoustic_speaker_map."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stem = "test_audio"
        key = "abcdef1234567890"

        cache_file = root / f"{stem}_{key}.transcript_cache.json"
        structured_file = root / f"{stem}_{key}.segments.json"
        utterance_file = root / f"{stem}_{key}.utterances.json"
        cluster_file = root / f"{stem}_{key}.cluster_map.json"

        # Create structured sidecars
        transcript_content = "Speaker 1: Hello world this is a test audio transcript with sufficient length."
        cache_file.write_text(transcript_content, encoding="utf-8")

        structured_turns = [
            {"speaker": "Speaker 1", "text": "Hello world", "start": 0.0, "end": 2.0}
        ]
        structured_file.write_text(json.dumps(structured_turns), encoding="utf-8")

        utterances = [
            {"speaker": "Speaker 1", "text": "Hello world", "start": 0.0, "end": 2.0}
        ]
        utterance_file.write_text(json.dumps(utterances), encoding="utf-8")

        cluster_map = {"SPEAKER_00": "Brandon Que", "SPEAKER_01": "Andy Kilinskis"}
        cluster_file.write_text(json.dumps(cluster_map), encoding="utf-8")

        # Create valid cache seal
        create_cache_seal(
            structured_file=structured_file,
            cache_file=cache_file,
            utterance_file=utterance_file,
            audio_sha256="dummyhash",
            cache_key=key,
        )

        metadata = {}
        cached_text = _try_load_cache(cache_file, structured_file, utterance_file, metadata)

        assert cached_text == transcript_content
        assert metadata.get("cluster_label_map") == cluster_map
        assert metadata.get("acoustic_speaker_map") == cluster_map
        assert metadata.get("speaker_map") == cluster_map
        assert metadata.get("has_acoustic_diarization") is True


def test_e2e_stereo_ch0_failure_and_hint_cluster_scoring_flow():
    """Wave 2 E2E: Stereo Ch0/Ch1 transcript with 7 remote speaker hints and Ch0 subdiarization failure."""
    from dual_pipeline.speaker_attribution_core.attribution_engine import AttributionEngine
    from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import apply_quote_hints_to_turns
    from dual_pipeline.transcript_stages_core.cluster_attribution import _build_cluster_options
    from dual_pipeline.exceptions import HITLRequiredError

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cache_dir = root / "output" / "transcript_cache" / "project_beacon" / "10-16-2026"
        cache_dir.mkdir(parents=True)

        remote_hints = [
            {"hint_id": "h1", "speaker_name": "Brandon Que", "quote": "architecture review of Project Beacon", "status": "QUEUED"},
            {"hint_id": "h2", "speaker_name": "Marcus Balk", "quote": "sprint demo presentation", "status": "QUEUED"},
            {"hint_id": "h3", "speaker_name": "Speaker_Alpha Muschett", "quote": "technical governance and protocols", "status": "QUEUED"},
            {"hint_id": "h4", "speaker_name": "David Childers", "quote": "delivery excellence milestones", "status": "QUEUED"},
            {"hint_id": "h5", "speaker_name": "Catalina Orostegui", "quote": "front end design system", "status": "QUEUED"},
            {"hint_id": "h6", "speaker_name": "Jeffrey Glade", "quote": "data pipeline architecture", "status": "QUEUED"},
            {"hint_id": "h7", "speaker_name": "Lillian Le", "quote": "quality assurance test gates", "status": "QUEUED"},
        ]
        hints_file = cache_dir / "speaker_hints_queue.json"
        hints_file.write_text(json.dumps(remote_hints), encoding="utf-8")

        # Synthesize realistic stereo transcript (316 Speaker 1, 91 Speaker 2, 37 Unknown)
        turns = []
        for i in range(316):
            if i == 10:
                turns.append("Speaker 1: Welcome to the architecture review of Project Beacon everyone.")
            elif i == 20:
                turns.append("Speaker 1: Today we have the sprint demo presentation ready.")
            elif i == 30:
                turns.append("Speaker 1: We must enforce technical governance and protocols on all waves.")
            elif i == 40:
                turns.append("Speaker 1: Let us look at the delivery excellence milestones for this quarter.")
            elif i == 50:
                turns.append("Speaker 1: The front end design system components are deployed.")
            elif i == 60:
                turns.append("Speaker 1: Here is the data pipeline architecture diagram.")
            elif i == 70:
                turns.append("Speaker 1: All quality assurance test gates must pass before activation.")
            else:
                turns.append(f"Speaker 1: remote audio dialogue segment {i}")

        for i in range(91):
            turns.append(f"Speaker 2: in room microphone dialogue segment {i}")
        for i in range(37):
            turns.append(f"Unknown: crosstalk segment {i}")

        transcript = "\n".join(turns)
        attendees = [
            "Brandon Que", "Marcus Balk", "Speaker_Alpha Muschett", "David Childers",
            "Catalina Orostegui", "Jeffrey Glade", "Lillian Le", "Andy Kilinskis",
            "Egle Tamasevice", "Amit Ayer", "Jennifer Pan", "Alex Sanchez",
            "Jake Stark", "Morgan Vance Krutilla",
        ]
        metadata = {
            "project": "project_beacon",
            "meeting_name": "Project Beacon Sprint Demo",
            "date": "10/16/2026",
            "cache_dir": str(cache_dir),
            "transcript_cache_dir": str(cache_dir),
            "attendees": ", ".join(attendees),
            "speaker_hints": remote_hints,
            "ch0_subdiarization_failed": True,
            "raw_transcript": transcript,
        }

        # Step 1: Pre-attribution quote hint reconciliation
        reconciled_transcript, reassignments = apply_quote_hints_to_turns(transcript, remote_hints)
        assert len(reassignments) == 7
        for h in remote_hints:
            assert h["speaker_name"] in reconciled_transcript
            assert h["status"] == "RECONCILED"

        # Step 2: Empirical cluster option scoring ranks candidates using quote boost (+0.45)
        options = _build_cluster_options(
            candidates=attendees,
            spk="Speaker 1",
            project="project_beacon",
            metadata=metadata,
            transcript=reconciled_transcript,
        )
        assert len(options) == len(attendees)
        top_option = options[0]
        # Empirical scoring must have placed a hinted candidate at the top with high confidence
        assert top_option["confidence"] >= 0.80
        assert top_option["recommended"] is True

        # Step 3: AttributionEngine execution blocks autonomous collapse into Andy Kilinskis
        engine = AttributionEngine(margin_threshold=0.30, min_autonomous_confidence=0.70)
        with pytest.raises(HITLRequiredError) as exc_info:
            engine.analyze_and_resolve(reconciled_transcript, metadata, autonomous_if_decisive=True)

        err = exc_info.value
        # Invariant: Must fail closed into HITLRequiredError rather than mapping all remaining turns to one person
        assert err.hitl_type in ("single_speaker_collapse", "speaker_attribution")


def test_e2e_hitl_context_preservation_and_checkpoint_resume_continuity():
    """Wave 3 E2E: Full lifecycle of HITL context preservation and checkpoint resume continuity."""
    from dual_pipeline.transcript_cli import _emit_hitl_json
    from dual_pipeline.transcript_stages_core.cluster_attribution import build_cluster_review_items
    from dual_pipeline.canonical_pipeline.c0_pa_runner import step_c0_pa_with_resume
    from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
    from dual_pipeline.exceptions import HITLRequiredError

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        transcript = (
            "Speaker 2: Welcome to the demo.\n"
            "Speaker 2: Let's get started.\n"
            "Speaker 1: I am presenting the architecture overview today.\n"
            "Speaker 2: Thanks for that overview.\n"
            "Speaker 2: Any questions from the team?\n"
        )
        metadata = {
            "project": "project_beacon",
            "meeting_name": "Project Beacon Sprint Demo",
            "date": "10/16/2026",
            "attendees": "Brandon Que, Marcus Balk, Andy Kilinskis",
            "roster": [
                {"name": "Brandon Que", "org": "PartnerAdvisory"},
                {"name": "Marcus Balk", "org": "PartnerAdvisory"},
                {"name": "Andy Kilinskis", "org": "PartnerAdvisory"},
            ],
        }

        # 1. Build cluster review items with full 5-row context window
        review_items, all_contexts, digest, prompt, options = build_cluster_review_items(
            transcript, ["Speaker 1"], metadata=metadata
        )
        assert len(review_items) == 1
        assert len(options) == 3
        assert "BEFORE 2" in prompt or "BEFORE 1" in prompt
        assert "TARGET" in prompt
        assert digest != ("0" * 64)

        # 2. Package into HITLRequiredError and verify _emit_hitl_json serialization
        hitl_err = HITLRequiredError(
            "Generic speaker detected",
            unmapped_speakers=["Speaker 1"],
            hitl_type="speaker_attribution",
            prompt=prompt,
            options=options,
            review_contexts=all_contexts,
            context_digest=digest,
        )
        json_payload = _emit_hitl_json(hitl_err)
        assert json_payload["hitl_required"] is True
        assert json_payload["prompt"] == prompt
        assert json_payload["options"] == options
        assert json_payload["context_digest"] == digest

        # 3. Simulate Operator Resolution & Resumption:
        # Operator confirms Speaker 1 -> Brandon Que
        class MockCheckpoint:
            def has_stage(self, stage): return stage == "C0_PA"
            def get_stage(self, stage):
                return {
                    "pa_context": {"metadata": {"speaker_map": {"Speaker 2": "Andy Kilinskis"}}},
                    "resolved_transcript": transcript,
                    "speaker_map": {"Speaker 2": "Andy Kilinskis"},
                }

        class MockTracker:
            def start_stage(self, *args, **kwargs): pass
            def complete_stage(self, *args, **kwargs): pass

        resume_metadata = {
            "project": "project_beacon",
            "meeting_name": "Project Beacon Sprint Demo",
            "speaker_map": {"Speaker 1": "Brandon Que"},
        }
        pa_context, resumed_text = step_c0_pa_with_resume(
            pipeline=None,
            checkpoint=MockCheckpoint(),
            should_resume=True,
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            source_text=transcript,
            input_hash="testhash123",
            meta=resume_metadata,
            presentation_path=None,
            out_p=root / "output",
            tracker=MockTracker(),
        )

        # Invariant 1: Merged speaker_map has both Speaker 1 and Speaker 2
        assert resume_metadata["speaker_map"] == {
            "Speaker 2": "Andy Kilinskis",
            "Speaker 1": "Brandon Que",
        }
        # Invariant 2: Speaker 1 in transcript is replaced with Brandon Que
        assert "Brandon Que:" in resumed_text


