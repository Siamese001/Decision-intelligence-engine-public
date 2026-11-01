#!/usr/bin/env python3
"""Unit tests for agent trajectory telemetry recording and privacy invariants."""
from __future__ import annotations

import json
import unittest

from dual_pipeline.telemetry_core.agent_trajectory import (
    AgentTrajectoryTelemetry,
    TelemetryPrivacyViolation,
    scrub_sensitive_string,
    validate_and_sanitize_metadata,
)


class AgentTrajectoryTelemetryUnitTests(unittest.TestCase):
    def test_session_initialization(self) -> None:
        telemetry = AgentTrajectoryTelemetry(
            agent_session_id="session-12345",
            agent_role="architect",
        )
        self.assertEqual(telemetry.agent_session_id, "session-12345")
        self.assertEqual(telemetry.agent_role, "architect")
        self.assertEqual(telemetry.convergence_status, "IN_PROGRESS")
        self.assertEqual(telemetry.step_count, 0)
        self.assertEqual(telemetry.tool_errors, 0)
        self.assertEqual(telemetry.edit_churn_count, 0)
        self.assertEqual(telemetry.edit_flail_index, 0.0)

    def test_step_recording_and_metric_aggregation(self) -> None:
        telemetry = AgentTrajectoryTelemetry("session-steps")
        telemetry.record_step("tool_search", latency_ms=120.5, status="SUCCESS")
        telemetry.record_step("tool_view", latency_ms=80.2, status="SUCCESS")
        telemetry.record_step("tool_exec", latency_ms=200.0, status="ERROR", error_type="RuntimeError")

        self.assertEqual(telemetry.step_count, 3)
        self.assertEqual(telemetry.tool_errors, 1)
        self.assertAlmostEqual(telemetry.total_latency_ms, 400.7, places=1)
        self.assertAlmostEqual(telemetry.avg_latency_ms, 133.57, places=1)
        self.assertAlmostEqual(telemetry.tool_error_rate, 0.3333, places=3)

    def test_edit_recording_and_flail_index(self) -> None:
        telemetry = AgentTrajectoryTelemetry("session-flail")

        # Smooth progression across 4 distinct files
        telemetry.record_edit("src/a.py", lines_added=10, lines_deleted=2)
        telemetry.record_edit("src/b.py", lines_added=5, lines_deleted=0)
        telemetry.record_edit("src/c.py", lines_added=20, lines_deleted=5)
        telemetry.record_edit("src/d.py", lines_added=15, lines_deleted=1)

        self.assertEqual(telemetry.edit_churn_count, 4)
        self.assertEqual(telemetry.total_lines_added, 50)
        self.assertEqual(telemetry.total_lines_deleted, 8)
        self.assertEqual(telemetry.edit_flail_index, 0.0)

        # Thrashing edits on the same file repeatedly
        telemetry_thrash = AgentTrajectoryTelemetry("session-thrash")
        for _ in range(10):
            telemetry_thrash.record_edit("src/problem_file.py", lines_added=2, lines_deleted=2)

        self.assertEqual(telemetry_thrash.edit_churn_count, 10)
        # (10 edits - 1 unique file) / 10 = 0.90
        self.assertEqual(telemetry_thrash.edit_flail_index, 0.90)

    def test_convergence_lifecycle(self) -> None:
        telemetry = AgentTrajectoryTelemetry("session-conv")
        self.assertEqual(telemetry.convergence_status, "IN_PROGRESS")
        self.assertIsNone(telemetry.ended_at_utc)

        telemetry.mark_convergence("CONVERGED")
        self.assertEqual(telemetry.convergence_status, "CONVERGED")
        self.assertIsNotNone(telemetry.ended_at_utc)

        with self.assertRaises(ValueError):
            telemetry.mark_convergence("UNKNOWN_STATUS")

    def test_telemetry_privacy_invariant_blocks_prohibited_keys(self) -> None:
        telemetry = AgentTrajectoryTelemetry("session-privacy", strict_privacy=True)

        with self.assertRaises(TelemetryPrivacyViolation):
            telemetry.set_metadata({"prompt": "Tell me the plan"})

        with self.assertRaises(TelemetryPrivacyViolation):
            telemetry.set_metadata({"raw_transcript": "Spoken dialogue here"})

        with self.assertRaises(TelemetryPrivacyViolation):
            telemetry.set_metadata({"api_key": "secret123"})

    def test_scrub_sensitive_strings_and_non_strict_sanitization(self) -> None:
        raw_text = "Connecting with key sk_test_redacted_placeholder to endpoint"
        scrubbed = scrub_sensitive_string(raw_text)
        self.assertNotIn("sk_test_redacted_placeholder", scrubbed)
        self.assertIn("[REDACTED_SECRET]", scrubbed)

        data = {
            "prompt": "Sensitive text",
            "safe_metric": 42,
            "secret_token": "token sk_test_redacted_placeholder",
        }
        sanitized = validate_and_sanitize_metadata(data, strict=False)
        self.assertNotIn("prompt", sanitized)
        self.assertEqual(sanitized["safe_metric"], 42)
        self.assertIn("[REDACTED_SECRET]", sanitized["secret_token"])

    def test_rfc8259_canonical_json_and_digest(self) -> None:
        telemetry = AgentTrajectoryTelemetry("session-rfc")
        telemetry.record_step("tool_one", 10.0)
        telemetry.set_metadata({"component": "telemetry_test"})
        telemetry.mark_convergence("CONVERGED")

        canonical_json = telemetry.to_rfc8259_canonical_json()
        parsed = json.loads(canonical_json)
        self.assertEqual(parsed["agent_session_id"], "session-rfc")
        self.assertEqual(parsed["convergence_status"], "CONVERGED")

        digest = telemetry.canonical_digest()
        self.assertEqual(len(digest), 64)
        # Verify idempotency
        self.assertEqual(digest, telemetry.canonical_digest())


if __name__ == "__main__":
    unittest.main()
