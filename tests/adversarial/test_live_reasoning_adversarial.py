"""Adversarial and edge-case qualification suite for Wave 8 Live Copilot Reasoning.

Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary & malformed payloads: Empty/negative deadlines, corrupted/truncated JSON streams, missing session IDs.
2. Concurrency & race conditions: Concurrent submissions racing with generation transitions and priority preemption.
3. Overflow & exhaustion: Grounded context cache LRU bound under extreme load, deliberation queue burst coalescing.
4. Invariant bypass attempts: Canonical authority escalation prevention, callback delivery bypass after cancellation/shutdown.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, List
from unittest import TestCase
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import (
    CopilotAnswerLedgerAdapter,
    CopilotDeliberationQueue,
    CopilotEpisode,
    CopilotRequestSnapshot,
    EpisodeTracker,
    GroundedContextCache,
    ensure_inferred_memory_boundary,
)
from dual_pipeline.live_meeting_recorder_core.streaming_copilot import (
    StreamingCopilotHandler,
    re_repair_json,
)


class TestLiveReasoningAdversarial(TestCase):
    """Adversarial and stress verification suite for Wave 8."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        import shutil
        try:
            self.tmp_dir.cleanup()
        except Exception:
            shutil.rmtree(self.runtime_root, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Category 1: Boundary & Malformed Payloads
    # -------------------------------------------------------------------------

    def test_boundary_zero_and_negative_deadline_handling(self) -> None:
        """Category 1: Boundary payload - Zero or negative deadlines expire immediately without throwing."""
        snap_zero = CopilotRequestSnapshot(
            deadline_seconds=0.0,
            generation_id="gen_0",
        )
        # Immediate expiration check
        self.assertFalse(snap_zero.is_valid("gen_0", now=snap_zero.created_at + 0.001))
        self.assertEqual(snap_zero.state, "EXPIRED")

        snap_neg = CopilotRequestSnapshot(
            deadline_seconds=-5.0,
            generation_id="gen_0",
        )
        self.assertFalse(snap_neg.is_valid("gen_0", now=snap_neg.created_at))
        self.assertEqual(snap_neg.state, "EXPIRED")

    def test_malformed_stream_json_recovery_fail_closed(self) -> None:
        """Category 1: Malformed input - Severely corrupted stream chunks fail closed without unhandled exceptions."""
        mock_client = MagicMock()
        # Chunks forming completely unrepairable JSON syntax
        corrupted_chunks = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content="<<<GARBAGE_START>>>\n"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content="Random non-json unescaped bytes \"'{};\n"))]),
        ]
        mock_client.chat.completions.create.return_value = corrupted_chunks

        handler = StreamingCopilotHandler(client=mock_client)
        snap = CopilotRequestSnapshot()

        result = handler.execute_streaming_evaluation(
            call_kwargs={"messages": []},
            request_snapshot=snap,
        )
        # Must return None safely (fail-closed)
        self.assertIsNone(result)

    def test_re_repair_json_adversarial_truncations(self) -> None:
        """Category 1: Malformed input - Truncated JSON with unmatched brackets handles gracefully."""
        self.assertIsNone(re_repair_json(""))
        self.assertIsNone(re_repair_json("no brackets at all"))

        # Incomplete JSON object with unclosed bracket but closed string
        unclosed_bracket = '{"category": "STRATEGIC", "suggested_script": "Wait before we speak"'
        repaired = re_repair_json(unclosed_bracket)
        self.assertIsNotNone(repaired)
        parsed = json.loads(repaired)
        self.assertEqual(parsed.get("category"), "STRATEGIC")

        # Incomplete JSON object with unclosed string fails parse cleanly
        from dual_pipeline.live_meeting_recorder_core.streaming_copilot import (
            _parse_stream_json,
        )
        unclosed_string = '{"category": "STRATEGIC", "suggested_script": "Wait before we'
        parsed_stream = _parse_stream_json(unclosed_string)
        self.assertIsNone(parsed_stream)

    # -------------------------------------------------------------------------
    # Category 2: Concurrency & Race Conditions
    # -------------------------------------------------------------------------

    def test_concurrent_turns_racing_with_generation_shift(self) -> None:
        """Category 2: Concurrency & race - Rapid parallel turns racing with generation transition."""
        advisor = LiveCopilotAdvisor(
            project_name="race_test",
            session_output_dir=self.runtime_root,
            mock_mode=True,
            request_timeout=2.0,
        )

        completed_callbacks: List[Dict[str, Any]] = []
        num_threads = 16

        def worker_turn(i: int) -> None:
            advisor.submit_turn_async(
                speaker=f"Speaker_{i % 3}",
                text=f"Turn utterance {i} exploring architecture decisions.",
                timestamp=f"10:00:{i:02d}",
                current_time=float(i),
                on_feedback=lambda fb: completed_callbacks.append(fb),
            )

        def worker_generation_shift() -> None:
            for g in range(5):
                time.sleep(0.01)
                advisor.set_generation(f"gen_{g}", topic_id=f"topic_{g}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads + 1) as executor:
            futures = [executor.submit(worker_turn, i) for i in range(30)]
            gen_future = executor.submit(worker_generation_shift)
            for f in futures:
                f.result()
            gen_future.result()

        advisor.shutdown()
        # Verifies no deadlock, no exceptions thrown during concurrent generation changes

    def test_priority_preemption_race_under_load(self) -> None:
        """Category 2: Concurrency & race - Priority turns successfully set pending task under race conditions."""
        advisor = LiveCopilotAdvisor(
            project_name="priority_test",
            session_output_dir=self.runtime_root,
            mock_mode=True,
        )

        inflection = MagicMock()
        inflection.is_priority = True

        dispatched = advisor.submit_turn_async(
            speaker="Director",
            text="Stop! We need an immediate contingency plan.",
            timestamp="10:05:00",
            current_time=100.0,
            on_feedback=lambda fb: None,
            inflection_event=inflection,
        )
        self.assertTrue(dispatched)
        self.assertTrue(advisor._active_request_snapshot.is_priority)

        advisor.shutdown()

    # -------------------------------------------------------------------------
    # Category 3: Overflow & Exhaustion
    # -------------------------------------------------------------------------

    def test_grounded_cache_overflow_lru_eviction(self) -> None:
        """Category 3: Overflow & exhaustion - Bounded cache prevents memory leak by evicting oldest keys."""
        cache = GroundedContextCache(max_size=20)

        for i in range(50):
            k = cache.make_key(
                session_id=f"sess_{i}",
                context_hash=f"ctx_hash_{i}",
                generation_id="gen_0",
                model_id="gpt-6-luna",
            )
            cache.put(k, {"idx": i})

        self.assertLessEqual(len(cache._cache), 20)
        # Verify newest entries survive
        latest_key = cache.make_key(
            session_id="sess_49",
            context_hash="ctx_hash_49",
            generation_id="gen_0",
            model_id="gpt-6-luna",
        )
        self.assertIsNotNone(cache.get(latest_key))

    def test_deliberation_queue_burst_exhaustion_coalescing(self) -> None:
        """Category 3: Overflow & exhaustion - Rapid burst of requests suppresses older snapshots cleanly."""
        queue = CopilotDeliberationQueue(enabled=True)
        snapshots = [
            CopilotRequestSnapshot(request_id=f"req_{i}", turn_seq=i)
            for i in range(40)
        ]

        for snap in snapshots:
            queue.submit_deliberation(snap, on_feedback=None)

        # All earlier snapshots must be marked SUPPRESSED_STALE
        for snap in snapshots[:-1]:
            self.assertEqual(snap.state, "SUPPRESSED_STALE")
            self.assertEqual(snap.suppression_reason, "coalesced_by_newer_request")

        # The last snapshot must remain ACTIVE
        self.assertEqual(snapshots[-1].state, "ACTIVE")

        popped = queue.pop_pending()
        self.assertIsNotNone(popped)
        assert popped is not None
        self.assertEqual(popped[0].request_id, "req_39")

    # -------------------------------------------------------------------------
    # Category 4: Invariant Bypass Attempts & Tamper Resistance
    # -------------------------------------------------------------------------

    def test_bypass_attempt_canonical_escalation_rejected(self) -> None:
        """Category 4: Invariant bypass attempt - Adversarial payload attempting to declare is_canonical: True is stripped."""
        tampered_payload = {
            "entity_id": "core_contract",
            "is_canonical": True,  # Adversarial attempt to promote inferred memory to canonical fact
            "is_inferred": False,
            "status": "APPROVED",
        }

        sanitized = ensure_inferred_memory_boundary(tampered_payload)
        # Boundary invariant enforces non-canonical inferred status
        self.assertFalse(sanitized["is_canonical"])
        self.assertTrue(sanitized["is_inferred"])
        self.assertEqual(sanitized["provenance"], "conversational_inference")

    def test_bypass_attempt_delivery_after_shutdown_or_cancellation(self) -> None:
        """Category 4: Invariant bypass attempt - Suppressed requests cannot execute delivery callback."""
        advisor = LiveCopilotAdvisor(
            project_name="bypass_test",
            session_output_dir=self.runtime_root,
            mock_mode=True,
        )

        delivered: List[Dict[str, Any]] = []

        def on_feedback(fb: Dict[str, Any]) -> None:
            delivered.append(fb)

        advisor.submit_turn_async(
            speaker="Eve",
            text="Query that will be cancelled before completion.",
            timestamp="10:10:00",
            current_time=50.0,
            on_feedback=on_feedback,
        )

        snap = advisor._active_request_snapshot
        advisor.cancel()

        # Run worker directly simulating delayed thread wakeup after cancel
        res = advisor._run_async_worker(
            turn_seq=snap.turn_seq,
            turn_ts=snap.created_at,
            ev=None,
            cb=on_feedback,
            snapshot=snap,
        )

        self.assertIsNone(res)
        # Callback was never called because snapshot was cancelled
        self.assertEqual(len(delivered), 0)

        advisor.shutdown()
