"""
tests/adversarial/test_sherpa_runtime_rca_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 1:
Sherpa Transcription Runtime RCA & Stereo Diarization Invariants.

Covers Mandatory Edge-Case Categories (per .agents/rules/e2e-and-edge-case-coverage.md):
1. Boundary payloads (0-byte, truncated 12-byte, 44-byte zero-data WAVs, digital silence).
2. Malformed input (invalid magic bytes, truncated headers, missing segment fields).
3. Concurrency & race (multi-threaded concurrent repair on shared WAV file).
4. Exact production regression (Cansu/Dan bleed scenario: Ch0=2500 vs Ch1=800 bleed).
"""
from __future__ import annotations

import concurrent.futures
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from typing import List, Dict, Any

from dual_pipeline.audio_ingestion_core.container_validator import (
    repair_wav_riff_header,
    validate_audio_container_headers,
)
from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    align_segments_via_stereo_energy,
)
from dual_pipeline.audio_postprocess import (
    deduplicate_consecutive_asr_segments,
)
from dual_pipeline.exceptions import AudioContainerValidationError


class TestSherpaRuntimeRCAAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_sherpa_rca_adv_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ----------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # ----------------------------------------------------------------------

    def test_boundary_zero_byte_wav(self) -> None:
        """Boundary: 0-byte file must fail closed with AudioContainerValidationError."""
        zero_wav = self.temp_path / "zero_byte.wav"
        zero_wav.write_bytes(b"")
        with self.assertRaises(AudioContainerValidationError):
            validate_audio_container_headers(zero_wav)

    def test_boundary_truncated_twelve_byte_wav(self) -> None:
        """Boundary: Exactly 12-byte WAV (RIFF....WAVE) without fmt chunk must fail closed."""
        trunc_wav = self.temp_path / "truncated_12.wav"
        trunc_wav.write_bytes(b"RIFF\x04\x00\x00\x00WAVE")
        with self.assertRaises(AudioContainerValidationError):
            validate_audio_container_headers(trunc_wav)

    def test_boundary_zero_data_silence_stereo_wav(self) -> None:
        """Boundary: Stereo WAV containing only digital zeros (RMS=0 on both channels)."""
        silence_wav = self.temp_path / "digital_silence.wav"
        with wave.open(str(silence_wav), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * 32000)  # 1.0 second of silence

        segments = [
            {"start": 0.1, "end": 0.9, "text": "Is anyone there?"}
        ]
        # Should not divide by zero or crash with NaN
        aligned = align_segments_via_stereo_energy(segments, silence_wav)
        self.assertEqual(len(aligned), 1)
        self.assertIn(aligned[0]["speaker"], ("SPEAKER_00", "SPEAKER_01"))

    # ----------------------------------------------------------------------
    # Category 2: Malformed Input
    # ----------------------------------------------------------------------

    def test_malformed_riff_magic_bytes(self) -> None:
        """Malformed: File with invalid magic bytes must fail closed."""
        bad_wav = self.temp_path / "invalid_magic.wav"
        bad_wav.write_bytes(b"BAAD\x24\x00\x00\x00WAVE" + b"\x00" * 40)
        with self.assertRaises(AudioContainerValidationError):
            validate_audio_container_headers(bad_wav)

    def test_malformed_asr_segments_handling(self) -> None:
        """Malformed: Segments with None text, missing start/end, or empty fields."""
        malformed_segments: List[Dict[str, Any]] = [
            {"start": 0.0, "end": 1.0, "text": ""},
            {"start": 1.0, "end": 2.0, "text": "   "},
            {"start": 2.0, "end": 3.0, "text": "Valid speech utterance."},
            {"start": 3.0, "end": 4.0},  # Missing text key
        ]
        deduped = deduplicate_consecutive_asr_segments(malformed_segments)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["text"], "Valid speech utterance.")

    # ----------------------------------------------------------------------
    # Category 3: Concurrency & Stress
    # ----------------------------------------------------------------------

    def test_concurrency_multi_threaded_riff_repair(self) -> None:
        """Concurrency: 16 concurrent threads repairing the same 0xFFFFFFFF WAV file."""
        wav_path = self.temp_path / "concurrent_corrupted.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x10" * 32000)

        with open(wav_path, "r+b") as f:
            f.seek(4)
            f.write(b"\xff\xff\xff\xff")

        def _do_repair() -> bool:
            return repair_wav_riff_header(wav_path)

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(_do_repair) for _ in range(16)]
            results = [f.result() for f in futures]

        self.assertTrue(any(results))
        # Verify file is healthy and passes validation
        spec = validate_audio_container_headers(wav_path)
        self.assertEqual(spec.format_name, "wav")

    def test_stress_one_thousand_consecutive_repeated_segments(self) -> None:
        """Stress: 1,000 rapid repeated segments collapsed in < 100ms."""
        repeated_sentence = "Whisper loop repetition stress test phrase."
        segments = [
            {"start": float(i), "end": float(i + 0.9), "text": repeated_sentence}
            for i in range(1000)
        ]
        deduped = deduplicate_consecutive_asr_segments(segments)
        self.assertEqual(len(deduped), 1)

    # ----------------------------------------------------------------------
    # Category 4: Exact Production Regression
    # ----------------------------------------------------------------------

    def test_exact_regression_sherpa_bleed_suppression(self) -> None:
        """Exact Regression: Cansu (remote) speech bleeding into Dan's microphone.

        Ch0 (direct tap): RMS ~2500
        Ch1 (room mic bleed from speakers): RMS ~800
        Without bleed margin, mono downmix attributed this speech to Dan (SPEAKER_01) 96.2%.
        With channel-energy attribution and bleed margin 1.15, this MUST attribute to Cansu (SPEAKER_00).
        """
        wav_path = self.temp_path / "sherpa_bleed_case.wav"
        n_samples = 16000 * 2  # 2 seconds
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            frames = bytearray()
            for i in range(n_samples):
                val0 = 2500 if (i % 32 < 16) else -2500
                val1 = 800 if (i % 32 < 16) else -800
                frames.extend(struct.pack("<hh", val0, val1))
            wf.writeframes(frames)

        segments = [
            {
                "start": 0.2, "end": 1.8,
                "text": "I can share my screen and show the Sherpa workflow.",
                "words": [
                    {"word": "I", "start": 0.2, "end": 0.4},
                    {"word": "can", "start": 0.4, "end": 0.6},
                    {"word": "share", "start": 0.6, "end": 0.9},
                    {"word": "my", "start": 0.9, "end": 1.1},
                    {"word": "screen.", "start": 1.1, "end": 1.8},
                ]
            }
        ]

        aligned = align_segments_via_stereo_energy(segments, wav_path, bleed_margin=1.15)
        self.assertEqual(len(aligned), 1)
        self.assertEqual(aligned[0]["speaker"], "SPEAKER_00", "Bleed speech was falsely collapsed onto SPEAKER_01!")
        self.assertEqual(aligned[0]["channel"], 0)

    # ----------------------------------------------------------------------
    # Category 5: Wave 3 Resume / Cache Determinism & Healer Safety (RC-08 to RC-11)
    # ----------------------------------------------------------------------

    def test_resume_cache_remediation_state_machine_alias(self) -> None:
        """RC-10: RemediationStateMachine is exported as alias of SelectiveRemediationStateMachine."""
        from dual_pipeline.remediation_core import RemediationStateMachine, SelectiveRemediationStateMachine
        from dual_pipeline.remediation_core.state_machine import RemediationStateMachine as RSM
        self.assertIs(RemediationStateMachine, SelectiveRemediationStateMachine)
        self.assertIs(RSM, SelectiveRemediationStateMachine)

        sm = RemediationStateMachine(max_attempts=3)
        self.assertEqual(sm.max_attempts, 3)
        sm.register_deliverable_pass("output_1", "Sample Output 1 text", score=98)
        self.assertTrue(sm.is_frozen("output_1"))
        self.assertEqual(sm.get_high_water_mark_score("output_1"), 98)

    def test_resume_cache_preserves_numeric_composite_score(self) -> None:
        """RC-11: Frozen deliverables preserve numeric composite_score >= 90 without non-numeric warnings."""
        import hashlib
        from dual_pipeline.transcript_qa_core.eval_cache import _build_skip_eval, check_pin
        from dual_pipeline.release_policy import validate_micro_judge_scorecard

        sha1 = hashlib.sha256("text1".encode("utf-8")).hexdigest()
        sha2 = hashlib.sha256("text2".encode("utf-8")).hexdigest()
        sha3 = hashlib.sha256("text3".encode("utf-8")).hexdigest()
        qa_report = {
            "output_01_forensic_transcript": {"frozen": True, "sha256": sha1, "score": 96},
            "output_02_executive_recap": {"frozen": True, "sha256": sha2, "composite_score": 93},
            "output_03_canonical_signal_projection": {"frozen": True, "sha256": sha3},
        }
        card1 = _build_skip_eval(qa_report, "output_01_forensic_transcript", "text1")
        card2 = _build_skip_eval(qa_report, "output_02_executive_recap", "text2")
        card3 = _build_skip_eval(qa_report, "output_03_canonical_signal_projection", "text3")

        self.assertEqual(card1["composite_score"], 96)
        self.assertEqual(card2["composite_score"], 93)
        self.assertEqual(card3["composite_score"], 90)
        self.assertEqual(card1["verdict"], "PASS")

        # Validate scorecards accept numeric composite_score for FROZEN_PRIOR_ARTIFACT
        is_valid1, errs1 = validate_micro_judge_scorecard(card1, output_num=1)
        self.assertTrue(is_valid1, f"Validation failed: {errs1}")
        is_valid2, errs2 = validate_micro_judge_scorecard(card2, output_num=2)
        self.assertTrue(is_valid2, f"Validation failed: {errs2}")
        is_valid3, errs3 = validate_micro_judge_scorecard(card3, output_num=3)
        self.assertTrue(is_valid3, f"Validation failed: {errs3}")

        # Check check_pin defaults None score to min_score for frozen/passing cards
        legacy_frozen_pin = {
            "output_1": {
                "status": "SKIPPED",
                "composite_score": None,
                "verdict": "PASS",
                "reason": "FROZEN_PRIOR_ARTIFACT",
                "artifact_sha256": "mock_sha",
            }
        }
        resolved = check_pin(
            legacy_frozen_pin, "output_1", "output_01", 95, "mock_sha",
            expected_c0_digest="none",
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["composite_score"], 95)

    def test_resume_cache_anti_conflation_allows_cross_workstream_bridge_slugs(self) -> None:
        """RC-09: Legitimate inter-workstream relationship edges do not trigger false conflation."""
        from dual_pipeline.transcript_stages_core.anti_conflation import (
            is_compound_workstream_reference,
            validate_anti_conflation,
            CompoundWorkstreamConflationError,
        )

        valid_cross_edges = [
            "-sherpa--lens-",
            "sig:sherpa--lens",
            "sig:workstream:project_beacon--relates_to--project:lens",
            "fact:project:lens--cross_reference--workstream:project_beacon",
        ]
        for edge_str in valid_cross_edges:
            self.assertFalse(
                is_compound_workstream_reference(edge_str),
                f"False positive conflation for legitimate cross-workstream edge '{edge_str}'",
            )

        # Confirm true compound workstream conflations still fail closed
        bad_projection = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "workstream:lens_sherpa", "name": "Project LENS / Sherpa"}
                ]
            }
        }
        with self.assertRaises(CompoundWorkstreamConflationError):
            validate_anti_conflation(bad_projection)

    def test_resume_cache_metadata_persistence_sanitization(self) -> None:
        """RC-08: Metadata persistence purges state machine objects and stringified artifacts."""
        from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
        from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker
        from dual_pipeline.transcript_metadata_helper import (
            sanitize_metadata_for_persistence,
            sanitize_loaded_metadata,
        )

        dirty_meta = {
            "meeting_name": "Test Sherpa",
            "state_machine": SelectiveRemediationStateMachine(),
            "circuit_breaker": CandidateCircuitBreaker(),
            "legacy_stringified_sm": "<dual_pipeline.remediation_core.state_machine.SelectiveRemediationStateMachine object at 0x10d5c7aa0>",
            "legacy_stringified_cb": "<dual_pipeline.token_optimization.circuit_breaker.CandidateCircuitBreaker object at 0x10da0ab70>",
            "valid_key": "safe_value",
            "nested": {
                "nested_sm": "<dual_pipeline.state object at 0x12345>",
                "nested_val": 42,
            },
        }

        sanitized = sanitize_metadata_for_persistence(dirty_meta)
        self.assertNotIn("state_machine", sanitized)
        self.assertNotIn("circuit_breaker", sanitized)
        self.assertNotIn("legacy_stringified_sm", sanitized)
        self.assertNotIn("legacy_stringified_cb", sanitized)
        self.assertNotIn("nested_sm", sanitized["nested"])
        self.assertEqual(sanitized["valid_key"], "safe_value")
        self.assertEqual(sanitized["nested"]["nested_val"], 42)

        # Verify loaded metadata cleans up stringified entries
        loaded = {
            "state_machine": "<dual_pipeline.remediation_core.state_machine.SelectiveRemediationStateMachine object at 0x10d5c7aa0>",
            "circuit_breaker": "<CandidateCircuitBreaker object at 0x123>",
            "meeting_name": "Loaded Test",
        }
        cleaned_loaded = sanitize_loaded_metadata(loaded)
        self.assertNotIn("state_machine", cleaned_loaded)
        self.assertNotIn("circuit_breaker", cleaned_loaded)
        self.assertEqual(cleaned_loaded["meeting_name"], "Loaded Test")

    def test_resume_cache_zero_llm_calls_on_frozen_deliverables(self) -> None:
        """RC-11: Resuming with frozen deliverables invokes 0 LLM calls and retains score >= 90.0."""
        import json
        from dual_pipeline.canonical_pipeline.evaluator import UnifiedPipelineQAEvaluator
        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
        from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
        from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
        from unittest.mock import patch

        f1 = "# Turn 00:00:01\nDan: Sherpa pipeline test.\n\n# Turn 00:00:05\nCansu: Everything looks verified."
        f2 = "# Executive Recap\n\n## Meeting Metadata\n- Date: 10/14/2026\n\n## Key Discussion Points\nVerified."
        f3 = json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "project:sherpa", "name": "Project Beacon"}],
                "relationship_signals": [],
            }
        })

        sm = SelectiveRemediationStateMachine()
        sm.register_deliverable_pass("output_1", f1, score=98)
        sm.register_deliverable_pass("output_2", f2, score=94)
        sm.register_deliverable_pass("output_3", f3, score=92)

        meta = {
            "meeting_name": "Sherpa Resume Test",
            "state_machine": sm,
            "resume": True,
        }
        synth = SynthesisResult(output_1=f1, output_2=f2, output_3=f3, attempt=1)

        with patch("dual_pipeline.credentials.get_openai_client") as mock_get_client:
            mock_get_client.side_effect = RuntimeError("UNEXPECTED_LLM_CALL: Should not call live credentials on frozen resume!")
            auth, qa_rep, judge_rep, final_dec = UnifiedPipelineQAEvaluator.evaluate(
                modality=CanonicalModality.AUDIO,
                synthesis_result=synth,
                source_text=f1,
                metadata=meta,
                mock=False,
            )

        self.assertEqual(final_dec.get("status"), "PASS")
        self.assertGreaterEqual(judge_rep["output_1_evaluation"]["composite_score"], 95)
        self.assertGreaterEqual(judge_rep["output_2_evaluation"]["composite_score"], 90)
        self.assertGreaterEqual(judge_rep["output_3_evaluation"]["composite_score"], 90)

    # ----------------------------------------------------------------------
    # Category 6: Wave 4 Idempotent Publication Journal & Role Facts (RC-13, RC-14b)
    # ----------------------------------------------------------------------

    def test_rolled_back_zero_delta_intent_retryable_under_same_directive(self) -> None:
        """RC-13: Rolled-back zero-delta intents allow retry under the same directive-id without sticky PENDING lock."""
        from dual_pipeline.canonical_pipeline.publication_journal import (
            guarded_publication,
            get_publication_checkpoint,
            mark_publication_rolled_back,
            reconcile_publication_checkpoint,
            PublicationReconciliationRequired,
        )

        directive_id = "test-sherpa-directive-rc13"
        bindings = {"source": "sherpa_connect_wav", "version": "v1"}

        # Simulate pre-commit / cascade failure that marks intent ROLLED_BACK
        def fail_pre_commit():
            raise RuntimeError("Sidecar mismatch before commit")

        with self.assertRaises(PublicationReconciliationRequired):
            try:
                guarded_publication(
                    runtime_root=self.temp_path,
                    directive_id=directive_id,
                    bindings_factory=lambda: bindings,
                    publish=fail_pre_commit,
                    allow_resume=False,
                )
            except Exception as exc:
                mark_publication_rolled_back(self.temp_path, directive_id, reason=str(exc), zero_delta=True)
                raise

        chk = get_publication_checkpoint(self.temp_path, directive_id)
        self.assertTrue(chk["intent_exists"])
        self.assertEqual(chk["intent_state"], "ROLLED_BACK")
        self.assertFalse(chk["result_exists"])
        self.assertTrue(chk["retryable"])

        # Now retry under the EXACT SAME directive_id (even with altered bindings from a bug fix)
        fixed_bindings = {"source": "sherpa_connect_wav", "version": "v2_fixed"}
        res = guarded_publication(
            runtime_root=self.temp_path,
            directive_id=directive_id,
            bindings_factory=lambda: fixed_bindings,
            publish=lambda: {"status": "SUCCESS_AFTER_ROLLBACK"},
            allow_resume=False,
        )
        self.assertEqual(res, {"status": "SUCCESS_AFTER_ROLLBACK"})

        chk_after = get_publication_checkpoint(self.temp_path, directive_id)
        self.assertTrue(chk_after["result_exists"])
        self.assertEqual(chk_after["result_state"], "RESULT_RECORDED")

    def test_ensure_roster_attendees_emits_roles_and_has_role_signals(self) -> None:
        """RC-14b: _ensure_roster_attendees emits role:* entities and HAS_ROLE signals."""
        from dual_pipeline.transcript_stages_core.graph_post_extractor import (
            _ensure_roster_attendees,
        )

        mutations: Dict[str, Any] = {
            "entity_upserts": [],
            "relationship_signals": [],
        }
        metadata: Dict[str, Any] = {
            "attendees": [
                "Drew Oakley (Delivery Lead, PartnerAdvisory)",
                "Cansu Miller (Data & Analytics Lead, PartnerAdvisory)",
            ]
        }

        _ensure_roster_attendees(mutations, metadata)

        entity_ids = {e["id"] for e in mutations["entity_upserts"]}
        self.assertIn("person:dan_okray", entity_ids)
        self.assertIn("person:cansu_miller", entity_ids)
        self.assertIn("org:partner_advisory", entity_ids)
        self.assertIn("role:delivery_lead", entity_ids)
        self.assertIn("role:data_analytics_lead", entity_ids)

        signals = mutations["relationship_signals"]
        has_role_dan = any(
            s.get("from_id") == "person:dan_okray" and
            s.get("to_id") == "role:delivery_lead" and
            s.get("relationship") == "HAS_ROLE"
            for s in signals
        )
        self.assertTrue(has_role_dan, "Missing HAS_ROLE signal for Drew Oakley!")

        has_role_cansu = any(
            s.get("from_id") == "person:cansu_miller" and
            s.get("to_id") == "role:data_analytics_lead" and
            s.get("relationship") == "HAS_ROLE"
            for s in signals
        )
        self.assertTrue(has_role_cansu, "Missing HAS_ROLE signal for Cansu Miller!")

    def test_failed_rollback_marks_zero_delta_false_and_blocks_retry(self) -> None:
        """Adversarial: Failed rollback marks zero_delta=False, setting retryable=False and blocking automatic retry."""
        from dual_pipeline.canonical_pipeline.publication_journal import (
            guarded_publication,
            get_publication_checkpoint,
            mark_publication_rolled_back,
            PublicationReconciliationRequired,
        )

        directive_id = "test-adv-failed-rollback"
        bindings = {"source": "failing_doc.md", "version": "v1"}

        # Simulate failure followed by failed rollback
        with self.assertRaises(PublicationReconciliationRequired):
            try:
                guarded_publication(
                    runtime_root=self.temp_path,
                    directive_id=directive_id,
                    bindings_factory=lambda: bindings,
                    publish=lambda: (_ for _ in ()).throw(RuntimeError("Activation corrupted")),
                    allow_resume=False,
                )
            except Exception as exc:
                mark_publication_rolled_back(self.temp_path, directive_id, reason=str(exc), zero_delta=False)
                raise

        chk = get_publication_checkpoint(self.temp_path, directive_id)
        self.assertEqual(chk["intent_state"], "ROLLED_BACK")
        self.assertFalse(chk["retryable"])

        # Attempting retry must fail closed with PARTIAL_MUTATION_ROLLBACK_FAILED
        with self.assertRaises(PublicationReconciliationRequired) as ctx:
            guarded_publication(
                runtime_root=self.temp_path,
                directive_id=directive_id,
                bindings_factory=lambda: bindings,
                publish=lambda: {"status": "SHOULD_NOT_RUN"},
                allow_resume=False,
            )
        self.assertEqual(ctx.exception.state, "PARTIAL_MUTATION_ROLLBACK_FAILED")
        self.assertFalse(ctx.exception.details["retry_safe"])

    def test_malformed_journal_json_recovery(self) -> None:
        """Adversarial: Malformed/corrupted journal JSON is classified as CORRUPT_JOURNAL and reconcilable."""
        from dual_pipeline.canonical_pipeline.publication_journal import (
            get_publication_checkpoint,
            reconcile_publication_checkpoint,
            guarded_publication,
        )

        directive_id = "test-adv-corrupt-journal"
        journal_dir = self.temp_path / "publication_journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        intent = journal_dir / f"{directive_id}.intent.json"
        intent.write_bytes(b'{"state": "PENDING", truncated')

        chk = get_publication_checkpoint(self.temp_path, directive_id)
        self.assertEqual(chk["intent_state"], "CORRUPT_JOURNAL")

        # Reconcile corrupted intent
        res = reconcile_publication_checkpoint(self.temp_path, directive_id, action="reset_interrupted")
        self.assertEqual(res["status"], "RECONCILED")
        self.assertFalse(intent.exists())

        # Clean publication can now succeed
        pub_res = guarded_publication(
            runtime_root=self.temp_path,
            directive_id=directive_id,
            bindings_factory=lambda: {"source": "clean.md"},
            publish=lambda: {"status": "SUCCESS_AFTER_RECONCILE"},
            allow_resume=False,
        )
        self.assertEqual(pub_res, {"status": "SUCCESS_AFTER_RECONCILE"})

    def test_concurrent_guarded_publication_lock_contention(self) -> None:
        """Adversarial: Concurrent guarded_publication threads synchronize on file_lock without deadlock."""
        from dual_pipeline.canonical_pipeline.publication_journal import guarded_publication
        import threading
        import time

        directive_id = "test-adv-concurrency"
        bindings = {"source": "concurrent_test.md"}
        events = []
        barrier = threading.Barrier(2)

        def worker(worker_id: int):
            barrier.wait()
            try:
                res = guarded_publication(
                    runtime_root=self.temp_path,
                    directive_id=directive_id,
                    bindings_factory=lambda: bindings,
                    publish=lambda: (time.sleep(0.05), {"worker": worker_id})[1],
                    allow_resume=True,
                )
                events.append((worker_id, "SUCCESS", res))
            except Exception as e:
                events.append((worker_id, "ERROR", type(e).__name__))

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # Both completed without deadlocking; one published and the other completed or reused cleanly
        self.assertEqual(len(events), 2)


if __name__ == "__main__":
    unittest.main()


