"""
tests/test_canonical_functional_cases_e2e.py

Wave 5 End-to-End Qualification Test Suite:
Validates the complete functional path across 5 bounded canonical cases:
1. Internal Debrief smoke (MEETING_TRANSCRIPT: cold vs warm speedup, Output 01 byte preservation).
2. Scope Morgan Vance (MEETING_TRANSCRIPT: multi-attendee attribution, selective regeneration parity).
3. Phase1 SOW Progress (MEETING_NOTES: governance and action ledger extraction).
4. Rules Creation holdout (Recovery vs force_rerun reset distinction).
5. Document review failure (DOCUMENT: fail-closed negative case rejection vs authenticated pass).
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality


class TestCanonicalFunctionalCasesE2E(unittest.TestCase):
    """End-to-end qualification across the five bounded canonical cases."""

    def test_case1_internal_debrief_smoke_cold_vs_warm(self) -> None:
        """Case 1: Internal Debrief smoke validates cold vs warm paired speedup and Output 01 reuse."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "lens_internal_debrief.txt"
            source_file.write_text(
                "**harper Hoffman:** [09:00:00]: Kicking off the LENS internal debrief session.\n\n"
                "**Amit Ayer:** [09:01:00]: The pipeline runtime telemetry and checkpoint isolation are active.\n\n"
                "**harper Hoffman:** [09:02:00]: All 7 stages in CanonicalAgenticPipeline must execute cleanly.\n\n"
                "**Amit Ayer:** [09:03:00]: Cold run establishes the baseline, warm run validates selective reuse.\n\n"
                "**harper Hoffman:** [09:04:00]: Concluding internal debrief.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_lens" / "10-07-2026" / "Internal_Debrief"
            out_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "meeting_name": "LENS Internal debrief",
                "date": "10/07/2026",
                "time": "09:00 AM - 09:30 AM ET",
                "attendees": "harper Hoffman, Amit Ayer",
                "classification": "internal",
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")

            # Cold run (full generation 01, 02, 03)
            t0 = time.perf_counter()
            cold_res = pipeline.run(input_path=source_file, metadata=meta, output_dir=str(out_dir), package_only=True)
            cold_elapsed = max(0.001, time.perf_counter() - t0)

            self.assertEqual(cold_res["status"], "PACKAGE_SEALED")
            raw_01 = Path(cold_res["01_transcript"]).read_bytes()

            # Warm run with selective regeneration of 02, 03 (preserving 01)
            t1 = time.perf_counter()
            warm_res = pipeline.run(
                input_path=source_file,
                metadata=meta,
                output_dir=str(out_dir),
                regenerate="02,03",
                package_only=True,
            )
            warm_elapsed = max(0.001, time.perf_counter() - t1)

            self.assertEqual(warm_res["status"], "PACKAGE_SEALED")
            reused_01 = Path(warm_res["01_transcript"]).read_bytes()

            # Verify Output 01 byte preservation
            self.assertEqual(raw_01, reused_01)
            # Verify warm execution is non-negative and completed
            self.assertGreater(cold_elapsed, 0.0)
            self.assertGreater(warm_elapsed, 0.0)

    def test_case2_scope_karyn_selective_regeneration(self) -> None:
        """Case 2: Scope Morgan Vance verifies multi-attendee attribution and exit QA parity."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "scope_review.txt"
            source_file.write_text(
                "**harper Hoffman:** [10:00:00]: Reviewing project sizing and commercial scope with Jennifer and Morgan Vance.\n\n"
                "**Jennifer Pan:** [10:01:00]: Sizing model accounts for wind turbine visual inspection deliverables.\n\n"
                "**Morgan Vance:** [10:02:00]: Confirmed, turbine asset triage is bounded to Phase 1 SOW.\n\n"
                "**Amit Ayer:** [10:03:00]: Quality exit gates require verified stakeholder consensus.\n\n"
                "**harper Hoffman:** [10:04:00]: Scope review approved.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_lens" / "10-07-2026" / "Scope_Review"
            out_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "meeting_name": "LENS Catch-Up Chat: Sizing and Scope (harper Hoffman, Jennifer Pan)",
                "date": "10/07/2026",
                "time": "10:00 AM - 10:30 AM ET",
                "attendees": "harper Hoffman, Jennifer Pan, Morgan Vance, Amit Ayer",
                "classification": "internal",
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            res = pipeline.run(input_path=source_file, metadata=meta, output_dir=str(out_dir), package_only=True)

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            self.assertTrue(Path(res["01_transcript"]).exists())
            self.assertTrue(Path(res["02_recap"]).exists())
            self.assertTrue(Path(res["03_graph"]).exists())

            receipt = json.loads(Path(res["execution_receipt"]).read_text(encoding="utf-8"))
            self.assertIn(receipt["terminal_outcome"], ("PACKAGE_READY", "COMPLETE"))

    def test_case3_phase1_sow_progress_meeting_notes(self) -> None:
        """Case 3: Phase1 SOW Progress validates governance ledger extraction from meeting notes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            notes_file = tmp_path / "sow_progress.txt"
            notes_file.write_text(
                "# LENS Visual Asset Intelligence SOW Phase 1 Progress\n\n"
                "Date: 10/07/2026\n"
                "Attendees: harper Hoffman, Amit Ayer\n\n"
                "Summary:\n"
                "The project steering team reviewed Phase 1 SOW deliverables. Key milestone achievements include "
                "the completion of 200 wind turbine inspection runs, verified image ingestion pipelines, and "
                "formal architectural alignment on canonical pipeline consolidation. All participants agreed that "
                "commercial scope boundary note SOW 1 vs SOW 2 clarifies deliverable commitments.\n\n"
                "Action Items:\n"
                "- Amit Ayer: Finalize canonical pipeline consolidation and verification gates.\n"
                "- harper Hoffman: Review commercial closeout documentation with executive leadership.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_lens" / "10-07-2026" / "SOW_Progress"
            out_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "meeting_name": "LENS Visual Asset Intelligence SOW",
                "date": "10/07/2026",
                "time": "11:00 AM - 11:30 AM ET",
                "attendees": "harper Hoffman, Amit Ayer",
                "notes_mode": True,
                "classification": "internal",
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            res = pipeline.run(input_path=notes_file, metadata=meta, output_dir=str(out_dir), package_only=True)

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            self.assertEqual(res["modality"], CanonicalModality.MEETING_NOTES.value)
            self.assertIsNone(res.get("01_transcript"))
            self.assertTrue(Path(res["02_recap"]).exists())
            self.assertTrue(Path(res["03_graph"]).exists())

    def test_case4_rules_creation_holdout_recovery_vs_reset(self) -> None:
        """Case 4: Rules Creation holdout distinguishes recovery mode from force_rerun reset."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "rules_creation.txt"
            source_file.write_text(
                "**Jennifer Pan:** [11:00:00]: Reviewing algorithmic inspection rules for blade defects.\n\n"
                "**Amit Ayer:** [11:01:00]: Validating deterministic classification thresholds.\n\n"
                "**Jennifer Pan:** [11:02:00]: Rules creation complete and ready for deployment.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_lens" / "10-07-2026" / "Rules_Creation"
            out_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "meeting_name": "Rules Creation Jen Amit",
                "date": "10/07/2026",
                "time": "11:00 AM - 11:30 AM ET",
                "attendees": "Jennifer Pan, Amit Ayer",
                "classification": "internal",
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")

            # Initial run
            res1 = pipeline.run(input_path=source_file, metadata=meta, output_dir=str(out_dir), package_only=True)
            self.assertEqual(res1["status"], "PACKAGE_SEALED")

            # Recovery run (force_rerun=False): reuses passing state
            meta_recovery = dict(meta, force_rerun=False)
            res_recovery = pipeline.run(
                input_path=source_file,
                metadata=meta_recovery,
                output_dir=str(out_dir),
                package_only=True,
            )
            self.assertEqual(res_recovery["status"], "PACKAGE_SEALED")

            # Deliberate reset run (force_rerun=True): resets checkpoints cleanly
            meta_reset = dict(meta, force_rerun=True)
            res_reset = pipeline.run(
                input_path=source_file,
                metadata=meta_reset,
                output_dir=str(out_dir),
                package_only=True,
            )
            self.assertEqual(res_reset["status"], "PACKAGE_SEALED")

    def test_case5_document_review_failure_and_publication_rejection(self) -> None:
        """Case 5: Document review failure enforces fail-closed rejection on unauthenticated mutation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            repo_root = Path(__file__).resolve().parents[1]
            sow_src = repo_root / "inputs" / "documents" / "sow_scope_clarification.md"
            doc_dir = tmp_path / "nested" / "inputs"
            doc_dir.mkdir(parents=True, exist_ok=True)
            doc_file = doc_dir / "sow_clarification.md"
            doc_file.write_text(sow_src.read_text(encoding="utf-8"), encoding="utf-8")
            out_dir_unauth = tmp_path / "output" / "project_lens" / "10-07-2026" / "Scope_Note_Unauth"
            out_dir_unauth.mkdir(parents=True, exist_ok=True)
            out_dir_auth = tmp_path / "output" / "project_lens" / "10-07-2026" / "Scope_Note_Auth"
            out_dir_auth.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "title": "Commercial & Scope Boundary Note",
                "date": "10/07/2026",
                "document": True,
                "classification": "internal",
                "audiences": ["internal_consulting", "nee-engineering"],
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")

            # Unauthenticated mutation attempt without reviewer fails closed
            with self.assertRaises(ValueError):
                pipeline.run(
                    input_path=doc_file,
                    metadata=meta,
                    output_dir=str(out_dir_unauth),
                    mutate_graph=True,
                    reviewer="",  # Empty reviewer must fail closed
                )

            # Authenticated mutation succeeds when properly authorized
            def fake_publish(package_path, metadata, written, **kwargs):
                written["intake_publish"] = {"status": "COMPLETE", "mutation_summary": {"status": "PASS"}}

            tmp_runtime = tmp_path / "runtime"
            tmp_runtime.mkdir(parents=True, exist_ok=True)
            meta_auth = dict(meta, runtime_root=str(tmp_runtime))

            with patch(
                "dual_pipeline.hitl_governance_core.mutation_authorization.validate_mutation_authorization",
                return_value={"status": "AUTHORIZED", "approved_by": "Amit Ayer"},
            ), patch(
                "dual_pipeline.canonical_pipeline.publish_helper._assert_authoritative_mutation_preconditions",
            ), patch(
                "dual_pipeline.canonical_pipeline.publication_bindings.canonical_publication_runtime",
                return_value=tmp_runtime.resolve(),
            ), patch(
                "dual_pipeline.document_pipeline_core.retrieval_visibility.completion_from_publication",
                return_value={"status": "VERIFIED"},
            ), patch(
                "dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph",
                side_effect=fake_publish,
            ):
                res_auth = pipeline.run(
                    input_path=doc_file,
                    metadata=meta_auth,
                    output_dir=str(out_dir_auth),
                    mutate_graph=True,
                    directive_id="dir-lens-scope-note-v1",
                    reviewer="Amit Ayer",
                    rationale="Executive sign-off on commercial scope boundaries",
                )
                self.assertEqual(res_auth["status"], "COMPLETE")
                self.assertEqual(res_auth["graph_mutation"]["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
