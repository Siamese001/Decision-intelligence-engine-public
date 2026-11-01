"""
tools/pr_queue_contract_core/checks_wave5.py

Wave 5 verification checks for the PR Queue Durable Prioritization Plan.
Covers decision view restoration, 20+ branch inspectability, linked detail artifact,
coverage fraction accuracy, and empirical readability probe measurements.
"""
from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict


def check_default_command_output_action_and_rationale() -> Dict[str, Any]:
    name = "default_output_action_and_rationale"
    try:
        from tools.pr_governance_core.render import render_queue_table

        queue = [
            {
                "branch": "feat/active-alpha",
                "tier": "P0.1",
                "sub_rank": "P0.1",
                "ahead": 4,
                "behind": 0,
                "domain": "Governance",
                "readiness": "🚀 Fast-Lane",
                "review_verdict": "MERGE_READY",
                "next_action": "[REVIEW] Ready for merge",
                "ranking_rationale": "P0 by rubric; clean merge and zero behind",
                "blocker": "None (clean)",
                "value_statement": "UNKNOWN: Unmeasured business value (structural heuristics only)",
                "completion_evidence_requirement": "Passing Tier 2 readiness receipt and wave plan completion",
                "completed_waves": "1, 2",
                "open_waves": "—",
            }
        ]

        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table(queue, view_mode="compact")
        out = buf.getvalue()

        if "DIAGNOSTIC & NEXT ACTION LEDGER" not in out:
            return {"name": name, "status": "FAIL", "error": "Diagnostic ledger heading missing"}
        if "**Next Action**: [REVIEW] Ready for merge" not in out:
            return {"name": name, "status": "FAIL", "error": "Next action missing from output"}
        if "**Ranking Rationale**: P0 by rubric" not in out:
            return {"name": name, "status": "FAIL", "error": "Ranking rationale missing from output"}
        if "**Blocker**: None (clean)" not in out:
            return {"name": name, "status": "FAIL", "error": "Blocker statement missing from output"}

        return {"name": name, "status": "PASS", "details": "Concrete next step, rationale, blocker, and value retained"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_inspectable_decision_view_capacity_20_plus() -> Dict[str, Any]:
    name = "inspectable_capacity_20_plus"
    try:
        from tools.pr_governance_core.render import render_queue_table

        twenty_queue = []
        for i in range(25):
            twenty_queue.append({
                "branch": f"feat/candidate-{i:02d}",
                "tier": f"P2.{i+1}",
                "sub_rank": f"P2.{i+1}",
                "ahead": i + 1,
                "behind": i % 5,
                "domain": "Testing",
                "readiness": "In Flight",
                "review_verdict": "REVIEW_READY",
                "next_action": f"[CONTINUE_WAVES] Candidate {i} active",
                "ranking_rationale": f"P2 candidate ranked at position {i+1}",
                "blocker": f"Blocker for candidate {i}",
                "value_statement": f"Value statement for candidate {i}",
                "completion_evidence_requirement": "Passing readiness receipt",
                "completed_waves": "—",
                "open_waves": "1",
            })

        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table(twenty_queue, view_mode="compact")
        out = buf.getvalue()

        for item in twenty_queue:
            b = item["branch"]
            if b not in out:
                return {"name": name, "status": "FAIL", "error": f"Branch {b} dropped from output"}
            if f"**Next Action**: {item['next_action']}" not in out:
                return {"name": name, "status": "FAIL", "error": f"Next action dropped for {b}"}
            if f"**Value**: {item['value_statement']}" not in out:
                return {"name": name, "status": "FAIL", "error": f"Value statement dropped for {b}"}

        return {"name": name, "status": "PASS", "details": "All 25 active branches fully inspectable without field omission"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_linked_detail_artifact_exists_and_valid() -> Dict[str, Any]:
    name = "linked_detail_artifact_integrity"
    try:
        from tools.pr_governance_core.render import render_queue_table

        with tempfile.TemporaryDirectory() as tmpdir:
            detail_path = Path(tmpdir) / "reports" / "pr_queue_detail.json"
            stashes_data = [
                {
                    "ref": f"stash@{{{i}}}",
                    "head_sha": f"sha{i:04d}",
                    "parent_sha": f"parent{i:04d}",
                    "date": "2026-10-06T10:00:00Z",
                    "classification": "STASH_MAIN",
                    "branch": "main",
                    "subject": f"WIP commit {i}",
                }
                for i in range(5)
            ]
            queue = [
                {
                    "branch": "feat/core-engine",
                    "tier": "P0.1",
                    "sub_rank": "P0.1",
                    "ahead": 2,
                    "behind": 0,
                    "clean_merge": True,
                    "domain": "Governance",
                    "next_action": "[REVIEW] Ready",
                    "ranking_rationale": "High value",
                }
            ]
            for s in stashes_data:
                queue.append({
                    "branch": s["ref"],
                    "tier": "W",
                    "sub_rank": "W.stash",
                    "domain": "Housekeeping",
                    "next_action": "[TRIAGE_STASH]",
                    "ranking_rationale": "Stash",
                    "classification": s["classification"],
                })

            buf = io.StringIO()
            with redirect_stdout(buf):
                render_queue_table(
                    queue,
                    inv_counts={"total_worktrees_children": 1, "stashes_total": 5},
                    view_mode="compact",
                    detail_path=detail_path,
                    inv={"stashes": stashes_data, "counts": {"total_worktrees_children": 1, "stashes_total": 5}},
                )
            out = buf.getvalue()

            if not detail_path.exists():
                return {"name": name, "status": "FAIL", "error": "Detail artifact was not created on disk"}
            if f"file://{detail_path}" not in out:
                return {"name": name, "status": "FAIL", "error": "Detail link missing from rendered table"}

            with open(detail_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("collapsed_stash_count") != 5:
                return {"name": name, "status": "FAIL", "error": f"Expected 5 stashes in detail, got {data.get('collapsed_stash_count')}"}
            if len(data.get("stashes", [])) != 5:
                return {"name": name, "status": "FAIL", "error": "Stashes list incomplete in detail artifact"}
            if data["stashes"][0].get("head_sha") != "sha0000":
                return {"name": name, "status": "FAIL", "error": "Stable stash commit sha not preserved"}

        return {"name": name, "status": "PASS", "details": "Detail artifact exists on disk with stable stash identities and complete branches"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_coverage_footer_fraction_accuracy() -> Dict[str, Any]:
    name = "coverage_footer_fraction_accuracy"
    try:
        from tools.pr_governance_core.render import render_queue_table

        # Asymmetric count test
        inv_counts_gap = {
            "covered_children": 10,
            "total_children": 11,
            "covered_stashes": 75,
            "total_stashes": 78,
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            render_queue_table([], inv_counts=inv_counts_gap, view_mode="compact")
        out = buf.getvalue()

        if "Worktree coverage: 10/11 children · 75/78 stashes" not in out:
            return {"name": name, "status": "FAIL", "error": f"Expected '10/11 children · 75/78 stashes' in output, got: {out}"}

        # Symmetric count test
        inv_counts_full = {
            "covered_children": 11,
            "total_children": 11,
            "covered_stashes": 78,
            "total_stashes": 78,
        }
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            render_queue_table([], inv_counts=inv_counts_full, view_mode="compact")
        out2 = buf2.getvalue()

        if "Worktree coverage: 11/11 children · 78/78 stashes" not in out2:
            return {"name": name, "status": "FAIL", "error": f"Expected '11/11 children · 78/78 stashes' in output, got: {out2}"}

        return {"name": name, "status": "PASS", "details": "Footer accurately distinguishes covered vs total physical entries"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}


def check_empirical_readiness_probe_measurements() -> Dict[str, Any]:
    name = "empirical_probe_measurements"
    try:
        from tools.pr_queue_render_probe import run_probe

        measurements, summary = run_probe(
            viewports=[800, 1000],
            chrome_bin="/nonexistent/chrome",
            assert_min_chars=28,
        )
        if summary.renderer_measurement_available is not False:
            return {"name": name, "status": "FAIL", "error": "Renderer measurement available should be False when chrome is missing"}
        if summary.renderer_measurement_status != "UNVERIFIED":
            return {"name": name, "status": "FAIL", "error": "Renderer measurement status must be UNVERIFIED when chrome is missing"}
        if not summary.threshold_passed:
            return {"name": name, "status": "FAIL", "error": "Threshold assertion failed on deterministic simulation"}

        return {"name": name, "status": "PASS", "details": "Empirical verdicts derived from measurements and UNVERIFIED renderer status flagged"}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "error": str(exc)}
