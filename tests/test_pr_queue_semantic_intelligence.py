"""Unit and regression tests for PR Queue Semantic Intelligence (Waves 1-3)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.diff_semantics import (
    classify_branch_diff,
    is_code_file,
    is_deliverable_file,
    is_doc_file,
)
from tools.pr_governance_core.wave_status import compute_wave_columns


class TestWave1DeliverableAndWavePollution(unittest.TestCase):
    def test_no_token_plan_pollution_from_inherited_main_plans(self) -> None:
        """Regression test for Defect 1: feat/live-* must not inherit open waves from fix_live_capture_sck_resilience.md."""
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            docs_plans = wt / "docs" / "plans"
            docs_plans.mkdir(parents=True)
            # Simulate an inherited plan on main containing 'live' in its filename with open waves 1..4
            (docs_plans / "fix_live_capture_sck_resilience.md").write_text(
                "# Plan\n## Wave 1: Open\n## Wave 2: Open\n## Wave 3: Open\n## Wave 4: Open\n",
                encoding="utf-8",
            )
            changed_lens = [
                "output/project_lens/10-16-2026/Ed_Amit/01_Ed_Amit_10-16-2026_1034AM1107AMET_hitl_resolved_transcript.md",
                "output/project_lens/10-16-2026/Ed_Amit/02_Ed_Amit_10-16-2026_1034AM1107AMET_executive_recap.md",
                "output/project_lens/10-16-2026/Ed_Amit/03_Ed_Amit_10-16-2026_1034AM1107AMET_canonical_signal_projection.json",
                "output/project_lens/10-16-2026/Ed_Amit/publication_coverage_receipt.json",
            ]
            open_w, done_w = compute_wave_columns(
                "feat/live-project-lens-10-16-2026-ed-amit",
                lambda _b, _p: "",
                changed_lens,
                wt_path=wt,
            )
            self.assertEqual((open_w, done_w), ("—", "—"))

            open_s, done_s = compute_wave_columns(
                "feat/live-google-partner_advisory-chat",
                lambda _b, _p: "",
                [],
                wt_path=wt,
            )
            self.assertEqual((open_s, done_s), ("—", "—"))

    def test_deliverable_files_not_misclassified_as_docs_only_plan(self) -> None:
        """Deliverables under output/ must classify as DELIVERABLE_PACKAGE_READY, not DOCS_ONLY_ACTIVE/UNSTARTED."""
        changed_lens = [
            "output/project_lens/10-16-2026/Ed_Amit/01_Ed_Amit_10-16-2026_1034AM1107AMET_hitl_resolved_transcript.md",
            "output/project_lens/10-16-2026/Ed_Amit/02_Ed_Amit_10-16-2026_1034AM1107AMET_executive_recap.md",
            "output/project_lens/10-16-2026/Ed_Amit/03_Ed_Amit_10-16-2026_1034AM1107AMET_canonical_signal_projection.json",
            "output/project_lens/10-16-2026/Ed_Amit/publication_coverage_receipt.json",
        ]
        for f in changed_lens:
            self.assertTrue(is_deliverable_file(f))
            self.assertFalse(is_doc_file(f))
            self.assertFalse(is_code_file(f))

        sem = classify_branch_diff(
            changed_lens,
            branch="feat/live-project-lens-10-16-2026-ed-amit",
            open_waves="—",
            completed_waves="—",
        )
        self.assertEqual(sem.category, "DELIVERABLE_PACKAGE_READY")
        self.assertFalse(sem.is_docs_only)
        self.assertFalse(sem.is_unstarted_plan)
        self.assertEqual(sem.code_files_count, 0)
        self.assertEqual(sem.deliverable_files_count, 4)
        self.assertEqual(sem.deliverable_dirs, ["output/project_lens/10-16-2026/Ed_Amit"])
        self.assertTrue(sem.has_deliverable_triad)
        self.assertTrue(sem.has_publication_receipt)


class TestWave2CanonicalMeetingSupersession(unittest.TestCase):
    def test_partner_advisory_chat_meeting_supersession_synthetic(self) -> None:
        """Superseded live-mic preview + PARTIAL_QA_FAILURE attempt must resolve to PRUNE when PACKAGE_READY exists on main."""
        import json
        from tools.pr_governance_core.live_meeting_bridge import (
            _MAIN_DELIVERABLE_CACHE,
            evaluate_worktree_meeting_supersession,
        )
        from tools.pr_topology_core.disposition import _evaluate_ahead_zero_branch
        from tools.pr_topology_core.matrix import BranchDiff

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _MAIN_DELIVERABLE_CACHE.pop(root.resolve(), None)
            # Create canonical PACKAGE_READY deliverable on main
            canon_dir = root / "output" / "partner_alliances" / "10-13-2026" / "PartnerAdvisory_Gcp"
            canon_dir.mkdir(parents=True)
            (canon_dir / "final_release_decision.json").write_text(
                json.dumps({"decision": "PACKAGE_READY", "status": "PASS", "project": "partner_alliances", "date": "10-13-2026"}),
                encoding="utf-8",
            )
            (canon_dir / "metadata.json").write_text(
                json.dumps({"project": "partner_alliances", "date": "10-13-2026", "meeting_name": "Weekly PartnerAdvisory & GCP Alignment", "status": "PASS"}),
                encoding="utf-8",
            )

            # Create worktree with collapsed live preview and PARTIAL_QA_FAILURE
            wt = root / ".worktrees" / "live_google_partner_advisory_chat"
            preview_dir = wt / "output" / "Google_PartnerAdvisory" / "10-13-2026" / "Chat_Amit"
            preview_dir.mkdir(parents=True)
            live_md = preview_dir / "01_10-13-2026_Chat_Amit_live_transcript.md"
            live_md.write_text("# Live Transcript (LIVE_PREVIEW)\n**[13:02:14]** Voice A: Hello\n", encoding="utf-8")

            fail_dir = wt / "output" / "partner_alliances" / "10-13-2026" / "Weekly_PartnerAdvisory_GCP_Alignment_native_trans"
            fail_dir.mkdir(parents=True)
            (fail_dir / "metadata.json").write_text(
                json.dumps({"project": "partner_alliances", "date": "10-13-2026", "meeting_name": "Weekly PartnerAdvisory & GCP Alignment", "status": "PARTIAL_QA_FAILURE"}),
                encoding="utf-8",
            )

            residuals = [
                "output/Google_PartnerAdvisory/10-13-2026/Chat_Amit/01_10-13-2026_Chat_Amit_live_transcript.md",
                "output/partner_alliances/10-13-2026/Weekly_PartnerAdvisory_GCP_Alignment_native_trans/metadata.json",
            ]
            sup = evaluate_worktree_meeting_supersession(wt, residuals, root)
            self.assertIsNotNone(sup)
            assert sup is not None
            self.assertTrue(sup["is_superseded"])
            self.assertEqual(sup["canonical_dir"], "output/partner_alliances/10-13-2026/PartnerAdvisory_Gcp")
            self.assertIn("collapsed live-mic preview", sup["worktree_state"])
            self.assertIn("PARTIAL_QA_FAILURE attempt", sup["worktree_state"])

    def test_uningested_live_recording_not_pruned(self) -> None:
        """An uningested live recording with no matching PACKAGE_READY deliverable on main must never be marked superseded."""
        import json
        from tools.pr_governance_core.live_meeting_bridge import (
            _MAIN_DELIVERABLE_CACHE,
            evaluate_worktree_meeting_supersession,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _MAIN_DELIVERABLE_CACHE.pop(root.resolve(), None)
            canon_dir = root / "output" / "partner_alliances" / "10-13-2026" / "PartnerAdvisory_Gcp"
            canon_dir.mkdir(parents=True)
            (canon_dir / "final_release_decision.json").write_text(
                json.dumps({"decision": "PACKAGE_READY", "status": "PASS", "project": "partner_alliances", "date": "10-13-2026"}),
                encoding="utf-8",
            )

            wt = root / ".worktrees" / "live_new_session"
            rec_dir = wt / "output" / "project_lens" / "10-17-2026" / "New_Meeting"
            rec_dir.mkdir(parents=True)
            (rec_dir / "01_10-17-2026_New_Meeting_live_transcript.md").write_text(
                "# Live Transcript (LIVE_PREVIEW)\n**[10:00:00]** Voice A: Unmerged new meeting\n",
                encoding="utf-8",
            )
            residuals = ["output/project_lens/10-17-2026/New_Meeting/01_10-17-2026_New_Meeting_live_transcript.md"]
            sup = evaluate_worktree_meeting_supersession(wt, residuals, root)
            self.assertIsNone(sup)


class TestWave3ForensicCommitAndActionProvenance(unittest.TestCase):
    def test_case_a_partner_advisory_chat_decommission_rendering(self) -> None:
        """Case A: feat/live-google-partner_advisory-chat renders PR to Main Required = N, Decommission (No Value), and cites canonical main deliverable."""
        from tools.pr_governance_core.council import harden_action_item
        from tools.pr_governance_core.executive_render import (
            evaluate_stage_gates_and_disposition,
        )

        item = {
            "branch": "feat/live-google-partner_advisory-chat",
            "tier": "D.1",
            "ahead": 0,
            "behind": 50,
            "dirty": True,
            "dirty_files_count": 29,
            "clean_merge": True,
            "open_waves": "—",
            "completed_waves": "—",
            "disposition_action": "PRUNE",
            "disposition_reason": "Superseded by PACKAGE_READY deliverable on main under output/partner_alliances/10-13-2026/PartnerAdvisory_Gcp/ (collapsed live-mic preview & PARTIAL_QA_FAILURE attempt)",
            "next_action": "[PRUNE] Superseded by PACKAGE_READY deliverable on main under output/partner_alliances/10-13-2026/PartnerAdvisory_Gcp/ (collapsed live-mic preview & PARTIAL_QA_FAILURE attempt)",
        }
        code_done, rebase_req, pr_req, disp, raw_act = evaluate_stage_gates_and_disposition(item)
        hardened = harden_action_item(item, raw_act, {})
        self.assertEqual(code_done, "N/A")
        self.assertEqual(rebase_req, "N/A")
        self.assertEqual(pr_req, "N")
        self.assertEqual(disp, "Decommission (No Value)")
        self.assertIn("output/partner_alliances/10-13-2026/PartnerAdvisory_Gcp/", hardened)
        self.assertIn("python tools/pr_governance.py finish feat/live-google-partner_advisory-chat --force", hardened)

    def test_case_b_ed_amit_lens_deliverable_forensic_action(self) -> None:
        """Case B: feat/live-project-lens-10-16-2026-ed-amit renders Code Completed = Y, Rebase Required = Y, PR to Main = Y, and cites commit/base SHAs + deliverable path."""
        from tools.pr_governance_core.council import harden_action_item
        from tools.pr_governance_core.executive_render import (
            evaluate_stage_gates_and_disposition,
        )

        item = {
            "branch": "feat/live-project-lens-10-16-2026-ed-amit",
            "tier": "P1.1",
            "ahead": 1,
            "behind": 2,
            "dirty": False,
            "dirty_files_count": 0,
            "clean_merge": True,
            "open_waves": "—",
            "completed_waves": "—",
            "tip_sha": "9a6600430",
            "merge_base_sha": "9c230353f",
            "main_head_sha": "a77af4244",
            "last_commit_subject": "feat(ingest): finalize 10/16/2026 Ed/Amit LENS deliverables",
            "diff_category": "DELIVERABLE_PACKAGE_READY",
            "code_files_count": 0,
            "deliverable_files_count": 44,
            "deliverable_dirs": ["output/project_lens/10-16-2026/Ed_Amit"],
            "has_deliverable_triad": True,
            "has_publication_receipt": True,
        }
        code_done, rebase_req, pr_req, disp, raw_act = evaluate_stage_gates_and_disposition(item)
        hardened = harden_action_item(item, raw_act, {})
        self.assertEqual(code_done, "Y")
        self.assertEqual(rebase_req, "Y")
        self.assertEqual(pr_req, "Y")
        self.assertEqual(disp, "Promote to Main")
        self.assertIn("9a6600430", hardened)
        self.assertIn("9c230353f", hardened)
        self.assertIn("a77af4244", hardened)
        self.assertIn("output/project_lens/10-16-2026/Ed_Amit/", hardened)
        self.assertIn("publication_coverage_receipt.json", hardened)
        self.assertIn("python tools/pr_governance.py rebase feat/live-project-lens-10-16-2026-ed-amit", hardened)
        self.assertIn("python tools/pr_governance.py merge feat/live-project-lens-10-16-2026-ed-amit", hardened)
        self.assertIn("python tools/pr_governance.py finish feat/live-project-lens-10-16-2026-ed-amit --force", hardened)
        self.assertNotIn("Class B pytest", hardened)


if __name__ == "__main__":
    unittest.main()


