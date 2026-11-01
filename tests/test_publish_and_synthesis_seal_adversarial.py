"""Wave 3 Adversarial & Edge-Case Tests: Synthesis Unsealed File Laundering & Publication Short-Circuit.

Covers mandatory edge-case categories:
1. Invariant bypass attempt (unsealed >1000 byte 01_* file in output_dir without stage_seals.json or manifest)
2. Malformed input / Tampered digest (stage_seals.json with mismatched SHA-256 or FAIL status)
3. Rollback & recovery (idempotent ACTIVATED receipt paired with FAILED fact coverage receipt)
4. Exact production regression (in-place _re_seal_active_pair_if_needed on sealed generation database)
"""
from __future__ import annotations

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


class TestPublishAndSynthesisSealAdversarial(unittest.TestCase):
    """Adversarial tests targeting Wave 3 synthesis checkpoint laundering and publish short-circuits."""

    def test_adversarial_unsealed_or_tampered_01_file_never_laundered_on_resume(self) -> None:
        """Category: Invariant bypass & Tampered digest — >1000 byte unsealed or SHA-tampered 01_* file must re-run L2."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            out_dir = Path(tmp) / "unsealed_out"
            out_dir.mkdir()
            unsealed_text = "# Forensic Transcript\n\n" + ("**[00:01:00] Speaker:** Unsealed text.\n" * 45)
            self.assertGreater(len(unsealed_text), 1000)
            cand_file = out_dir / "01_unsealed_hitl_resolved_transcript.md"
            cand_file.write_text(unsealed_text, encoding="utf-8")

            mock_l2_f = MagicMock()
            mock_l2_f.execute.return_value = "# Forensic Transcript\n\n**Date:** 10/28/2026\n\n**[00:01:00] Speaker:** Fresh L2.\n"
            strategy = FullMeetingSynthesisStrategy(provider=MagicMock(), l2_forensic=mock_l2_f)

            with (
                patch("dual_pipeline.transcript_stages.L1ForensicCognition") as mock_l1,
                patch("dual_pipeline.transcript_qa_core.healing_mesh.DeterministicHealer.heal_output_1", side_effect=lambda t, metadata=None: (t, [])),
            ):
                mock_l1.return_value.plan.return_value = {}
                # 1. No stage_seals.json, no final_release_decision.json, no .manifest.json
                res1 = strategy._resolve_output_1(
                    source_text="raw source",
                    metadata={"output_dir": str(out_dir), "resume": True},
                    attempt=1,
                    remediation_directive=None,
                    progress_notify=None,
                    ckpt_dir=None,
                    pa_context={"output_dir": str(out_dir)},
                )
                self.assertIn("Fresh L2", res1)
                self.assertEqual(mock_l2_f.execute.call_count, 1)

                # 2. Tampered SHA-256 in stage_seals.json even when final_release_decision.json says output_1_pass=True
                (out_dir / "stage_seals.json").write_text(
                    json.dumps({"output_1": {"status": "PASS", "sha256": "0" * 64}}),
                    encoding="utf-8",
                )
                (out_dir / "final_release_decision.json").write_text(
                    json.dumps({"output_1_pass": True}),
                    encoding="utf-8",
                )
                res2 = strategy._resolve_output_1(
                    source_text="raw source",
                    metadata={"output_dir": str(out_dir), "resume": True},
                    attempt=1,
                    remediation_directive=None,
                    progress_notify=None,
                    ckpt_dir=None,
                    pa_context={"output_dir": str(out_dir)},
                )
                self.assertIn("Fresh L2", res2)
                self.assertEqual(mock_l2_f.execute.call_count, 2)

    def test_adversarial_publish_blocks_idempotent_short_circuit_on_failed_coverage(self) -> None:
        """Category: Rollback & recovery — pre-existing ACTIVATED receipt with FAILED fact coverage fails closed."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            out_p = Path(tmp) / "pub_fail"
            out_p.mkdir()
            rt_root = Path(tmp) / "rt"
            rt_root.mkdir()
            (out_p / "publication_fact_coverage_receipt.json").write_text(
                json.dumps({"status": "FAILED", "fact_coverage_ratio": 0.0}),
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
                with self.assertRaises(RuntimeError) as ctx:
                    _validate_auth_and_runtime(
                        out_p=out_p,
                        meta={"meeting_name": "Test Sync", "runtime_root": str(rt_root)},
                        written={"03_graph": "graph.json"},
                        release_runner=fake_runner,
                    )
                self.assertIn("PUBLICATION_COVERAGE_UNVERIFIED", str(ctx.exception))

    def test_adversarial_reseal_active_pair_blocks_sealed_generation_db(self) -> None:
        """Category: Exact production regression — _re_seal_active_pair_if_needed refuses sealed generation paths."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            rt_root = Path(tmp)
            gen_dir = rt_root / "generations" / "gen-sealed-001"
            gen_dir.mkdir(parents=True)
            sealed_db = gen_dir / "graph.sqlite"
            sealed_db.write_bytes(b"SQLite format 3\x00")
            (gen_dir / "pair_manifest.json").write_text(
                json.dumps({"generation_id": "gen-sealed-001"}), encoding="utf-8"
            )
            with self.assertRaises(RuntimeError) as ctx:
                _re_seal_active_pair_if_needed(rt_root, sealed_db)
            self.assertIn("SEALED_GENERATION_RESEAL_FORBIDDEN", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
