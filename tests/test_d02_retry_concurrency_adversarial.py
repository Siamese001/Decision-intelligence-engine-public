"""
tests/test_d02_retry_concurrency_adversarial.py

Adversarial and E2E test suite for Deliverable 02 (Executive Recap) Wave 3:
- Single retry owner (transport handles 429/500/503/network; pass layer retries ONLY on WordFloorError).
- Non-retryable errors (TimeoutError, HTTPError, etc.) are terminal at pass level.
- urlopen timeout adheres to min(120.0, remaining_deadline).
- Sibling aborts via shared threading.Event on terminal failure.
- Process-wide semaphore capping concurrent Gemini calls.
- Total calls per attempt worst case <= 9.
"""

from __future__ import annotations

import io
import json
import threading
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from config.transport import SiblingPassAbortedError
from dual_pipeline.transcript_stages_core.call_ledger import D02CallLedger
from dual_pipeline.transcript_stages_core.external_synthesizer import (
    call_gemini_generate_content,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    DEFAULT_PASS_2A_DEADLINE_SEC,
    DEFAULT_PASS_2B_DEADLINE_SEC,
    DEFAULT_PASS_2C_DEADLINE_SEC,
    execute_d02_3pass_dag,
    execute_pass_2a,
    execute_pass_2b,
    execute_pass_2c,
    resolve_pass_deadlines,
)


def _make_gemini_response(text: str, finish_reason: str = "STOP") -> bytes:
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": text}], "role": "model"},
                "finishReason": finish_reason,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 500,
            "candidatesTokenCount": len(text.split()),
            "totalTokenCount": 500 + len(text.split()),
        },
    }
    return json.dumps(payload).encode("utf-8")


class D02RetryConcurrencyAdversarialTests(unittest.TestCase):
    """Verifies Wave 3 retry discipline, deadline sizing, and sibling cancellation."""

    def setUp(self) -> None:
        self.ledger = D02CallLedger()
        self.mock_user_content = "Transcript and meeting discussion content for testing."
        self.mock_system_prompt = "You are synthesizing Artifact 02."

    def test_single_retry_owner_transport_handles_429_pass_fails_fast(self) -> None:
        """Transport owns 429 retries (3 tries). Pass layer does NOT retry on transport failure."""
        call_count = 0

        def fake_urlopen(req, timeout=120.0):
            nonlocal call_count
            call_count += 1
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=429,
                msg="Too Many Requests",
                hdrs={},
                fp=io.BytesIO(b"Rate limit exceeded"),
            )

        with (
            patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake_key"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch("time.sleep", return_value=None),  # Fast forward backoff sleep
        ):
            with self.assertRaises(RuntimeError) as ctx:
                execute_pass_2a(
                    model="gemini-3.8-flash",
                    user_content=self.mock_user_content,
                    min_words=100,
                    max_retries=2,
                    deadline_seconds=30.0,
                    ledger=self.ledger,
                    system_prompt=self.mock_system_prompt,
                )
            self.assertIn("429", str(ctx.exception))
            # Transport retried 3 times on attempt 1. Pass 2A did NOT start attempt 2!
            self.assertEqual(call_count, 3)
            # Verifies worst-case under network error is 3 calls, NOT 3x3=9!
            self.assertEqual(self.ledger.call_count, 3)

    def test_timeout_error_never_retried_at_pass_level(self) -> None:
        """TimeoutError is terminal and never retried by the pass layer."""
        call_count = 0

        def fake_urlopen(req, timeout=120.0):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("Socket timed out")

        with (
            patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake_key"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch("time.sleep", return_value=None),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                execute_pass_2c(
                    model="gemini-3.8-flash",
                    user_content=self.mock_user_content,
                    min_words=100,
                    max_retries=2,
                    deadline_seconds=30.0,
                    ledger=self.ledger,
                    system_prompt=self.mock_system_prompt,
                )
            self.assertIn("connection error", str(ctx.exception))
            # Transport retried 3 times, pass layer made 0 retries
            self.assertEqual(call_count, 3)

    def test_urlopen_timeout_matches_remaining_deadline(self) -> None:
        """urllib.request.urlopen timeout must adhere to min(120, remaining_deadline)."""
        observed_timeouts = []

        def fake_urlopen(req, timeout=120.0):
            observed_timeouts.append(timeout)
            resp = MagicMock()
            resp.read.return_value = _make_gemini_response("Valid output response text")
            resp.__enter__.return_value = resp
            return resp

        with (
            patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake_key"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            call_gemini_generate_content(
                model="gemini-3.8-flash",
                system_prompt="Test prompt",
                user_content=self.mock_user_content,
                deadline_seconds=25.0,  # Short deadline
                pass_id="2B",
                pass_attempt=1,
            )

        self.assertEqual(len(observed_timeouts), 1)
        self.assertLessEqual(observed_timeouts[0], 25.0)
        self.assertGreaterEqual(observed_timeouts[0], 20.0)

    def test_sibling_abort_on_terminal_failure(self) -> None:
        """When one pass fails terminally, abort_event terminates sibling passes quickly."""
        abort_event = threading.Event()
        abort_event.set()

        # 1. Sibling pass raises SiblingPassAbortedError immediately before starting call
        with self.assertRaises(SiblingPassAbortedError):
            execute_pass_2b(
                model="gemini-3.8-flash",
                user_content=self.mock_user_content,
                abort_event=abort_event,
            )

        with self.assertRaises(SiblingPassAbortedError):
            execute_pass_2c(
                model="gemini-3.8-flash",
                user_content=self.mock_user_content,
                abort_event=abort_event,
            )

        # 2. In-flight call raises SiblingPassAbortedError when abort_event is set during execution
        test_abort_event = threading.Event()

        def slow_fake_urlopen(req, timeout=120.0):
            # Hang until abort event is triggered
            for _ in range(50):
                if test_abort_event.is_set():
                    break
                time.sleep(0.05)
            resp = MagicMock()
            resp.read.return_value = _make_gemini_response("Valid text")
            resp.__enter__.return_value = resp
            return resp

        def trigger_abort_later():
            time.sleep(0.1)
            test_abort_event.set()

        t_abort = threading.Thread(target=trigger_abort_later)
        t_abort.start()

        t0 = time.monotonic()
        with (
            patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake_key"),
            patch("urllib.request.urlopen", side_effect=slow_fake_urlopen),
            self.assertRaises(SiblingPassAbortedError),
        ):
            call_gemini_generate_content(
                    model="gemini-3.8-flash",
                    system_prompt="System",
                    user_content=self.mock_user_content,
                    deadline_seconds=30.0,
                    abort_event=test_abort_event,
                )

        elapsed = time.monotonic() - t0
        # Verified observed well within 1 heartbeat (15s) - typically < 1.2s!
        self.assertLess(elapsed, 2.0)
        t_abort.join()

    def test_pass_deadlines_scaled_for_thinking_model(self) -> None:
        """Verify resolve_pass_deadlines scales up for gemini-3 models."""
        std_deadlines = resolve_pass_deadlines("gemini-2.0-flash")
        self.assertEqual(std_deadlines["2A"], DEFAULT_PASS_2A_DEADLINE_SEC)
        self.assertEqual(std_deadlines["2B"], DEFAULT_PASS_2B_DEADLINE_SEC)
        self.assertEqual(std_deadlines["2C"], DEFAULT_PASS_2C_DEADLINE_SEC)

        thinking_deadlines = resolve_pass_deadlines("gemini-3.8-flash")
        self.assertGreater(thinking_deadlines["2A"], DEFAULT_PASS_2A_DEADLINE_SEC)
        self.assertGreater(thinking_deadlines["2B"], DEFAULT_PASS_2B_DEADLINE_SEC)
        self.assertGreater(thinking_deadlines["2C"], DEFAULT_PASS_2C_DEADLINE_SEC)

    def test_concurrency_semaphore_is_process_wide(self) -> None:
        """Verify _GEMINI_CONCURRENCY_SEMAPHORE limits simultaneous in-flight calls."""
        in_flight = 0
        max_seen = 0
        lock = threading.Lock()

        def fake_urlopen(req, timeout=120.0):
            nonlocal in_flight, max_seen
            with lock:
                in_flight += 1
                if in_flight > max_seen:
                    max_seen = in_flight
            time.sleep(0.05)
            with lock:
                in_flight -= 1
            resp = MagicMock()
            resp.read.return_value = _make_gemini_response("Valid text " * 30)
            resp.__enter__.return_value = resp
            return resp

        threads = []
        with (
            patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake_key"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            for _i in range(8):
                t = threading.Thread(
                    target=call_gemini_generate_content,
                    kwargs={
                        "model": "gemini-3.8-flash",
                        "system_prompt": "Prompt",
                        "user_content": "Content",
                        "deadline_seconds": 30.0,
                    },
                )
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

        # Semaphore value is 3, so max concurrent calls must be <= 3
        self.assertLessEqual(max_seen, 3)

    def test_worst_case_call_budget_under_word_floor_retries_le_9(self) -> None:
        """Word floor retries (pass-level) + single 2B attempt results in at most 7 calls (<= 9)."""
        long_2a = "Strategic narrative word " * 1000
        table_2b = "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n| 1 | Test item | Lead | 10/10/2026 | None |"
        long_2c = "# Speaker One - Org, Role\n- **Theme**: Detailed contribution " * 200

        calls_2a = 0
        calls_2c = 0

        def fake_call(model, system_prompt, user_content, json_mode=False, deadline_seconds=120.0, pass_id=None, pass_attempt=1, ledger=None, abort_event=None):
            nonlocal calls_2a, calls_2c
            if pass_id == "2A":
                calls_2a += 1
                # Fail word floor on first 2 attempts, succeed on 3rd
                if calls_2a < 3:
                    return "Too short"
                return long_2a
            elif pass_id == "2B":
                return table_2b
            elif pass_id == "2C":
                calls_2c += 1
                # Fail word floor on first 2 attempts, succeed on 3rd
                if calls_2c < 3:
                    return "Too short"
                return long_2c
            return ""

        with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", side_effect=fake_call):
            res = execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt=self.mock_system_prompt,
                user_content=self.mock_user_content,
                ledger=self.ledger,
            )

        self.assertIn("Action & Governance Ledger", res)
        # 3 calls for 2A + 1 call for 2B + 3 calls for 2C = 7 calls <= 9!
        total_calls = calls_2a + 1 + calls_2c
        self.assertEqual(total_calls, 7)
        self.assertLessEqual(total_calls, 9)


if __name__ == "__main__":
    unittest.main()
