"""tests/test_verdict_verifier_e2e.py

End-to-end and adversarial tests for Rigorous Per-Verdict File-Diff Verification
and HITL Disposition Escalation (Wave 1 & Wave 2).

Covers:
1. All 5 canonical value dispositions:
   - Promote to Main
   - Complete In-Flight Work
   - Salvage Subset & Archive
   - Hold / Blocked
   - Decommission (No Value)
2. All 7 ReviewVerdict values:
   - MERGE_READY, REVIEW_READY, RESOLVE_CONFLICTS, REBASE_RETARGET,
     GOVERNANCE_BLOCK, SPLIT_REQUIRED, SUPERSEDED_TOMBSTONE
3. Untracked worktree file hunk extraction (`extract_untracked_worktree_hunks`)
   and dirty worktree file absorption (`compute_dirty_file_in_main_pct`).
4. Queue reconciliation (`reconcile_review`) preserving `SUPERSEDED_TOMBSTONE`
   and partial hunk absorption (`REVIEW_READY`) when `ahead == 0` and `dirty == True`.
5. HITL clarification gate (`needs_operator_decision(c_top, c_second)`) and
   zero-jargon `question_validator.py` compliance on every generated question payload.
6. Mandatory edge-case categories:
   - Boundary payload (empty item, zero hunks, 0-byte untracked files)
   - Malformed input (None/missing fields, non-dict signals)
   - Invariant bypass attempt (dirty worktree with unabsorbed files attempting PRUNE)
   - Exact production regression (`feat/wave-plan-word-limit-debate` partial absorption)
"""
from __future__ import annotations

from pathlib import Path
import pytest

from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload
from tools.pr_governance_core.executive_render import evaluate_stage_gates_and_disposition
from tools.pr_governance_core.queue_reconcile import reconcile_review, tier_from_disposition
from tools.pr_governance_core.verdict_verifier import (
    CANONICAL_DISPOSITIONS,
    collect_dirty_worktree_files,
    extract_untracked_worktree_hunks,
    needs_operator_decision,
    verify_branch_verdict_and_disposition,
)


class TestVerdictVerifierDispositions:
    """Verifies deterministic file-diff proof across all 5 canonical value dispositions."""

    def test_promote_to_main_high_confidence(self) -> None:
        item = {
            "branch": "feat/serialized-pr-submission-lease",
            "ahead": 1,
            "behind": 4,
            "dirty": False,
            "clean_merge": True,
            "open_waves": "—",
            "review_verdict": "MERGE_READY",
            "diff_category": "CODE_ACTIVE",
            "absorbed_files": [],
            "salvageable_files": ["tools/pr_governance_core/submission_lease.py", "tests/test_submission_lease.py"],
            "signals": {"file_hunks": [{"file_path": "tools/pr_governance_core/submission_lease.py"}], "hunk_absorption_ratio": 0.0},
        }
        res = verify_branch_verdict_and_disposition(item)
        assert res.verified_disposition == "Promote to Main"
        assert res.c_top >= 0.90
        assert res.margin_delta >= 0.70
        assert res.requires_hitl_clarification is False
        assert res.hitl_question_payload is None

    def test_promote_to_main_with_conflicts_high_confidence(self) -> None:
        item = {
            "branch": "feat/rca-apex_trans-ingestion-runtime",
            "ahead": 1,
            "behind": 8,
            "dirty": False,
            "clean_merge": False,
            "open_waves": "—",
            "review_verdict": "RESOLVE_CONFLICTS",
            "diff_category": "CODE_ACTIVE",
            "absorbed_files": [],
            "salvageable_files": ["dual_pipeline/canonical_pipeline/u0_ingress.py"],
            "signals": {"file_hunks": [{"file_path": "dual_pipeline/canonical_pipeline/u0_ingress.py"}], "hunk_absorption_ratio": 0.05},
        }
        res = verify_branch_verdict_and_disposition(item)
        assert res.verified_disposition == "Promote to Main"
        assert res.c_top >= 0.85
        assert res.requires_hitl_clarification is False

    def test_complete_in_flight_work_dirty_unabsorbed(self) -> None:
        item = {
            "branch": "feat/active-authoring-branch",
            "ahead": 0,
            "behind": 2,
            "dirty": True,
            "clean_merge": True,
            "open_waves": "Wave 1",
            "review_verdict": "—",
            "diff_category": "CODE_ACTIVE",
            "absorbed_files": [],
            "salvageable_files": ["tools/new_module.py", "tests/test_new_module.py"],
            "signals": {"file_hunks": [{"file_path": "tools/new_module.py"}], "hunk_absorption_ratio": 0.0},
        }
        res = verify_branch_verdict_and_disposition(item)
        assert res.verified_disposition == "Complete In-Flight Work"
        assert res.c_top >= 0.90
        assert res.requires_hitl_clarification is False

    def test_hold_blocked_on_governance_or_split_or_retarget(self) -> None:
        for blocking_verdict in ("GOVERNANCE_BLOCK", "SPLIT_REQUIRED", "REBASE_RETARGET"):
            item = {
                "branch": f"feat/blocked-{blocking_verdict.lower()}",
                "ahead": 2,
                "behind": 1,
                "dirty": False,
                "clean_merge": True,
                "open_waves": "—",
                "review_verdict": blocking_verdict,
                "diff_category": "CODE_ACTIVE",
                "absorbed_files": [],
                "salvageable_files": ["tools/some_file.py"],
                "signals": {"file_hunks": [{"file_path": "tools/some_file.py"}], "hunk_absorption_ratio": 0.0},
            }
            res = verify_branch_verdict_and_disposition(item)
            assert res.verified_disposition == "Hold / Blocked"
            assert res.c_top >= 0.90
            assert res.requires_hitl_clarification is False

    def test_decommission_no_value_on_zero_commit_and_superseded(self) -> None:
        # 1. Clean ahead==0 branch
        zero_item = {
            "branch": "feat/judge-gold-set-calibration",
            "ahead": 0,
            "behind": 45,
            "dirty": False,
            "clean_merge": True,
            "open_waves": "—",
            "review_verdict": "—",
            "diff_category": "EMPTY_STUB",
            "absorbed_files": [],
            "salvageable_files": [],
            "signals": {"file_hunks": [], "hunk_absorption_ratio": 0.0},
        }
        res_zero = verify_branch_verdict_and_disposition(zero_item)
        assert res_zero.verified_disposition == "Decommission (No Value)"
        assert res_zero.c_top >= 0.95
        assert res_zero.requires_hitl_clarification is False

        # 2. 100% absorbed code branch
        sup_item = {
            "branch": "feat/fully-absorbed-feature",
            "ahead": 1,
            "behind": 12,
            "dirty": False,
            "clean_merge": True,
            "open_waves": "—",
            "review_verdict": "SUPERSEDED_TOMBSTONE",
            "diff_category": "SUPERSEDED_CODE",
            "absorbed_files": ["tools/absorbed.py"],
            "salvageable_files": [],
            "signals": {"file_hunks": [{"file_path": "tools/absorbed.py"}], "hunk_absorption_ratio": 0.96},
        }
        res_sup = verify_branch_verdict_and_disposition(sup_item)
        assert res_sup.verified_disposition == "Decommission (No Value)"
        assert res_sup.verified_review_verdict == "SUPERSEDED_TOMBSTONE"
        assert res_sup.requires_hitl_clarification is False


class TestExactProductionRegressionAndHITLGate:
    """Pins the exact production regression on feat/wave-plan-word-limit-debate (partial absorption)."""

    def test_wave_plan_word_limit_debate_partial_absorption_triggers_hitl(self) -> None:
        item = {
            "branch": "feat/wave-plan-word-limit-debate",
            "ahead": 0,
            "behind": 40,
            "dirty": True,
            "dirty_files_count": 8,
            "clean_merge": True,
            "open_waves": "—",
            "review_verdict": "REVIEW_READY",
            "diff_category": "CODE_ACTIVE",
            "absorbed_files": [
                "tools/implementation_plan_core/evidence_requirements.py",
                "docs/plans/feat_wave_plan_word_limit_debate.md",
                "reports/wave1_word_limit_calibration_receipt.json",
                "reports/wave2_plan_validator_word_scaling_receipt.json",
            ],
            "salvageable_files": [
                ".agents/hooks.json",
                ".agents/rules/wave-progression.md",
                "governance/policy_registry.yaml",
                "tests/test_wave_plan_word_scaling.py",
            ],
            "signals": {
                "file_hunks": [
                    {"file_path": "tools/implementation_plan_core/evidence_requirements.py"},
                    {"file_path": "tests/test_wave_plan_word_scaling.py"},
                ],
                "hunk_absorption_ratio": 0.42,
            },
        }
        res = verify_branch_verdict_and_disposition(item)
        assert res.verified_disposition == "Salvage Subset & Archive"
        assert res.verified_review_verdict == "REVIEW_READY"
        assert res.requires_hitl_clarification is True
        assert res.margin_delta <= 0.20
        assert res.hitl_question_payload is not None

        # Verify generated HITL question payload passes strict question_validator.py (zero-jargon, length, confidence)
        val = validate_question_payload(
            res.hitl_question_payload["question"],
            res.hitl_question_payload["options"],
        )
        assert val.is_valid, f"HITL question payload failed validation: {val.errors}"

        # Verify executive renderer surfaces Salvage Subset & Archive + [HITL_CLARIFICATION_REQUIRED]
        item["requires_hitl_clarification"] = res.requires_hitl_clarification
        code_done, rebase_req, pr_req, disp, action = evaluate_stage_gates_and_disposition(item)
        assert disp == "Salvage Subset & Archive"
        assert pr_req == "Y (Partial)"
        assert "[HITL_CLARIFICATION_REQUIRED]" in action

    def test_partial_hunk_ratio_single_file_triggers_hitl(self) -> None:
        item = {
            "branch": "feat/partial-single-file-work",
            "ahead": 1,
            "behind": 10,
            "dirty": False,
            "clean_merge": True,
            "open_waves": "—",
            "review_verdict": "REVIEW_READY",
            "diff_category": "CODE_ACTIVE",
            "absorbed_files": [],
            "salvageable_files": ["tools/partial_module.py"],
            "signals": {
                "file_hunks": [{"file_path": "tools/partial_module.py"}],
                "hunk_absorption_ratio": 0.50,
            },
        }
        res = verify_branch_verdict_and_disposition(item)
        assert res.verified_disposition == "Salvage Subset & Archive"
        assert res.requires_hitl_clarification is True
        assert res.hitl_question_payload is not None
        val = validate_question_payload(
            res.hitl_question_payload["question"],
            res.hitl_question_payload["options"],
        )
        assert val.is_valid, f"HITL question payload failed validation: {val.errors}"

    def test_cross_slug_meeting_deliverable_superseded_tombstone(self, tmp_path: Path) -> None:
        """Verify cross-slug PACKAGE_READY meeting deliverable on main resolves to Decommission (No Value) / SUPERSEDED_TOMBSTONE."""
        import json

        canon_dir = tmp_path / "output" / "project_jarvis" / "10-23-2026" / "Day_Life_Interview_1_Dustin_PartnerAdvisory"
        canon_dir.mkdir(parents=True, exist_ok=True)
        (canon_dir / "final_release_decision.json").write_text(json.dumps({
            "terminal_outcome": "PASS",
            "date": "10-23-2026",
            "project": "nea",
            "meeting_name": "JARVIS Day in the Life Interview 1",
        }))
        (canon_dir / "metadata.json").write_text(json.dumps({
            "date": "10/23/2026",
            "project": "nea",
            "meeting_name": "JARVIS Day in the Life Interview 1",
        }))

        plan_rel = "output/project_jarvis/10-23-2026/Jarvis_Day_Life_Interview_1_Traci/00_Jarvis_Day_Life_Interview_1_Traci_ingestion_plan.json"
        item = {
            "branch": "ingest/doc-project-jarvis-10-23-2026-day-life-interview-1-traci",
            "ahead": 0,
            "behind": 121,
            "dirty": True,
            "clean_merge": True,
            "open_waves": "—",
            "review_verdict": "—",
            "absorbed_files": [],
            "salvageable_files": [plan_rel],
            "in_main_pcts": {plan_rel: 0.0},
            "signals": {"file_hunks": [{"file_path": plan_rel}], "hunk_absorption_ratio": 0.0},
        }
        res = verify_branch_verdict_and_disposition(item, root=tmp_path)
        assert res.verified_disposition == "Decommission (No Value)"
        assert res.verified_review_verdict == "SUPERSEDED_TOMBSTONE"
        assert res.absorbed_files == [plan_rel]
        assert res.unabsorbed_files == []
        assert res.requires_hitl_clarification is False


class TestAdversarialAndEdgeCases:
    """Covers boundary payloads, malformed inputs, and invariant bypass attempts."""

    def test_boundary_empty_and_malformed_inputs(self) -> None:
        # Empty dictionary
        res_empty = verify_branch_verdict_and_disposition({})
        assert res_empty.verified_disposition in CANONICAL_DISPOSITIONS
        assert res_empty.c_top >= 0.80

        # Malformed None fields
        res_malformed = verify_branch_verdict_and_disposition({
            "branch": None,
            "ahead": None,
            "behind": None,
            "dirty": None,
            "code_review": "invalid_not_a_dict",
            "signals": None,
        })
        assert res_malformed.verified_disposition == "Decommission (No Value)"

    def test_reconcile_review_preserves_dirty_ahead_zero_verdicts(self) -> None:
        # 1. Dirty ahead==0 with >=85% hunk absorption preserves SUPERSEDED_TOMBSTONE
        rec_tomb = reconcile_review(
            "SUPERSEDED_TOMBSTONE",
            ahead=0,
            dirty=True,
            signals={"hunk_absorption_ratio": 0.90, "file_hunks": [{"file_path": "a.py"}]},
        )
        assert rec_tomb.verdict == "SUPERSEDED_TOMBSTONE"
        assert rec_tomb.tombstone_evidence is True

        # 2. Dirty ahead==0 with partial hunk absorption (42%) returns REVIEW_READY instead of '—'
        rec_part = reconcile_review(
            "REVIEW_READY",
            ahead=0,
            dirty=True,
            signals={"hunk_absorption_ratio": 0.42, "file_hunks": [{"file_path": "a.py"}]},
        )
        assert rec_part.verdict == "REVIEW_READY"
        assert "partial hunk absorption" in rec_part.note

        # 3. Clean ahead==0 with no hunks returns '—'
        rec_clean = reconcile_review("MERGE_READY", ahead=0, dirty=False, signals={})
        assert rec_clean.verdict == "—"

    def test_invariant_bypass_dirty_ahead_zero_never_pruned_when_unabsorbed(self) -> None:
        # Attempt to pass [PRUNE] on a branch that actually has partial absorption
        item = {
            "branch": "feat/attempt-prune-bypass",
            "ahead": 0,
            "behind": 30,
            "dirty": True,
            "absorbed_files": ["tools/a.py"],
            "salvageable_files": ["tests/test_unique.py"],
            "next_action": "[PRUNE] synthetic bypass attempt",
            "signals": {"hunk_absorption_ratio": 0.40, "file_hunks": [{"file_path": "tests/test_unique.py"}]},
        }
        res = verify_branch_verdict_and_disposition(item)
        assert res.verified_disposition == "Salvage Subset & Archive"
        assert res.requires_hitl_clarification is True
        assert tier_from_disposition("D.1", "SALVAGE_DELIVERABLES", domain_fallback="P2.1") == "P2.1"

    def test_untracked_worktree_hunk_extraction_on_nonexistent_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        assert extract_untracked_worktree_hunks(missing) == []
        assert collect_dirty_worktree_files(missing, set()) == set()

    def test_needs_operator_decision_margin_boundary(self) -> None:
        assert needs_operator_decision(0.58, 0.42) is True   # delta = 0.16 <= 0.20
        assert needs_operator_decision(0.60, 0.40) is True   # delta = 0.20 <= 0.20
        assert needs_operator_decision(0.92, 0.08) is False  # delta = 0.84 > 0.20

    def test_deleted_dirty_worktree_file_present_on_main_is_absorbed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify a missing/deleted file in a dirty worktree that already exists on main reports 1.0 absorption."""
        from tools.pr_governance_core import verdict_verifier

        wt_dir = tmp_path / "wt"
        wt_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(verdict_verifier, "run_git", lambda args, cwd=None, check=False: "blob\n" if args[:3] == ["cat-file", "-t", "main:output/project_atlas/06-30-2026/Rewire_Approval_Hannah_Hitchcock/source_manifest.json"] else "")

        pct, numstat = verdict_verifier.compute_dirty_file_in_main_pct(
            "output/project_atlas/06-30-2026/Rewire_Approval_Hannah_Hitchcock/source_manifest.json",
            wt_dir,
            "main",
            tmp_path,
        )
        assert pct == 1.0
        assert numstat == (0, 0)

    def test_output03_document_container_sealed_on_main_syncs_item_lists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify Output-03 document containers on main are recognized as sealed and synchronize item absorbed/salvageable lists."""
        from tools.pr_governance_core import container_triage

        container = "output/project_atlas/06-30-2026/Rewire_Approval_Hannah_Hitchcock"
        manifest_rel = f"{container}/source_manifest.json"
        monkeypatch.setattr(
            container_triage,
            "run_git",
            lambda args, cwd=None, check=False: (
                f"{container}/03_Rewire_Approval_Hannah_Hitchcock_06-30-2026_1120AM_canonical_signal_projection.json\n"
                f"{container}/final_release_decision.json\n"
                f"{manifest_rel}\n"
            ),
        )
        assert container_triage.is_meeting_container_sealed_on_main(container, tmp_path) is True

        item = {
            "branch": "test_hotspot_agent",
            "tier": "P3.1",
            "ahead": 0,
            "behind": 6,
            "dirty": True,
            "clean_merge": True,
            "open_waves": "—",
            "review_verdict": "—",
            "absorbed_files": ["output/bmo/10-12-2026/James_Beebe_Business_Management_Chad/source_manifest.json"],
            "salvageable_files": [manifest_rel],
            "in_main_pcts": {manifest_rel: 0.0},
            "signals": {"file_hunks": [], "hunk_absorption_ratio": 0.0},
        }
        res = verify_branch_verdict_and_disposition(item, root=tmp_path)
        assert res.verified_disposition == "Decommission (No Value)"
        assert res.requires_hitl_clarification is False
        assert item["salvageable_files"] == []
        assert manifest_rel in item["absorbed_files"]
        assert item["tier"] == "D.1"

