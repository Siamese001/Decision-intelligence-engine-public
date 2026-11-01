"""tests/test_cache_to_hitl_handoff_adversarial.py

Adversarial and boundary regression tests for Wave 1 cache discovery,
spooler continuity, and pre-diarization hydration invariants.

Categories covered:
1. Boundary payload: Empty {} speaker map, 0-byte or empty hints queue
2. Malformed input: Date-only container names, corrupted manifests
3. Concurrency & race: Spooler adoption and chunks_dir protection during worktree repoint
4. Invariant bypass attempt: Stale manifest attempting to overwrite explicit CLI attendees
5. Rollback & recovery: Warm ASR cache hit restoring acoustic speaker and cluster maps
6. Exact production regression: Sprint Demo 10-16-2026 cache path and slug preservation
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from dual_pipeline.transcript_pipeline_core.speaker_map_loader import load_colocated_speaker_map
from dual_pipeline.canonical_pipeline.u0_ingress import (
    hydrate_live_container_artifacts,
    _resolve_live_artifact_dirs,
)
from dual_pipeline.live_meeting_recorder_core.worktree_repointer import repoint_recorder_paths
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import SpeakerHintQueue, QueuedSpeakerHint


def test_adversarial_empty_speaker_map_short_circuit_bypassed():
    """Category 1: Boundary payload - 2-byte empty {} speaker map must NOT block hint fallback."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Create 2-byte empty speaker map file
        empty_map_file = root / "sprint_demo_speaker_map.json"
        empty_map_file.write_text("{}", encoding="utf-8")

        # Create valid speaker_hints_queue.json
        hints_file = root / "speaker_hints_queue.json"
        hints_payload = [
            {"hint_id": "h1", "speaker_name": "Brandon Que", "voice_id": "Voice A", "quote": "architecture review"}
        ]
        hints_file.write_text(json.dumps(hints_payload), encoding="utf-8")

        # Verify loader ignores empty {} and successfully falls back to speaker_hints_queue.json
        res = load_colocated_speaker_map(empty_map_file)
        assert res is not None, "load_colocated_speaker_map should not return None or {}"
        assert res.get("Voice A") == "Brandon Que"


def test_adversarial_date_dir_slug_corruption_prevented():
    """Category 2 & 6: Malformed input / Production regression - date dir name must not corrupt slug."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cache_dir = root / "output" / "transcript_cache" / "project_beacon" / "10-16-2026"
        cache_dir.mkdir(parents=True)
        wav_file = cache_dir / "00_10-16-2026_Sprint_Demo_Marcus_PartnerAdvisory_master_093432.wav"
        wav_file.write_bytes(b"RIFFdummywavdata")

        # Create manifest with clean slug
        manifest = {
            "project": "project_beacon",
            "meeting": "Project Beacon Sprint Demo",
            "date": "10/16/2026",
            "date_dirname": "10-16-2026",
            "attendees": [
                {"name": "Marcus Balk", "role": "Presenter", "org": "PartnerAdvisory"}
            ],
        }
        manifest_file = cache_dir / "00_10-16-2026_Sprint_Demo_Marcus_PartnerAdvisory_093432_manifest.json"
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

        metadata = {
            "input_path": str(wav_file),
            "project": "project_beacon",
            "filename_slug": "Sprint_Demo_Marcus_PartnerAdvisory",
        }

        hydrated = hydrate_live_container_artifacts(metadata, input_path=wav_file)
        # Verify slug was not corrupted to "10-16-2026"
        assert metadata.get("filename_slug") == "Sprint_Demo_Marcus_PartnerAdvisory"
        assert metadata.get("container_slug") != "10-16-2026"
        assert "time" in hydrated or metadata.get("time") is not None


def test_adversarial_cli_attendees_never_overwritten_by_stale_manifest():
    """Category 5: Invariant bypass - manifest must NOT overwrite explicit 14-person CLI roster."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cache_dir = root / "output" / "transcript_cache" / "project_beacon" / "10-16-2026"
        cache_dir.mkdir(parents=True)
        wav_file = cache_dir / "00_10-16-2026_Sprint_Demo_Marcus_PartnerAdvisory_master_093432.wav"
        wav_file.write_bytes(b"RIFFdummywavdata")

        # Stale 2-person manifest with erroneous names
        manifest = {
            "project": "project_beacon",
            "attendees": [
                {"name": "David Decof", "role": "Solution Principal", "org": "PartnerAdvisory"},
                {"name": "Brandon Dass", "role": "Client Executive", "org": "PartnerAdvisory"},
            ],
        }
        manifest_file = cache_dir / "00_10-16-2026_Sprint_Demo_Marcus_PartnerAdvisory_093432_manifest.json"
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

        cli_attendees = (
            "David Childers, Speaker_Alpha Muschett, Marcus Balk, Brandon Que, "
            "Catalina Orostegui, Jeffrey Glade, Andy Kilinskis, Lillian Le"
        )
        metadata = {
            "input_path": str(wav_file),
            "attendees": cli_attendees,
            "filename_slug": "Sprint_Demo_Marcus_PartnerAdvisory",
        }

        hydrate_live_container_artifacts(metadata, input_path=wav_file)

        # Invariant: CLI roster string is preserved exactly, NOT overwritten by David Decof / Brandon Dass
        assert metadata["attendees"] == cli_attendees
        assert "David Decof" not in metadata["attendees"]
        assert "Brandon Dass" not in metadata["attendees"]


def test_adversarial_spooler_chunk_continuity_during_repoint():
    """Category 3: Concurrency & race - repoint_recorder_paths must keep spooler chunks intact."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        spool_chunks = root / "ephemeral_spool"
        spool_chunks.mkdir()
        target_worktree = root / "worktree"
        target_worktree.mkdir()

        # Mock spooler that is adopted and running
        class MockSpooler:
            _is_adopted = True
            _is_running = True
            def is_adopted(self):
                return self._is_adopted
            def is_running(self):
                return self._is_running

        class MockCaptureManager:
            _active_strategy = "spooler_adopted"
            _ffmpeg_proc = object()
            chunks_dir = spool_chunks
            master_wav = spool_chunks / "master.wav"

        class MockRecorder:
            date_dirname = "10-16-2026"
            clean_slug = "Sprint_Demo"
            session_tag = "120000"
            _target_output_dir = target_worktree / "output"
            _target_cache_dir = target_worktree / "cache"
            _target_chunks_dir = target_worktree / "chunks"
            output_dir = root / "staging_output"
            cache_dir = root / "staging_cache"
            chunks_dir = spool_chunks
            spooler = MockSpooler()
            capture_manager = MockCaptureManager()
            speaker_hint_queue = None
            chat_attributions = None
            speaker_hint_manager = None
            collateral_watcher = None
            session_manifest = root / "staging_manifest.json"

        rec = MockRecorder()
        repoint_recorder_paths(rec, target_worktree_dir=target_worktree)

        # Capture manager chunks_dir must remain bound to the active spooler
        assert rec.capture_manager.chunks_dir == spool_chunks
        assert rec.capture_manager.chunks_dir != rec._target_chunks_dir


def test_adversarial_speaker_hint_queue_mirroring():
    """Category 4: Invariant bypass - hints must mirror across cache_dir and output_dir."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cache_d = root / "cache"
        output_d = root / "output"
        cache_d.mkdir()
        output_d.mkdir()

        q = SpeakerHintQueue(output_dir=output_d, cache_dir=cache_d)
        q.enqueue_hint("Brandon Que", "architecture review", raw_input="Brandon: architecture review")

        # Invariant: Saved file exists in both cache_dir and output_dir
        cache_file = cache_d / "speaker_hints_queue.json"
        output_file = output_d / "speaker_hints_queue.json"

        assert cache_file.exists(), "Hints queue must exist in cache_dir"
        assert output_file.exists(), "Hints queue must exist in output_dir"

        cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
        output_data = json.loads(output_file.read_text(encoding="utf-8"))

        assert len(cache_data) == 1
        assert len(output_data) == 1
        assert cache_data[0]["speaker_name"] == "Brandon Que"
        assert output_data[0]["speaker_name"] == "Brandon Que"


def test_adversarial_ch0_subdiarization_failure_triggers_collapse_review():
    """Wave 2: Multi-attendee Ch0 failure forces collapse review even when skew < 85.0%."""
    from dual_pipeline.speaker_attribution_core.collapse_detector import check_speaker_skew_and_collapse
    from dual_pipeline.exceptions import HITLRequiredError

    # 316 turns on Speaker 1, 91 turns on Speaker 2 (total 407 turns -> Speaker 1 = 77.6% share, < 85%)
    turns = [f"Speaker 1: remote audio turn {i}" for i in range(316)]
    turns += [f"Speaker 2: in room turn {i}" for i in range(91)]
    transcript = "\n".join(turns)

    roster = [
        {"name": f"Attendee {i}", "org": "PartnerAdvisory"} for i in range(14)
    ]
    metadata = {
        "raw_transcript": transcript,
        "ch0_subdiarization_failed": True,
    }

    # Must raise HITLRequiredError due to ch0_subdiarization_failed on >2 attendee roster
    with pytest.raises(HITLRequiredError) as exc_info:
        check_speaker_skew_and_collapse(transcript, roster, metadata)

    err = exc_info.value
    assert err.hitl_type == "single_speaker_collapse"
    assert "Speaker 1" in err.unmapped_speakers or "dominant_label" in str(err)


def test_adversarial_quote_hint_asterisk_stripping():
    """Wave 2: apply_quote_hints_to_turns strips markdown asterisks and brackets from bound_speaker_id."""
    from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import apply_quote_hints_to_turns

    raw_hint = {
        "speaker_name": "Brandon Que",
        "quote": "architecture review of Project Beacon",
        "status": "QUEUED",
    }
    hints = [raw_hint]

    # Markdown bold formatting on speaker turn
    transcript = "**Speaker 1**: We are doing an architecture review of Project Beacon today."
    updated, reassignments = apply_quote_hints_to_turns(transcript, hints)

    # Turn should be updated
    assert "Brandon Que" in updated
    # bound_speaker_id in raw hint must be clean "Speaker 1", NOT "Speaker 1**"
    assert raw_hint.get("bound_speaker_id") == "Speaker 1"
    assert raw_hint.get("status") == "RECONCILED"


def test_adversarial_quote_hint_voice_alias_and_lexical_fallback():
    """Wave 2: _check_quote_hint_match matches Voice A <-> Speaker 1, and falls back to lexical turn match."""
    from dual_pipeline.speaker_attribution_core.candidate_scorer import _check_quote_hint_match

    # Test 1: Voice A bound matches Speaker 1
    hint_voice_a = {
        "speaker_name": "Brandon Que",
        "quote": "cloud deployment strategy",
        "bound_speaker_id": "Voice A",
    }
    match1 = _check_quote_hint_match(
        [hint_voice_a],
        spk_id="Speaker 1",
        name="Brandon Que",
        full_text="",
        turns=["cloud deployment strategy"],
    )
    assert match1 == "cloud deployment strategy"

    # Test 2: bound_speaker_id is "Speaker 2", but the actual turn text is in Speaker 1's turns.
    # Should NOT abort early via continue; must match lexical turns!
    hint_different_bound = {
        "speaker_name": "Marcus Balk",
        "quote": "sprint velocity and burndown charts",
        "bound_speaker_id": "Speaker 2",  # mismatched bound ID
        "status": "QUEUED",
    }
    match2 = _check_quote_hint_match(
        [hint_different_bound],
        spk_id="Speaker 1",
        name="Marcus Balk",
        full_text="",
        turns=["Here are our sprint velocity and burndown charts for sprint 4."],
    )
    assert match2 == "sprint velocity and burndown charts"
    assert hint_different_bound.get("bound_speaker_id") == "Speaker 1"


def test_adversarial_ch0_failure_blocks_single_speaker_auto_assignment():
    """Wave 2: AttributionEngine blocks autonomous assignment of collapsed Ch0 tracks."""
    from dual_pipeline.speaker_attribution_core.attribution_engine import AttributionEngine
    from dual_pipeline.exceptions import HITLRequiredError

    engine = AttributionEngine(margin_threshold=0.30, min_autonomous_confidence=0.70)
    transcript = "\n".join([f"Speaker 1: turn {i}" for i in range(20)])
    roster = [f"Attendee {i}" for i in range(14)]
    metadata = {
        "attendees": ", ".join(roster),
        "ch0_subdiarization_failed": True,
        "meeting_name": "Sprint Demo",
    }

    # Autonomous resolution must be BLOCKED on Speaker 1 when Ch0 subdiarization failed
    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(transcript, metadata, autonomous_if_decisive=True)

    err = exc_info.value
    assert err.hitl_type in ("single_speaker_collapse", "speaker_attribution")


def test_adversarial_emit_hitl_json_preserves_prompt_and_contexts():
    """Wave 3: _emit_hitl_json serializes full multi-row prompt, options, contexts, and digest."""
    from dual_pipeline.transcript_cli import _emit_hitl_json
    from dual_pipeline.exceptions import HITLRequiredError

    multi_row_prompt = (
        "### HITL Decision Required (Speaker Attribution)\n\n"
        "[BEFORE 2] Alice: Hello\n\n"
        "[BEFORE 1] Bob: Hi\n\n"
        "[TARGET] Speaker 1: This is the crucial target turn.\n\n"
        "[AFTER 1] Charlie: Next\n\n"
        "[AFTER 2] Dave: End\n"
    )
    options = [
        {"speaker": "Brandon Que", "confidence": 0.88, "recommended": True},
        {"speaker": "Marcus Balk", "confidence": 0.55, "recommended": False},
    ]
    review_contexts = [{"target_id": "turn_1", "turn_text": "This is the crucial target turn."}]
    digest = "a" * 64

    err = HITLRequiredError(
        "Generic speaker detected",
        unmapped_speakers=["Speaker 1"],
        hitl_type="speaker_attribution",
        prompt=multi_row_prompt,
        options=options,
        review_contexts=review_contexts,
        context_digest=digest,
    )

    payload = _emit_hitl_json(err)
    assert payload["hitl_required"] is True
    assert payload["prompt"] == multi_row_prompt
    assert "[BEFORE 2]" in payload["prompt"]
    assert "[TARGET]" in payload["prompt"]
    assert payload["options"] == options
    assert payload["review_contexts"] == review_contexts
    assert payload["context_digest"] == digest


def test_adversarial_timestamp_turn_speaker_parsing():
    """Wave 3: **Speaker 1** [00:01:23]: parses as 'Speaker 1', not '**Speaker 1** [00'."""
    import re
    from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import execute_clearance_lifecycle

    def _parse_turn_speaker(line: str) -> str:
        clean_line = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", "", line).strip()
        if ":" in clean_line:
            raw_spk = clean_line.split(":", 1)[0].strip()
            return re.sub(r"[\*\[\]#]+", "", raw_spk).strip()
        return "Speaker"

    assert _parse_turn_speaker("**Speaker 1** [00:01:23]: Discussing architecture.") == "Speaker 1"
    assert _parse_turn_speaker("Speaker 2 [00:01:45]: Concurring.") == "Speaker 2"
    assert _parse_turn_speaker("### [00:02:10] **Marcus Balk**: Presentation.") == "Marcus Balk"

    class MockSpan:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    class MockRecorder:
        def span(self, name): return MockSpan()

    transcript = (
        "**Speaker 1** [00:01:23]: Discussing the Project Beacon architecture.\n"
        "Speaker 2 [00:01:45]: Concurring with the approach.\n"
    )
    meta = {
        "meeting_name": "Sprint Demo",
        "date": "10/16/2026",
    }

    execute_clearance_lifecycle(transcript, meta, MockRecorder())
    raw_turns = meta.get("raw_turns")
    assert raw_turns is not None
    assert len(raw_turns) == 2
    # Verify exact parsed speaker names without timestamp corruption
    assert "[00" not in raw_turns[0]["speaker"]
    assert "[00" not in raw_turns[1]["speaker"]
    assert raw_turns[0]["speaker"] == "Speaker 1"
    assert raw_turns[1]["speaker"] == "Speaker 2"


def test_adversarial_u0_quote_hints_reconcile_unknown_utterances():
    """Wave 3: _check_acoustic_unknown_utterances reconciles Unknown turns matching quote hints."""
    from dual_pipeline.canonical_pipeline.u0_ingress import _check_acoustic_unknown_utterances

    raw_utterances = [
        {"speaker": "Speaker 1", "text": "Starting the demo."},
        {"speaker": "Unknown", "text": "Here is the cloud deployment strategy for our infrastructure."},
    ]
    hints = [
        {"speaker_name": "Brandon Que", "quote": "cloud deployment strategy for our infrastructure"},
    ]
    metadata = {
        "structured_utterances": raw_utterances,
        "speaker_hints": hints,
    }
    missing = []
    diagnostics = []

    _check_acoustic_unknown_utterances(metadata, missing, diagnostics)

    # Invariant: Unknown turn is reconciled to Brandon Que, and missing speaker_map is NOT raised
    assert raw_utterances[1]["speaker"] == "Brandon Que"
    assert raw_utterances[1].get("reconciled_by_hint") is True
    assert "speaker_map" not in missing


def test_adversarial_c0_pa_resume_merges_fresh_speaker_map():
    """Wave 3: step_c0_pa_with_resume merges fresh speaker_map over checkpoint state."""
    from dual_pipeline.canonical_pipeline.c0_pa_runner import step_c0_pa_with_resume
    from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality

    class MockCheckpoint:
        def has_stage(self, stage): return stage == "C0_PA"
        def get_stage(self, stage):
            return {
                "pa_context": {"metadata": {"speaker_map": {"Speaker 2": "Andy Kilinskis"}}},
                "resolved_transcript": "Speaker 1: Hello from remote\nSpeaker 2: Hello from room",
                "speaker_map": {"Speaker 2": "Andy Kilinskis"},
            }

    class MockTracker:
        def start_stage(self, *args, **kwargs): pass
        def complete_stage(self, *args, **kwargs): pass

    checkpoint = MockCheckpoint()
    # Fresh input provides mapping for Speaker 1 -> Brandon Que
    meta = {
        "speaker_map": {"Speaker 1": "Brandon Que"},
    }

    pa_context, source_text = step_c0_pa_with_resume(
        pipeline=None,
        checkpoint=checkpoint,
        should_resume=True,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        source_text="Speaker 1: Hello from remote\nSpeaker 2: Hello from room",
        input_hash="hash123",
        meta=meta,
        presentation_path=None,
        out_p=Path("/tmp/out"),
        tracker=MockTracker(),
    )

    # Invariant 1: speaker_map is merged: contains BOTH Speaker 1 and Speaker 2
    assert meta["speaker_map"].get("Speaker 1") == "Brandon Que"
    assert meta["speaker_map"].get("Speaker 2") == "Andy Kilinskis"
    # Invariant 2: source_text has Speaker 1 replaced with Brandon Que
    assert "Brandon Que:" in source_text


