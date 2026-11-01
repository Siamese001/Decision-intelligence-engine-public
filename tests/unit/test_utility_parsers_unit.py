#!/usr/bin/env python3
"""Unit test suite for Core Utility Parsers, Date Normalizers, and AST Linting Primitives.

Fulfills 2026+ Agentic Verification Standards (Dimension 1):
1. Pure boundary isolation: 100% provider-free, zero-network, zero-disk I/O.
2. Exhaustive edge-case testing for normalize_iso_date (ISO, US, dash/slash variants, nulls, corrupt tokens).
3. Deterministic RFC-8259 canonical JSON serialization and cryptographic digest hashing (digest, stable_id, trace_id).
4. Safe mutable path containment and escape prevention (assert_safe_mutable_path).
5. Model-neutral naming pattern verification (scan_path_for_model_tokens, exemption rules).
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import pytest

    unit_marker = pytest.mark.unit
except ImportError:  # pragma: no cover

    def unit_marker(cls_or_fn):
        return cls_or_fn


from dual_pipeline.core import (
    artifact_digest,
    assert_safe_mutable_path,
    canonical_bytes,
    canonical_json,
    digest,
    normalize_iso_date,
    seal_artifact,
    span_id,
    stable_id,
    trace_id,
    utc_now,
)
from tools.lint_model_neutral_naming import (
    is_path_exempt,
    scan_path_for_model_tokens,
)


@unit_marker
class UtilityParsersUnitTests(unittest.TestCase):
    """Pure isolated unit tests for core date parsing, serialization, and naming verification."""

    # --- 1. Date Normalization & Boundary Safety ---

    def test_normalize_iso_date_standard_formats(self) -> None:
        """Verify standard date formats map cleanly to YYYY-MM-DD."""
        self.assertEqual(normalize_iso_date("2026-09-14"), "2026-09-14")
        self.assertEqual(normalize_iso_date("09/14/2026"), "2026-09-14")
        self.assertEqual(normalize_iso_date("10-02-2026"), "2026-10-02")
        self.assertEqual(normalize_iso_date("2026/09/14"), "2026-09-14")
        # Single digit month/day zero padding
        self.assertEqual(normalize_iso_date("9/4/2026"), "2026-09-04")
        self.assertEqual(normalize_iso_date("9-4-2026"), "2026-09-04")

    def test_normalize_iso_date_iso_timestamps(self) -> None:
        """ISO timestamp strings should cleanly truncate to the 10-char date component."""
        self.assertEqual(normalize_iso_date("2026-09-14T10:23:09Z"), "2026-09-14")
        self.assertEqual(normalize_iso_date("2026-09-14T15:30:00.123456+00:00"), "2026-09-14")

    def test_normalize_iso_date_null_and_malformed_inputs(self) -> None:
        """Null, empty, whitespace, and corrupt strings fail safely to None."""
        self.assertIsNone(normalize_iso_date(None))
        self.assertIsNone(normalize_iso_date(""))
        self.assertIsNone(normalize_iso_date("   "))
        self.assertIsNone(normalize_iso_date("null"))
        self.assertIsNone(normalize_iso_date("NULL"))
        self.assertIsNone(normalize_iso_date("None"))
        self.assertIsNone(normalize_iso_date("not_a_date"))
        self.assertIsNone(normalize_iso_date("invalid_text"))
        self.assertIsNone(normalize_iso_date("abcdef"))
        self.assertIsNone(normalize_iso_date("12345"))
        self.assertIsNone(normalize_iso_date(12345))  # type: ignore[arg-type]
        self.assertIsNone(normalize_iso_date(["2026-09-14"]))  # type: ignore[arg-type]

    # --- 2. RFC-8259 Canonical JSON & Cryptographic Hashes ---

    def test_canonical_json_sorting_and_compact_separators(self) -> None:
        """RFC-8259 canonical JSON sorts keys and uses compact separators with no whitespace."""
        payload = {"b": 2, "a": 1, "z": [3, 2, 1], "nested": {"k2": "v2", "k1": "v1"}}
        res = canonical_json(payload)
        expected = '{"a":1,"b":2,"nested":{"k1":"v1","k2":"v2"},"z":[3,2,1]}'
        self.assertEqual(res, expected)
        self.assertEqual(canonical_bytes(payload), expected.encode("utf-8"))

    def test_digest_stability_and_sha256_length(self) -> None:
        """Digest of identical objects must be byte-for-byte identical across runs."""
        obj1 = {"key": "value", "count": 42}
        obj2 = {"count": 42, "key": "value"}
        dig1 = digest(obj1)
        dig2 = digest(obj2)
        self.assertEqual(dig1, dig2)
        self.assertEqual(len(dig1), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in dig1))

    def test_stable_id_formatting(self) -> None:
        """Stable IDs must clean prefixes and append deterministic hash suffixes."""
        sid1 = stable_id("task:inspection", "module_1", 100, length=16)
        sid2 = stable_id("task:inspection", "module_1", 100, length=16)
        self.assertEqual(sid1, sid2)
        self.assertTrue(sid1.startswith("task:inspection-"))
        self.assertEqual(len(sid1.split("-")[-1]), 16)

    def test_trace_and_span_id_lengths(self) -> None:
        """trace_id must be 32 hex chars and span_id must be 16 hex chars."""
        tid = trace_id("flow-001", 123)
        sid = span_id("subflow-001", 456)
        self.assertEqual(len(tid), 32)
        self.assertEqual(len(sid), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in tid))
        self.assertTrue(all(c in "0123456789abcdef" for c in sid))

    def test_seal_artifact_and_digest_cycle(self) -> None:
        """Sealing an artifact must inject a valid canonical_digest without altering payload."""
        raw_artifact = {"event": "intake_admitted", "package_id": "pkg-001"}
        raw_hash = artifact_digest(raw_artifact)

        sealed = seal_artifact(raw_artifact)
        self.assertIn("canonical_digest", sealed)
        self.assertEqual(sealed["canonical_digest"]["algorithm"], "sha256")
        self.assertEqual(sealed["canonical_digest"]["value"], raw_hash)

        # Calculating artifact_digest on already sealed payload must match original hash
        self.assertEqual(artifact_digest(sealed), raw_hash)

    def test_utc_now_iso_format(self) -> None:
        """utc_now must return a valid ISO-8601 string ending in 'Z'."""
        now_str = utc_now()
        self.assertTrue(now_str.endswith("Z"))
        self.assertIn("T", now_str)
        self.assertEqual(len(now_str), 20)  # e.g. '2026-09-14T10:23:09Z'

    # --- 3. Path Security & Escape Prevention ---

    def test_assert_safe_mutable_path_valid_subpath(self) -> None:
        """Valid subpaths inside root must resolve successfully."""
        root = Path("/tmp/sandbox_root").resolve()
        target = root / "nested" / "file.json"
        resolved = assert_safe_mutable_path(root, target)
        self.assertEqual(resolved, target)

    def test_assert_safe_mutable_path_rejects_parent_escape(self) -> None:
        """Paths that escape root via '..' must be rejected with RuntimeError."""
        root = Path("/tmp/sandbox_root").resolve()
        escape_target = root / ".." / "outside.txt"
        with self.assertRaises(RuntimeError) as ctx:
            assert_safe_mutable_path(root, escape_target)
        self.assertIn("outside its runtime root", str(ctx.exception))

    # --- 4. Model-Neutral Naming Linter Primitives ---

    def test_scan_path_detects_prohibited_model_identifiers(self) -> None:
        """Filenames and path components containing model identifiers must be flagged."""
        violating_paths = [
            "reports/gpt4o_audit_findings.json",
            "evals/luna_high_reasoning_receipt.md",
            "documentation/astra_architecture_review.pdf",
            "specs/sol_medium_refactor_notes.md",
            "reports/claude_3_5_sonnet_analysis.json",
            "metrics/gemini_pro_eval_results.json",
            "benchmarks/llama_3_latency.log",
        ]
        for path in violating_paths:
            violations = scan_path_for_model_tokens(path)
            self.assertGreater(len(violations), 0, f"Expected violation for path: {path}")

    def test_scan_path_allows_clean_domain_names(self) -> None:
        """Model-neutral domain filenames must pass with zero violations."""
        clean_paths = [
            "reports/wave1_test_architecture_receipt.json",
            "evals/decision_intelligence_accuracy.json",
            "documentation/DECISION_INTELLIGENCE_SPEC.md",
            "dual_pipeline/query_core/engine.py",
            "tests/unit/test_multi_agent_budget_escalation.py",
            "output/project_nexus/08-28-2026/02_executive_recap.md",
        ]
        for path in clean_paths:
            violations = scan_path_for_model_tokens(path)
            self.assertEqual(violations, [], f"Expected clean path, got: {violations}")

    def test_is_path_exempt_rules(self) -> None:
        """Grandfathered or internal system directories must be exempt from model naming lints."""
        self.assertTrue(is_path_exempt(".git/config"))
        self.assertTrue(is_path_exempt(".venv/lib/python"))
        self.assertTrue(is_path_exempt("archive/tests/legacy_runners/release_gate.py"))
        self.assertTrue(is_path_exempt(".agents/rules/agent-routing.md"))
        self.assertFalse(is_path_exempt("reports/new_report.md"))


if __name__ == "__main__":
    unittest.main()
