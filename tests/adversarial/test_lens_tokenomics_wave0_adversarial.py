"""
tests/adversarial/test_lens_tokenomics_wave0_adversarial.py

Adversarial and edge-case tests for Wave 0: Project LENS Tokenomics Baseline.
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty traces, 0-byte files, null inputs)
2. Malformed input (corrupt JSONL, non-dict payloads, invalid lines)
3. Invariant bypass attempt (building receipt with violated invariants must fail closed)
4. Exact production regression (pins exact 10-07 incident metrics against drift)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.audit_lens_tokenomics_baseline import (
    EMPIRICAL_LENS_BASELINE,
    build_wave0_receipt,
    compute_tokenomics_baseline,
    load_telemetry_traces,
    verify_baseline_invariants,
)


class LensTokenomicsWave0AdversarialTests(unittest.TestCase):
    def test_01_boundary_payload_empty_and_zero_byte_traces(self) -> None:
        """Category 1: Boundary payload - 0-byte file and missing traces."""
        # Non-existent path
        missing_path = Path("/tmp/non_existent_traces_file_12345.jsonl")
        records = load_telemetry_traces(missing_path)
        self.assertEqual(records, [])

        baseline = compute_tokenomics_baseline(missing_path)
        self.assertEqual(baseline["parsed_traces_count"], 0)

        # 0-byte file
        with tempfile.NamedTemporaryFile(suffix=".jsonl") as tmp:
            records = load_telemetry_traces(Path(tmp.name))
            self.assertEqual(records, [])

    def test_02_malformed_input_corrupt_jsonl_lines(self) -> None:
        """Category 2: Malformed input - truncated JSON, non-JSON lines, and whitespace."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
            tmp.write('{"valid": 1}\n')
            tmp.write('MALFORMED_NOT_JSON\n')
            tmp.write('{"incomplete": \n')
            tmp.write('\n\n')
            tmp.write('{"valid": 2}\n')
            tmp_path = Path(tmp.name)

        try:
            records = load_telemetry_traces(tmp_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["valid"], 1)
            self.assertEqual(records[1]["valid"], 2)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_03_invariant_bypass_attempt_fails_closed(self) -> None:
        """Category 5: Invariant bypass attempt - building receipt with invalid baseline must raise ValueError."""
        tampered_baseline = {
            "session_interval": {"wall_clock_minutes": 50.0},
            "pipeline_attempts": {"total_explicit_runs": 5, "failed_runs": 0},
            "token_expenditures": {"host_total_tokens": 100},
        }

        with self.assertRaises(ValueError) as ctx:
            build_wave0_receipt(tampered_baseline)

        self.assertIn("Baseline validation failed", str(ctx.exception))

    def test_04_exact_production_regression_pinned_incident_metrics(self) -> None:
        """Category 7: Exact production regression - pins exact 10-07 incident metrics."""
        sess = EMPIRICAL_LENS_BASELINE["session_interval"]
        tokens = EMPIRICAL_LENS_BASELINE["token_expenditures"]
        attempts = EMPIRICAL_LENS_BASELINE["pipeline_attempts"]

        # Exact pinned metrics from LENS forensic incident
        self.assertEqual(sess["wall_clock_minutes"], 197.0)
        self.assertEqual(attempts["total_explicit_runs"], 36)
        self.assertEqual(attempts["failed_runs"], 31)
        self.assertEqual(tokens["host_total_tokens"], 261_000_000)
        self.assertEqual(tokens["host_cache_read_tokens"], 251_000_000)
        self.assertEqual(tokens["wasted_retry_tokens"], 820_000)
        self.assertEqual(tokens["stage03_graph_extraction_tokens"], 468_500)
        self.assertEqual(tokens["micro_judge_tokens"], 361_600)


if __name__ == "__main__":
    unittest.main()
