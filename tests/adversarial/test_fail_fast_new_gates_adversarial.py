"""
tests/adversarial/test_fail_fast_new_gates_adversarial.py

Adversarial test suite covering Wave 7 fail-fast gates:
- Audio duration coverage reconciliation gate
- Token context window preflight validation gate
- Document pipeline file size and extension rejection
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.audio_duration_gate import reconcile_transcript_coverage
from dual_pipeline.document_pipeline_core.content_loader import (
    _dispatch_file_loader,
    load_document_content,
)
from dual_pipeline.document_pipeline_core.metadata_helper import scaffold_document_metadata
from dual_pipeline.l2_providers_core.types import L2ProviderError
from dual_pipeline.token_preflight_gate import validate_context_window


class TestFailFastNewGatesAdversarial(unittest.TestCase):
    def test_reconcile_transcript_coverage_success(self) -> None:
        segments = [{"start": 0.0, "end": 92.0, "text": "speech"}]
        # 92% coverage >= 90% threshold -> should not raise
        reconcile_transcript_coverage(100.0, segments, min_coverage=0.90)

    def test_reconcile_transcript_coverage_zero_expected_duration(self) -> None:
        # Zero or negative expected duration -> no-op
        reconcile_transcript_coverage(0.0, [])
        reconcile_transcript_coverage(-10.0, [])

    def test_reconcile_transcript_coverage_empty_segments_raises(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            reconcile_transcript_coverage(120.0, [], min_coverage=0.90)
        self.assertIn("Transcript is empty", str(ctx.exception))

    def test_reconcile_transcript_coverage_insufficient_coverage_raises(self) -> None:
        segments = [{"start": 10.0, "end": 70.0, "text": "speech"}]  # 60s covered out of 100s = 60%
        with self.assertRaises(RuntimeError) as ctx:
            reconcile_transcript_coverage(100.0, segments, min_coverage=0.90)
        self.assertIn("Insufficient transcript coverage", str(ctx.exception))

    def test_validate_context_window_success(self) -> None:
        policy = MagicMock()
        policy.context_window_tokens = 10000
        # 3500 chars ~= 1000 tokens <= 10000
        validate_context_window({"request_input_chars": 3500}, policy)

    def test_validate_context_window_exceeded_raises(self) -> None:
        policy = MagicMock()
        policy.context_window_tokens = 1000
        # 35000 chars ~= 10000 tokens > 1000 limit
        with self.assertRaises(L2ProviderError) as ctx:
            validate_context_window({"request_input_chars": 35000}, policy)
        self.assertIn("BUDGET_EXHAUSTED", str(ctx.exception))

    def test_validate_context_window_no_limit_passes(self) -> None:
        policy = MagicMock()
        policy.context_window_tokens = None
        validate_context_window({"request_input_chars": 50000}, policy)

    def test_document_loader_unknown_extension_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _dispatch_file_loader(".unsupported", Path("test.unsupported"), b"content")
        self.assertIn("Unknown document extension", str(ctx.exception))

    def test_document_loader_exceeds_max_bytes_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f_path = Path(f.name)
        try:
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value.st_size = 55 * 1024 * 1024
                with self.assertRaises(ValueError) as ctx:
                    load_document_content(f_path)
                self.assertIn("exceeds 52428800 bytes limit", str(ctx.exception))
        finally:
            f_path.unlink(missing_ok=True)

    def test_document_metadata_helper_missing_author_or_title_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"Sample document text\n")
            f_path = Path(f.name)
        try:
            # Missing author should default to Corporate Communications
            meta = scaffold_document_metadata(
                input_path=f_path,
                title="Some Title",
                author=None,
                date_str="09/30/2026",
                project="project_nexus",
            )
            self.assertEqual(meta["author"], "Corporate Communications")
        finally:
            f_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
