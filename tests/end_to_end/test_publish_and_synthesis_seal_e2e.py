"""Wave 3 E2E Tests: Publish Helper Activation Invariants & Synthesis Checkpoint Verification."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.publish_helper import (
    _re_seal_active_pair_if_needed,
    _validate_auth_and_runtime,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
)
from dual_pipeline.runtime.pipeline_context import (
    compute_stage_manifest,
    save_stage_checkpoint,
)


class TestPublishAndSynthesisSealE2E(unittest.TestCase):
    """End-to-end verification of Wave 3 synthesis checkpoint seal and publication coverage checks."""

    def test_e2e_synthesis_resumes_only_verified_sealed_deliverable_01(self) -> None:
        """Verifies FullMeetingSynthesisStrategy reuses 01_* from output_dir when SHA-256 seal matches."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            out_dir = Path(tmp) / "run_out"
            out_dir.mkdir()
            valid_content = "# Forensic Transcript\n\n**Date:** 10/28/2026\n\n" + ("**[00:01:00] Speaker A:** Valid turn.\n" * 40)
            cand_file = out_dir / "01_test_hitl_resolved_transcript.md"
            cand_file.write_text(valid_content, encoding="utf-8")
            sha = hashlib.sha256(valid_content.encode("utf-8")).hexdigest()
            (out_dir / "stage_seals.json").write_text(
                json.dumps({"output_1": {"status": "PASS", "sha256": sha}}),
                encoding="utf-8",
            )

            mock_l2_f = MagicMock()
            strategy = FullMeetingSynthesisStrategy(provider=MagicMock(), l2_forensic=mock_l2_f)
            meta = {"output_dir": str(out_dir), "resume": True}
            res = strategy._resolve_output_1(
                source_text="raw source",
                metadata=meta,
                attempt=1,
                remediation_directive=None,
                progress_notify=None,
                ckpt_dir=None,
                pa_context={"output_dir": str(out_dir)},
            )
            self.assertEqual(res, valid_content)
            mock_l2_f.execute.assert_not_called()

    def test_e2e_publish_helper_allows_verified_idempotent_recovery(self) -> None:
        """Verifies _validate_auth_and_runtime permits idempotent recovery when fact coverage receipt is VERIFIED."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            out_p = Path(tmp) / "pub_out"
            out_p.mkdir()
            rt_root = Path(tmp) / "rt"
            rt_root.mkdir()
            (out_p / "publication_fact_coverage_receipt.json").write_text(
                json.dumps({"status": "VERIFIED", "fact_coverage_ratio": 1.0}),
                encoding="utf-8",
            )
            fake_runner = MagicMock()
            fake_runner.ROOT = rt_root
            with (
                patch(
                    "dual_pipeline.canonical_pipeline.publication_bindings.canonical_publication_runtime",
                    return_value=rt_root,
                ),
                patch(
                    "dual_pipeline.hitl_governance_core.mutation_authorization.validate_mutation_authorization",
                    return_value={"idempotent": True, "status": "ACTIVATED"},
                ),
            ):
                _, _, short_circuit = _validate_auth_and_runtime(
                    out_p=out_p,
                    meta={"meeting_name": "Test Sync", "runtime_root": str(rt_root)},
                    written={"03_graph": "graph.json"},
                    release_runner=fake_runner,
                )
                self.assertIsNotNone(short_circuit)
                self.assertEqual(short_circuit["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
