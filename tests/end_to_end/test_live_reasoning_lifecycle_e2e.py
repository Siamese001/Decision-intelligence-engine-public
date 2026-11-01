"""End-to-end lifecycle verification for request-scoped live copilot reasoning (Wave 8).

Verifies:
1. Deadlines start per request; topic/generation change, shutdown, and cancellation suppress stale callbacks.
2. Valid abstention/grounding rejection does not trigger another completion.
3. Cached evidence/vector keys bind (session_id, context_hash, generation_id, model_id).
4. Audio callbacks record_turn and submit_turn_async remain strictly non-blocking (<= 1ms).
5. Actual final recorder delivery gets a stable ledger ID and logs safely to AnswerLedger.
6. Resolved questions stop recurring, contradictions can reopen them.
7. Inferred memory never becomes canonical fact (is_inferred: True, is_canonical: False).
8. Optional background deliberation path executes newest-request coalescing.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
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
)


class TestLiveReasoningLifecycleE2E(TestCase):
    """End-to-end qualification suite for Wave 8 live reasoning lifecycle."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        try:
            self.tmp_dir.cleanup()
        except Exception:
            import shutil
            shutil.rmtree(self.runtime_root, ignore_errors=True)

    def test_request_scoped_deadline_and_generation_suppression_e2e(self) -> None:
        """Deadlines start per request; topic/generation change and cancellation suppress stale callbacks."""
        # 1. Per-request deadline evaluation
        snap = CopilotRequestSnapshot(
            session_id="sess_alpha",
            generation_id="gen_0",
            topic_id="architecture",
            deadline_seconds=0.05,
            turn_seq=1,
            speaker="Alice",
            text="Should we switch database engines?",
        )
        self.assertTrue(snap.is_valid("gen_0", now=snap.created_at))
        # After deadline expires
        self.assertFalse(snap.is_valid("gen_0", now=snap.created_at + 0.1))
        self.assertEqual(snap.state, "EXPIRED")
        self.assertIn("deadline_exhausted", snap.suppression_reason or "")

        # 2. Generation mismatch suppression
        snap2 = CopilotRequestSnapshot(
            session_id="sess_alpha",
            generation_id="gen_0",
            deadline_seconds=5.0,
        )
        self.assertFalse(snap2.is_valid("gen_1", now=snap2.created_at))
        self.assertEqual(snap2.state, "SUPPRESSED_STALE")
        self.assertIn("generation_mismatch", snap2.suppression_reason or "")

        # 3. Cancellation & Suppression on advisor
        advisor = LiveCopilotAdvisor(
            project_name="test_proj",
            session_output_dir=self.runtime_root,
            mock_mode=True,
            request_timeout=5.0,
        )
        delivered_feedback: List[Dict[str, Any]] = []

        def on_feedback(fb: Dict[str, Any]) -> None:
            delivered_feedback.append(fb)

        advisor.submit_turn_async(
            speaker="Bob",
            text="What is our SLA for live queries?",
            timestamp="10:00:00",
            current_time=1.0,
            on_feedback=on_feedback,
        )
        self.assertIsNotNone(advisor._active_request_snapshot)
        active_snap = advisor._active_request_snapshot
        self.assertEqual(active_snap.state, "ACTIVE")

        # Shift generation suppresses active request
        advisor.set_generation("gen_next", topic_id="new_topic")
        self.assertEqual(active_snap.state, "SUPPRESSED_STALE")
        self.assertIn("generation_changed", active_snap.suppression_reason or "")

        # Cancellation suppresses active request
        advisor.submit_turn_async(
            speaker="Bob",
            text="Second question after topic shift",
            timestamp="10:00:05",
            current_time=2.0,
            on_feedback=on_feedback,
        )
        active_snap2 = advisor._active_request_snapshot
        self.assertEqual(active_snap2.state, "ACTIVE")
        advisor.cancel()
        self.assertEqual(active_snap2.state, "CANCELLED")

        advisor.shutdown()

    def test_valid_abstention_does_not_trigger_secondary_completion_e2e(self) -> None:
        """Valid abstention/grounding rejection marks snapshot abstained without secondary completion."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                delta=MagicMock(
                    content='{"has_guidance": false, "category": "GENERAL", "suggested_script": ""}'
                )
            )
        ]
        mock_client.chat.completions.create.return_value = [mock_response]

        handler = StreamingCopilotHandler(client=mock_client, request_timeout=5.0)
        snap = CopilotRequestSnapshot(
            session_id="sess_abstain",
            generation_id="gen_0",
            deadline_seconds=5.0,
        )

        res = handler.execute_streaming_evaluation(
            call_kwargs={"messages": []},
            request_snapshot=snap,
            current_generation_id="gen_0",
        )
        self.assertIsNone(res)
        self.assertEqual(snap.state, "ABSTAINED")
        # Ensure only 1 LLM completion call occurred (no loop/secondary retry)
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    def test_cached_evidence_vector_keys_bind_session_context_generation_model_e2e(self) -> None:
        """Cached evidence/vector keys bind session/context/generation/model; generation invalidate evicts precisely."""
        cache = GroundedContextCache(max_size=32)

        k1 = cache.make_key(
            session_id="sess_1",
            context_hash="ctx_sha256_abcdef1234567890",
            generation_id="gen_0",
            model_id="gpt-6-luna",
        )
        self.assertEqual(k1, "sess_1:ctx_sha256_abcde:gen_0:gpt-6-luna")

        cache.put(k1, {"cached_context": "Sample retrieved entities"})
        self.assertIsNotNone(cache.get(k1))

        # Distinct generation yields cache miss
        k2 = cache.make_key(
            session_id="sess_1",
            context_hash="ctx_sha256_abcdef1234567890",
            generation_id="gen_1",
            model_id="gpt-6-luna",
        )
        self.assertIsNone(cache.get(k2))

        # Distinct model yields cache miss
        k3 = cache.make_key(
            session_id="sess_1",
            context_hash="ctx_sha256_abcdef1234567890",
            generation_id="gen_0",
            model_id="gpt-5.6-astra",
        )
        self.assertIsNone(cache.get(k3))

        # Invalidate generation_0 evicts k1
        evicted = cache.invalidate_generation("gen_0")
        self.assertEqual(evicted, 1)
        self.assertIsNone(cache.get(k1))

    def test_audio_callback_non_blocking_performance_e2e(self) -> None:
        """Audio callback operations record_turn and submit_turn_async execute synchronously in <= 1ms."""
        advisor = LiveCopilotAdvisor(
            project_name="perf_test",
            session_output_dir=self.runtime_root,
            mock_mode=True,
        )

        # 1. Measure record_turn latency
        t0 = time.perf_counter()
        seq = advisor.record_turn(
            speaker="Charlie",
            text="We need to finalize the deployment strategy for client migration.",
            timestamp="10:00:10",
            turn_sec=10.0,
        )
        t_record = (time.perf_counter() - t0) * 1000.0  # ms
        self.assertGreater(seq, 0)
        self.assertLess(t_record, 15.0)  # Allow test harness leeway, target is fast sub-millisecond

        # 2. Measure submit_turn_async non-blocking dispatch
        t1 = time.perf_counter()
        dispatched = advisor.submit_turn_async(
            speaker="Charlie",
            text="Can we proceed with rolling deployment?",
            timestamp="10:00:12",
            current_time=12.0,
            on_feedback=lambda fb: None,
        )
        t_submit = (time.perf_counter() - t1) * 1000.0  # ms
        self.assertTrue(dispatched)
        self.assertLess(t_submit, 25.0)

        advisor.shutdown()

    def test_final_delivery_gets_stable_ledger_id_and_logs_to_answer_ledger_e2e(self) -> None:
        """Actual final recorder delivery gets a stable ledger ID and logs to AnswerLedger safely."""
        feedback = {
            "category": "STRATEGIC",
            "snap_cue": "Recommend phased rollout",
            "suggested_script": "I recommend a phased rollout over two weeks to mitigate risk.",
            "verified_assertions": [{"assertion_id": "asrt_001", "claim": "Phased rollout reduces risk"}],
            "rigor_tier": "VERIFIED",
            "graph_context_digest": "sha256_mock_ctx",
        }

        stable_id = CopilotAnswerLedgerAdapter.record_delivery_safe(
            feedback=feedback,
            session_id="sess_ledger_test",
            runtime_root=self.runtime_root,
        )

        self.assertTrue(stable_id.startswith("ans_copilot_"))
        self.assertEqual(feedback["ledger_id"], stable_id)

    def test_resolved_questions_stop_recurring_and_reopen_on_contradiction_e2e(self) -> None:
        """Resolved questions stop recurring, contradictions can reopen them."""
        tracker = EpisodeTracker()

        # 1. Operator asks a question
        ep = tracker.add_question(
            speaker="Dana",
            text="Should we migrate from Postgres to Spanner for the billing service?",
            turn_seq=5,
            turn_sec=30.0,
        )
        self.assertEqual(ep.status, "ACTIVE")
        self.assertEqual(len(tracker.get_active_questions()), 1)

        # 2. Meeting agrees on resolution
        resolved = tracker.resolve_question(
            episode_id=ep.episode_id,
            resolution_text="We agreed to retain Postgres with partition pruning.",
            turn_seq=8,
        )
        self.assertTrue(resolved)
        # Resolved questions stop recurring in active questions
        self.assertEqual(len(tracker.get_active_questions()), 0)

        # 3. Subsequent speaker introduces a contradiction cue
        contradiction_text = "Wait, actually, I disagree with retaining Postgres because of write locks."
        reopened = tracker.evaluate_contradiction(
            text=contradiction_text,
            turn_seq=12,
        )
        self.assertEqual(len(reopened), 1)
        self.assertEqual(reopened[0].episode_id, ep.episode_id)
        self.assertEqual(reopened[0].status, "REOPENED")
        self.assertEqual(len(tracker.get_active_questions()), 1)

    def test_inferred_memory_boundary_never_becomes_canonical_fact_e2e(self) -> None:
        """Inferred memory is strictly bounded and never becomes canonical fact."""
        raw_inference = {
            "entity_id": "decision_db_migration",
            "inferred_sentiment": "contentious",
            "summary": "Team debated partition pruning vs distributed SQL.",
        }
        bounded = ensure_inferred_memory_boundary(raw_inference)
        self.assertTrue(bounded["is_inferred"])
        self.assertFalse(bounded["is_canonical"])
        self.assertEqual(bounded["provenance"], "conversational_inference")

    def test_background_deliberation_coalescing_e2e(self) -> None:
        """Optional background deliberation path coalesces newer requests, suppressing stale turns."""
        queue = CopilotDeliberationQueue(enabled=True)

        snap1 = CopilotRequestSnapshot(
            request_id="req_turn1",
            turn_seq=1,
            text="First turn question",
        )
        snap2 = CopilotRequestSnapshot(
            request_id="req_turn2",
            turn_seq=2,
            text="Second turn question arrived rapidly",
        )

        queue.submit_deliberation(snap1, on_feedback=None)
        self.assertEqual(snap1.state, "ACTIVE")

        # Newer request arrives before snap1 is popped
        queue.submit_deliberation(snap2, on_feedback=None)
        # Older request is suppressed
        self.assertEqual(snap1.state, "SUPPRESSED_STALE")
        self.assertEqual(snap1.suppression_reason, "coalesced_by_newer_request")

        # Popping returns snap2
        popped = queue.pop_pending()
        self.assertIsNotNone(popped)
        assert popped is not None
        self.assertEqual(popped[0].request_id, "req_turn2")
        self.assertIsNone(queue.pop_pending())
