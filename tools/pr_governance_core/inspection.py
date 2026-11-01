"""
Branch inspection, listing, and doctor health audit for PR governance.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from tools.pr_priority_engine import derive_priority, load_rubric
from tools.pr_priority_facts import fetch_worktree_dirty_numstat

from .common import (
    REQUIRED_HOOKS,
    ROOT,
    calculate_readiness_and_risk,
    check_merge_conflict,
    compute_first_parent_sync,
    resolve_python,
    run_git,
    slugify_branch,
)
from .code_review_rules import ReviewVerdict
from .diff_semantics import classify_branch_diff
from .domain import classify_domain
from .queue_actions import (
    build_siblings_map,
    explain_next_action,
    explain_worktree_action,
    optional_conflict_triage,
)
from .queue_order import assign_sub_ranks, sort_queue
from .queue_reconcile import (
    compute_risk_tier,
    reconcile_review,
    tier_from_disposition,
    tombstone_next_action,
)
from .worktree import list_active_worktrees
from .worktree_inventory import build_worktree_inventory


def is_remote_tracking_branch(branch: str) -> bool:
    """Determines whether a branch ref represents a remote tracking branch."""
    return (
        branch.startswith("origin/")
        or branch.startswith("remotes/")
        or branch.startswith("upstream/")
    )


def get_feature_branches(
    include_remote: bool = False,
    fetch_first: bool = False,
    repo_root: Optional[Path] = None,
) -> List[str]:
    root = repo_root or ROOT
    if fetch_first:
        try:
            run_git(["fetch", "--prune", "origin"], check=False, cwd=root, timeout=30.0)
        except Exception as e:
            print(f"⚠️ Warning: git fetch --prune origin failed or timed out: {e}", file=sys.stderr)

    out = run_git(["branch", "--format=%(refname:short)"], check=True, cwd=root)
    branches = []
    for line in out.splitlines():
        b = line.strip()
        if b and b != "main" and not b.startswith("backup-"):
            branches.append(b)

    if include_remote:
        try:
            remote_out = run_git(
                ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/"],
                check=False,
                cwd=root,
                timeout=15.0,
            )
        except Exception:
            remote_out = ""

        local_set = set(branches)
        for line in remote_out.splitlines():
            r = line.strip()
            if not r or r in ("origin", "origin/HEAD", "origin/main", "origin/master"):
                continue
            if r.startswith("origin/backup-") or r.startswith("origin/backup/"):
                continue
            local_counterpart = r[len("origin/"):] if r.startswith("origin/") else r
            if local_counterpart not in local_set and r not in branches:
                branches.append(r)

    return sorted(branches)


def _matches_receipt(
    data: Dict[str, Any],
    branch: str,
    head_sha: str,
    expected_base_sha: Optional[str] = None,
) -> bool:
    rec_sha = data.get("head_commit_sha") or data.get("head_sha")
    rec_branch = data.get("branch")
    if rec_branch != branch or data.get("terminal_outcome") != "PASS":
        return False
    if not (rec_sha and rec_sha == head_sha):
        return False
    if expected_base_sha and data.get("base_commit_sha") and data.get("base_commit_sha") != expected_base_sha:
        return False
    # Enforce complete gate set: reject legacy receipts lacking grounded delivery verification or failing stages
    stages = data.get("stages", [])
    if not isinstance(stages, list):
        return False
    total_stages = int(data.get("total_stages") or data.get("stages_evaluated") or len(stages))
    if total_stages < 30 or len(stages) < total_stages:
        return False
    if not all(isinstance(s, dict) and s.get("status") in ("PASS", "EXEMPT") for s in stages):
        return False
    stage_names = {s.get("stage_name") for s in stages if isinstance(s, dict)}
    if "Grounded Delivery & Claim Manifest Verification" not in stage_names:
        return False
    return True


def _check_fallback_git_and_worktree(branch: str, head_sha: str, slug: str, repo_root: Path = ROOT) -> bool:
    expected_base_sha: Optional[str] = None
    try:
        expected_base_sha = run_git(["rev-parse", "main"], cwd=repo_root, check=False).strip() or None
    except Exception:
        expected_base_sha = None

    try:
        raw = run_git(["show", f"{branch}:reports/readiness/local_main_readiness_{slug}_receipt.json"], cwd=repo_root)
        if _matches_receipt(json.loads(raw), branch, head_sha, expected_base_sha=expected_base_sha):
            return True
    except Exception:
        pass

    wt_receipt = (
        repo_root
        / ".worktrees"
        / slug
        / "reports"
        / "readiness"
        / f"local_main_readiness_{slug}_receipt.json"
    )
    if wt_receipt.exists():
        try:
            with open(wt_receipt, "r", encoding="utf-8") as f:
                if _matches_receipt(json.load(f), branch, head_sha, expected_base_sha=expected_base_sha):
                    return True
        except Exception:
            pass
    return False


def _check_readiness_receipt_pass(branch: str, head_sha: str, slug: str, repo_root: Path = ROOT) -> bool:
    # 1. Out-of-tree DSSE attestation or legacy in-tree receipt
    try:
        from tools.attestation_core import check_readiness_qualification

        passed, _, _ = check_readiness_qualification(
            repo_root=repo_root,
            branch=branch,
            head_sha=head_sha,
            slug=slug,
        )
        if passed:
            return True
    except Exception:
        pass

    # 2. Git object database or active worktree fallback
    return _check_fallback_git_and_worktree(branch, head_sha, slug, repo_root=repo_root)


def inspect_branch_status(branch: str, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = repo_root or ROOT
    head_sha = (run_git(["rev-parse", branch], cwd=root, check=False) or "").strip()
    eff_branch = branch
    if branch == "HEAD":
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False, timeout=10.0
        )
        if proc.returncode == 0 and proc.stdout.strip():
            eff_branch = proc.stdout.strip()
    behind, ahead = compute_first_parent_sync("main", eff_branch, cwd=root)
    if behind == -1 or ahead == -1:
        try:
            sync = subprocess.run(["git", "rev-list", "--left-right", "--count", f"main...{eff_branch}"], cwd=root, capture_output=True, text=True, check=False, timeout=10)
            if sync.returncode == 0:
                behind, ahead = (int(part) for part in sync.stdout.split())
        except Exception:
            pass

    if ahead == -1 or behind == -1:
        clean_merge = False
    elif ahead > 0:
        clean_merge, _ = check_merge_conflict("main", eff_branch, cwd=root)
    else:
        clean_merge = True

    slug_branch = branch
    if branch == "HEAD":
        b_name = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).strip()
        if b_name and b_name != "HEAD":
            slug_branch = b_name

    slug = slugify_branch(slug_branch)
    receipt_pass = _check_readiness_receipt_pass(slug_branch, head_sha, slug, repo_root=root)
    changed_files_raw = run_git(["diff", "--name-only", f"main...{eff_branch}"], cwd=root, check=False)
    changed_files = [f for f in changed_files_raw.splitlines() if f.strip()]

    from .diff_semantics import classify_branch_diff
    diff_sem = classify_branch_diff(
        changed_files,
        branch=eff_branch,
        clean_merge=clean_merge,
        behind=behind,
        repo_root=root,
    )

    merge_base_sha = run_git(["merge-base", "main", eff_branch], cwd=root, check=False).strip()[:9]
    main_head_sha = run_git(["rev-parse", "main"], cwd=root, check=False).strip()[:9]
    last_subject = run_git(["log", "-1", "--format=%s", eff_branch], cwd=root, check=False).strip()

    return {
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "clean_merge": clean_merge,
        "readiness_receipt_pass": receipt_pass,
        "changed_files": changed_files,
        "is_remote": is_remote_tracking_branch(branch),
        "tip_sha": head_sha[:9],
        "merge_base_sha": merge_base_sha,
        "main_head_sha": main_head_sha,
        "last_commit_subject": last_subject,
        "diff_semantics": diff_sem.to_dict(),
        "diff_category": diff_sem.category,
        "is_docs_only_plan": diff_sem.is_docs_only and diff_sem.is_unstarted_plan,
        "code_files_count": diff_sem.code_files_count,
        "doc_files_count": diff_sem.doc_files_count,
    }


def _audit_performance_settings() -> None:
    for cfg_key, expected in [
        ("core.fsmonitor", "true"),
        ("core.untrackedCache", "true"),
        ("core.commitGraph", "true"),
    ]:
        try:
            val = run_git(["config", "--bool", cfg_key], check=False).strip().lower()
            if val == expected:
                print(f"✅ Git performance setting '{cfg_key}' is enabled ({val}).")
            else:
                print(
                    f"ℹ️ Git performance setting '{cfg_key}' is '{val}' (recommended: '{expected}'). Enable with: git config {cfg_key} {expected}"
                )
        except Exception:
            pass


def _audit_git_hooks() -> bool:
    all_healthy = True
    try:
        hooks_path = run_git(["config", "core.hooksPath"], check=False)
        if hooks_path == ".githooks":
            print("✅ Git core.hooksPath is correctly set to '.githooks'.")
        else:
            print(
                f"❌ Git core.hooksPath is '{hooks_path}' (expected '.githooks'). Fix with: git config core.hooksPath .githooks"
            )
            all_healthy = False
    except Exception as e:
        print(f"❌ Failed to check core.hooksPath: {e}")
        all_healthy = False

    for hook_rel in REQUIRED_HOOKS:
        hook_path = ROOT / hook_rel
        if not hook_path.exists():
            print(f"❌ Missing hook script: {hook_rel}")
            all_healthy = False
        elif not os.access(hook_path, os.X_OK):
            print(f"❌ Hook is not executable: {hook_rel}. Fix with: chmod +x {hook_rel}")
            all_healthy = False
        else:
            print(f"✅ Hook exists and is executable: {hook_rel}")
    return all_healthy


def cmd_doctor(args: argparse.Namespace) -> int:
    print("=" * 80)
    print("  LOCAL PR GOVERNANCE: DOCTOR HEALTH AUDIT")
    print("=" * 80)

    all_healthy = True

    # 1. Git hooks audit
    if not _audit_git_hooks():
        all_healthy = False

    # 1b. Git engine performance settings
    _audit_performance_settings()

    # 3. Python virtualenv
    py = resolve_python()
    if not Path(py).exists():
        print(f"❌ Python executable not found at: {py}")
        all_healthy = False
    else:
        print(f"✅ Python runtime available: {py}")

    # 4. Critical Gatekeeper Tools
    critical_tools = [
        "tools/verify_commit.py",
        "tools/verify_main_readiness.py",
        "tools/verify_merge_receipt.py",
        "tools/lint_commit_message.py",
        "tools/validate_dual_pipeline_contracts.py",
        "tools/validate_output_hierarchy.py",
        "tools/lint_production_purity.py",
        "tools/scan_credential_leaks.py",
    ]
    for tool_rel in critical_tools:
        tool_path = ROOT / tool_rel
        if tool_path.exists():
            print(f"✅ Gatekeeper tool present: {tool_rel}")
        else:
            print(f"❌ Missing gatekeeper tool: {tool_rel}")
            all_healthy = False

    # 5. Merged Feature Branch Hygiene & Auto-Prune
    try:
        active_wt_branches = {
            str(wt.get("branch", "")).strip() for wt in list_active_worktrees(ROOT)
        }
        current_branch = run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, check=False
        ).strip()
        from .merge_proof import BranchMergeState, classify_branch_merge_state

        main_head = run_git(["rev-parse", "main"], cwd=ROOT, check=False).strip()
        merged_raw = run_git(
            ["branch", "--format=%(refname:short)", "--merged", "main"], cwd=ROOT, check=False
        )
        merged_candidates = []
        zero_commit_lingering = []
        for line in merged_raw.splitlines():
            b = line.strip()
            if (
                not b
                or b in ("main", "master", current_branch)
                or b in active_wt_branches
                or b.startswith("(")
                or "/" not in b
            ):
                continue
            b_head = run_git(["rev-parse", b], cwd=ROOT, check=False).strip()
            m_state = classify_branch_merge_state(b, b_head, main_head, root=ROOT)
            if m_state == BranchMergeState.GENUINELY_MERGED:
                merged_candidates.append(b)
            elif m_state == BranchMergeState.ZERO_COMMIT_ANCESTOR:
                zero_commit_lingering.append(b)

        if merged_candidates:
            print(
                f"ℹ️ Found {len(merged_candidates)} lingering genuinely merged feature branch(es) (manual retirement recommended): {', '.join(merged_candidates)}"
            )
        else:
            print("✅ No lingering merged feature branches found.")

        if zero_commit_lingering:
            print(
                f"ℹ️ Found {len(zero_commit_lingering)} lingering zero-commit ancestor branch(es) sitting at older main: {', '.join(zero_commit_lingering)}"
            )
    except Exception as e:
        print(f"⚠️ Failed to audit merged branches: {e}")

    # 6. Cache Pruning (Optional)
    if getattr(args, "prune_caches", False):
        try:
            from tools.readiness_cache_core import prune_stale_caches
            days = getattr(args, "retention_days", 7)
            print(f"🧹 Pruning stale caches older than {days} days...")
            res = prune_stale_caches(ROOT, max_age_days=days)
            print(f"✅ Pruned {res.get('total_pruned', 0)} stale cache item(s): {res}")
        except Exception as exc:
            print(f"⚠️ Failed to prune caches: {exc}")

    print("=" * 80)
    if all_healthy:
        print("🎉 ALL GOVERNANCE DOCTOR CHECKS PASSED: Environment is 100% healthy.")
        print("=" * 80)
        return 0
    else:
        print("⛔ GOVERNANCE DOCTOR DETECTED ISSUES: Resolve errors above.")
        print("=" * 80)
        return 1


def get_priority_registry_path() -> Path:
    """Resolve relative to the current working tree dynamically to avoid module-level path leakage."""
    return Path(__file__).resolve().parents[2] / "config" / "pr_priority_registry.json"


def load_priority_registry() -> Dict[str, Dict[str, str]]:
    """
    Load branch-to-priority-tier metadata from config/pr_priority_registry.json.
    Returns a dict mapping branch_name -> {
        "tier": str,
        "rationale": str,
        "scope": str,
    }.
    """
    mapping: Dict[str, Dict[str, str]] = {}
    registry_path = get_priority_registry_path()
    if not registry_path.exists():
        return mapping

    import contextlib
    import fcntl
    import time

    with open(registry_path, "r", encoding="utf-8") as f:
        deadline = time.monotonic() + 10.0
        locked = False
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                locked = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        try:
            registry = json.load(f)
        finally:
            if locked:
                with contextlib.suppress(Exception):
                    fcntl.flock(f, fcntl.LOCK_UN)

    for tier, data in registry.get("tiers", {}).items():
        raw_branches = data.get("branches", {})
        if isinstance(raw_branches, dict):
            for branch, meta in raw_branches.items():
                if isinstance(meta, dict):
                    mapping[branch] = {
                        "tier": tier,
                        "rationale": meta.get("rationale", ""),
                        "reason": meta.get("reason", "") or meta.get("rationale", ""),
                        "scope": meta.get("scope", ""),
                        "owner": meta.get("owner", ""),
                        "expires_at": meta.get("expires_at", ""),
                    }
                else:
                    mapping[branch] = {
                        "tier": tier,
                        "rationale": str(meta),
                        "reason": str(meta),
                        "scope": "",
                    }
        elif isinstance(raw_branches, list):
            for item in raw_branches:
                if isinstance(item, str):
                    mapping[item] = {
                        "tier": tier,
                        "rationale": "",
                        "scope": "",
                    }
                elif isinstance(item, dict):
                    branch_name = item.get("branch", "")
                    if branch_name:
                        mapping[branch_name] = {
                            "tier": tier,
                            "rationale": item.get("rationale", ""),
                            "reason": item.get("reason", "") or item.get("rationale", ""),
                            "scope": item.get("scope", ""),
                            "owner": item.get("owner", ""),
                            "expires_at": item.get("expires_at", ""),
                        }

    for branch, ov in registry.get("overrides", {}).items():
        if isinstance(ov, dict):
            mapping[branch] = {
                "tier": ov.get("tier", "P3"),
                "rationale": ov.get("reason", "") or ov.get("rationale", ""),
                "reason": ov.get("reason", "") or ov.get("rationale", ""),
                "scope": ov.get("scope", ""),
                "owner": ov.get("owner", ""),
                "expires_at": ov.get("expires_at", ""),
            }
    return mapping


def _get_branch_meta(priority_map: Dict[str, Any], branch: str) -> Dict[str, str]:
    """Extract tier, rationale, and scope from priority_map supporting both dict and str values."""
    val = priority_map.get(branch)
    if isinstance(val, dict):
        return {
            "tier": val.get("tier", "P3"),
            "rationale": val.get("rationale", ""),
            "scope": val.get("scope", ""),
        }
    elif isinstance(val, str):
        return {
            "tier": val,
            "rationale": "",
            "scope": "",
        }
    return {
        "tier": "P3",
        "rationale": "Unregistered branch",
        "scope": "",
    }


def _determine_readiness_override(tier: str, disp: Any, review_verdict: str, default_rl: str) -> str:
    if tier.startswith("A"):
        return "🗄️ Archived"
    if tier.startswith("D"):
        return "🪦 Superseded (Tombstone)" if (disp and getattr(disp, "action", "") == "TOMBSTONE") else default_rl
    verdict_map = {
        ReviewVerdict.GOVERNANCE_BLOCK.value: "⛔ Governance Block",
        ReviewVerdict.SPLIT_REQUIRED.value: "✂️ Split Required",
        ReviewVerdict.REBASE_RETARGET.value: "🔄 Retarget Required",
    }
    return verdict_map.get(review_verdict, default_rl)


_PRIORITY_ORDER = ["P0", "P1", "P2", "P3", "D", "TOMBSTONE"]


def _tier_sort_key(tier: str) -> int:
    base = tier.split(".")[0]
    try:
        return _PRIORITY_ORDER.index(base)
    except ValueError:
        return len(_PRIORITY_ORDER)


def _parse_waves_from_branch(
    branch: str, wt_path: Optional[Path] = None, changed_files: Optional[List[str]] = None
) -> tuple[str, str]:
    """Return (open, completed) waves read live from the branch's plan documents (see wave_status.py)."""
    from .wave_status import compute_wave_columns

    if changed_files is not None:
        changed = changed_files
    else:
        changed = (run_git(["diff", "--name-only", f"main...{branch}"], check=False) or "").splitlines()

    def _show(br: str, path: str) -> str:
        return run_git(["show", f"{br}:{path}"], check=False) or ""

    return compute_wave_columns(branch, _show, changed, wt_path=wt_path)


def _render_queue_table(
    global_queue: List[Dict[str, Any]],
    inv_counts: Optional[Dict[str, int]] = None,
    view_mode: str = "executive",
    inv: Optional[Dict[str, Any]] = None,
    show_council: bool = False,
    repo_root: Optional[Path] = None,
) -> None:
    from .render import render_queue_table

    render_queue_table(
        global_queue,
        inv_counts=inv_counts,
        view_mode=view_mode,
        repo_root=repo_root or ROOT,
        inv=inv,
        show_council=show_council,
    )


def _compute_overlap_context_map(
    matrix: Any, branches: List[str], info_map: Dict[str, Any], args: argparse.Namespace
) -> Dict[str, Dict[str, Any]]:
    overlap_context_map: Dict[str, Dict[str, Any]] = {}
    for cand in getattr(matrix, "superseded_candidates", []):
        overlap_context_map[cand["older_branch"]] = {
            "duplicate_of": cand["newer_branch"],
            "shared_files": cand.get("shared_file_count", 0),
        }

    if getattr(args, "simulate_merge", False) or getattr(args, "overlap", False):
        try:
            from tools.pr_overlap_core import plan_merge_order
            mplan = plan_merge_order(ROOT, "main", [b for b in branches if info_map[b]["clean_merge"]])
            if mplan.has_errors or mplan.plan_status == "PROVISIONAL":
                print(f"⚠️ Merge order planner provisional: {'; '.join(mplan.error_details)}", file=sys.stderr)
            for rec in mplan.rebase_recommendations:
                ctx = overlap_context_map.setdefault(rec.branch, {})
                if rec.is_stacked:
                    ctx["rebase_onto"] = rec.target_base
                    ctx["upstream_branch"] = rec.upstream_branch
        except Exception as e:
            print(f"⚠️ Merge order planner simulation failure: {e}", file=sys.stderr)
    return overlap_context_map
def _build_orphan_queue_item(entry: Dict[str, Any]) -> Dict[str, Any]:
    c = entry["classification"]
    is_deliv = (c == "ORPHAN_DELIVERABLES")
    next_act = explain_worktree_action(entry)
    live_status = entry.get("live_meeting_status")
    dom = "Live Meeting" if (is_deliv or live_status or entry["name"].startswith("live_")) else "Housekeeping"

    status_labels = {
        "RECORDING_IN_PROGRESS": "🎙️ Recording",
        "SYNTHESIS_PENDING": "⏳ Synthesis Pending",
        "STAGE_U0_READY": "📦 Stage U0 Ready",
        "ORPHANED_AUDIO": "🎵 Orphaned Audio",
        "ABANDONED_STUB": "📁 Abandoned Stub",
    }
    if live_status in status_labels:
        rl = status_labels[live_status]
    elif c.startswith("ORPHAN_"):
        rl = "📂 Orphan Dir"
    elif c == "STALE_REGISTRATION":
        rl = "🗄️ Stale Registration"
    else:
        rl = "📄 Non-Directory"

    return {
        "branch": entry["name"],
        "tier": "W",
        "ahead": 0,
        "behind": 0,
        "clean_merge": False,
        "dirty": bool(entry.get("unique_files_count", 0) > 0),
        "registry_rationale": "Orphan worktree directory" if c.startswith("ORPHAN_") else c,
        "domain_impact": f"{c}: {entry.get('unique_files_count', 0)} unique files",
        "domain": dom,
        "priority": {"tier": "W", "score": 100 if (is_deliv or live_status == "STAGE_U0_READY") else 10},
        "value_score": 100.0 if (is_deliv or live_status == "STAGE_U0_READY") else 10.0,
        "readiness": rl,
        "risk_score": 0 if is_deliv else 1,
        "risk_tier": 0 if is_deliv else 1,
        "open_waves": "—",
        "completed_waves": "—",
        "next_action": next_act,
        "superseded_by": None,
        "review_verdict": "ORPHAN",
        "review_note": "",
        "code_review": {},
        "conflict_triage": None,
        "rebase_effort": "NONE",
        "effort_score": 0.0,
        "manual_conflicts": 0,
        "info": {"ahead": 0, "behind": 0, "clean_merge": False},
        "wt": {"path": entry["path"], "is_root": False},
        "classification": c,
        "unique_files_count": entry.get("unique_files_count", 0),
    }


def _build_stash_queue_item(s: Dict[str, Any]) -> Dict[str, Any]:
    stash_act = explain_worktree_action({
        "classification": s["classification"],
        "stash_ref": s["ref"],
        "branch": s["branch"],
        "subject": s["subject"],
    })
    return {
        "branch": s["ref"],
        "tier": "W",
        "ahead": 0,
        "behind": 0,
        "clean_merge": False,
        "dirty": False,
        "registry_rationale": f"Git stash ({s['classification']})",
        "domain_impact": f"{s['classification']}: {s['subject']}",
        "domain": "Housekeeping",
        "priority": {"tier": "W", "score": 1},
        "value_score": 1.0,
        "readiness": "📦 Stash",
        "risk_score": 2,
        "risk_tier": 2,
        "open_waves": "—",
        "completed_waves": "—",
        "next_action": stash_act,
        "superseded_by": None,
        "review_verdict": "STASH",
        "review_note": s["subject"],
        "code_review": {},
        "conflict_triage": None,
        "rebase_effort": "NONE",
        "effort_score": 0.0,
        "manual_conflicts": 0,
        "info": {"ahead": 0, "behind": 0, "clean_merge": False},
        "wt": None,
        "classification": s["classification"],
    }


def _resolve_merge_eligibility(
    it: Dict[str, Any], blocked: Dict[str, str], cycles: List[List[str]]
) -> str:
    from tools.pr_governance_core.decision_api import MergeEligibility
    b = it["branch"]
    tier = it.get("tier", "")
    ahead = it.get("ahead", 0)
    clean = it.get("clean_merge", True)
    dirty = it.get("dirty", False)

    if tier.startswith(("D", "TOMBSTONE")):
        return MergeEligibility.DECOMMISSIONED.value
    if tier.startswith("W"):
        return MergeEligibility.BLOCKED_WIP.value
    if b in blocked:
        return MergeEligibility.BLOCKED_PREREQUISITE.value
    if any(b in c for c in cycles):
        return MergeEligibility.BLOCKED_CYCLE.value
    if not clean:
        return MergeEligibility.BLOCKED_CONFLICT.value
    if ahead == 0:
        return MergeEligibility.BLOCKED_AUTHORING.value
    if dirty:
        return MergeEligibility.BLOCKED_WIP.value
    readiness_ok = it.get("readiness_receipt_pass")
    if readiness_ok is None and isinstance(it.get("info"), dict):
        readiness_ok = it["info"].get("readiness_receipt_pass")
    open_waves = it.get("open_waves")
    review_verdict = str(it.get("review_verdict") or "")
    if readiness_ok is False:
        return MergeEligibility.BLOCKED_GOVERNANCE.value
    if open_waves not in (None, "", "—", "None", []):
        return MergeEligibility.BLOCKED_GOVERNANCE.value
    if review_verdict in ("GOVERNANCE_BLOCK", "SPLIT_REQUIRED", "FAIL", "BLOCKED"):
        return MergeEligibility.BLOCKED_GOVERNANCE.value
    return MergeEligibility.ELIGIBLE.value


def _populate_sequence_and_eligibility(
    global_queue: List[Dict[str, Any]],
    seq: List[str],
    blocked: Dict[str, str],
    hard_graph: Dict[str, List[str]],
    cycles: List[List[str]],
) -> None:
    for it in global_queue:
        b = it["branch"]
        it["merge_eligibility"] = _resolve_merge_eligibility(it, blocked, cycles)
        it["hard_prerequisites"] = hard_graph.get(b, [])
        it["execution_sequence_index"] = (seq.index(b) + 1) if b in seq else None
        if b in blocked:
            it["blocked_reason"] = blocked[b]


def _enrich_outcome_and_ordering(
    global_queue: List[Dict[str, Any]], priority_map: Dict[str, Any]
) -> None:
    from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome
    from tools.pr_governance_core.queue_actions import (
        explain_blocker_and_evidence_requirement,
        explain_relative_order,
    )
    for idx, it in enumerate(global_queue):
        b = it["branch"]
        tier = it.get("tier", "")
        outcome = evaluate_branch_outcome(
            branch=b,
            tier=tier,
            changed_files=list(it.get("info", {}).get("changed_files", [])),
            override_data=priority_map.get("overrides", {}).get(b) if isinstance(priority_map, dict) else None,
            repo_root=ROOT,
        )
        it["outcome_metadata"] = outcome.to_dict()
        it["value_statement"] = outcome.value_statement()
        prev_it = global_queue[idx - 1] if idx > 0 else None
        next_it = global_queue[idx + 1] if idx + 1 < len(global_queue) else None
        it["relative_order_reason"] = explain_relative_order(it, prev_it, next_it)
        it["ranking_rationale"] = it["relative_order_reason"]
        blocker_str, req_str = explain_blocker_and_evidence_requirement(it)
        it["blocker"] = blocker_str
        it["completion_evidence_requirement"] = req_str
        from .complexity import evaluate_refactoring_complexity
        from .executive_render import detect_graph_mutated
        comp = evaluate_refactoring_complexity(it)
        it["complexity"] = comp.to_dict()
        it["graph_mutated"] = detect_graph_mutated(it, repo_root=ROOT)


def _handle_json_or_receipt_output(
    args: argparse.Namespace,
    global_queue: List[Dict[str, Any]],
    inv: Dict[str, Any],
    seq: List[str],
    blocked: Dict[str, str],
) -> Optional[int]:
    if not (getattr(args, "json", False) or getattr(args, "receipt", None)):
        return None
    out_json = []
    for x in global_queue:
        d = dict(x)
        d.pop("info", None)
        d.pop("wt", None)
        out_json.append(d)

    if getattr(args, "receipt", None):
        receipt_path = Path(args.receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump({
                "terminal_outcome": "PASS",
                "coverage_summary": inv["counts"],
                "execution_sequence": seq,
                "blocked_branches": blocked,
                "branches": out_json,
            }, f, indent=2)
        print(f"Receipt written to {args.receipt}", file=sys.stderr)

    if getattr(args, "json", False):
        print(json.dumps(out_json, indent=2))
        return 0
    return None


def cmd_list(args: argparse.Namespace) -> int:  # noqa: C901
    snapshot_path = getattr(args, "from_snapshot", None) or os.environ.get("NEE_PR_QUEUE_SNAPSHOT_PATH")
    if snapshot_path:
        snap_file = Path(snapshot_path)
        if not snap_file.exists():
            print(f"❌ SNAPSHOT_NOT_FOUND: {snapshot_path}", file=sys.stderr)
            return 1
        with open(snap_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        if isinstance(raw_data, list):
            global_queue = raw_data
            inv_counts = None
            seq = [it["branch"] for it in global_queue if "branch" in it]
            blocked = {}
        elif isinstance(raw_data, dict):
            global_queue = raw_data.get("branches") or raw_data.get("queue") or raw_data.get("global_queue") or []
            inv_counts = raw_data.get("coverage_summary") or raw_data.get("counts")
            seq = raw_data.get("execution_sequence") or [it["branch"] for it in global_queue if "branch" in it]
            blocked = raw_data.get("blocked_branches") or {}
        else:
            global_queue = []
            inv_counts = None
            seq = []
            blocked = {}

        if inv_counts is None:
            reg_c = len([it for it in global_queue if not it.get("tier", "").startswith(("W", "ORPHAN"))])
            stash_c = len([it for it in global_queue if it.get("branch", "").startswith("stash@{") or it.get("tier") == "W"])
            orph_c = max(0, len(global_queue) - reg_c - stash_c)
            inv_counts = {
                "total_worktrees_children": len(global_queue),
                "covered_worktrees_children": len(global_queue),
                "total_physical_children": len(global_queue),
                "total_children": len(global_queue),
                "covered_children": len(global_queue),
                "stashes_total": stash_c,
                "covered_stashes": stash_c,
                "total_stashes": stash_c,
                "registered": reg_c,
                "orphan_directories": orph_c,
                "stale_registrations": 0,
                "live_meeting_sessions": 0,
                "live_recording_in_progress": 0,
                "live_synthesis_pending": 0,
                "live_stage_u0_ready": 0,
            }

        inv = {"counts": inv_counts, "worktrees": [], "stashes": []}

        res_code = _handle_json_or_receipt_output(args, global_queue, inv, seq, blocked)
        if res_code is not None:
            return res_code

        show_council = bool(getattr(args, "council", False))
        _render_queue_table(
            global_queue,
            inv_counts=inv["counts"],
            view_mode=getattr(args, "view", "compact"),
            inv=inv,
            show_council=show_council,
        )
        return 0

    include_remote = getattr(args, "remote", False)
    fetch_first = getattr(args, "fetch", False)
    include_github = getattr(args, "github", False)
    github_prs = {}
    if include_github:
        from .github_adapter import get_github_prs

        github_prs = get_github_prs(repo_root=ROOT, force_refresh=fetch_first)

    branches = get_feature_branches(include_remote=include_remote, fetch_first=fetch_first)
    worktrees = list_active_worktrees(ROOT, ignore_review_artifacts=True)
    branch_to_wt = {wt["branch"]: wt for wt in worktrees if wt.get("branch")}
    priority_map = load_priority_registry()
    rubric = load_rubric()

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(4, len(branches) or 1)) as ex:
        infos = list(ex.map(inspect_branch_status, branches))
    info_map = {b: inf for b, inf in zip(branches, infos, strict=True)}
    health_map = {b: "HEALTHY" if info_map[b]["clean_merge"] else "CONFLICT" for b in branches}

    def _eval_waves(b: str):
        wt_info = branch_to_wt.get(b)
        wt_p = Path(wt_info["path"]) if wt_info and wt_info.get("path") else None
        open_w, comp_w = _parse_waves_from_branch(b, wt_path=wt_p, changed_files=info_map[b].get("changed_files"))
        return b, {"open_waves": open_w, "completed_waves": comp_w}

    with ThreadPoolExecutor(max_workers=min(4, len(branches) or 1)) as ex:
        waves_map = dict(ex.map(_eval_waves, branches))

    from tools.pr_governance_core.code_review import compute_code_review

    enable_llm = getattr(args, "llm_summary", False)

    def _eval_review(b: str):
        return b, compute_code_review(
            b,
            base_branch="main",
            root=ROOT,
            use_cache=True,
            enable_llm_summary=enable_llm,
            clean_merge=info_map[b]["clean_merge"],
            worktree_info=branch_to_wt,
        )

    with ThreadPoolExecutor(max_workers=min(4, len(branches) or 1)) as ex:
        code_reviews = dict(ex.map(_eval_review, branches))

    from tools.pr_topology_core.disposition import (
        GENERIC_KEEP_REASON,
        compute_dispositions,
    )
    from tools.pr_topology_core.matrix import compute_topology_matrix

    print("Computing topology matrix...", file=sys.stderr)
    matrix = compute_topology_matrix(branches, ROOT, "main", branch_status_map=info_map)
    dispositions = compute_dispositions(
        matrix,
        worktree_info=branch_to_wt,
        waves_info=waves_map,
        branch_health=health_map,
        lifecycle_overrides=priority_map,
    )
    files_map = {b: d.files for b, d in matrix.branch_diffs.items()}
    siblings_map = build_siblings_map(files_map)
    overlap_context_map = _compute_overlap_context_map(matrix, branches, info_map, args) or {}


    global_queue = []
    override_warnings: List[str] = []
    for b in branches:
        info = info_map[b]
        wt = branch_to_wt.get(b)
        is_dirty = bool(wt and wt.get("dirty"))
        # ahead==0 + dirty: classify the uncommitted paths so the row keeps a real domain tier (rule §0.6).
        dirty_numstat = (
            fetch_worktree_dirty_numstat(wt["path"])
            if (is_dirty and wt and wt.get("path"))
            else None
        )
        meta = derive_priority(
            b,
            info.get("changed_files", []),
            priority_map,
            rubric,
            ahead=info.get("ahead"),
            behind=info.get("behind"),
            clean_merge=info.get("clean_merge"),
            worktree=wt,
            numstat=dirty_numstat,
            repo_root=ROOT,
        )
        tier, rationale, scope = meta["tier"], meta["rationale"], meta["scope"]

        disp = dispositions.get(b)
        next_action = "—"
        if disp:
            tier = tier_from_disposition(tier, disp.action)
            next_action = f"[{disp.action}] {disp.reason}"

        w_data = waves_map.get(b, {"open_waves": "—", "completed_waves": "—"})
        open_waves, completed_waves = w_data["open_waves"], w_data["completed_waves"]
        rl, rs = calculate_readiness_and_risk(info, wt, open_waves=open_waves)

        triage = optional_conflict_triage(
            b, ROOT, info["clean_merge"], base="main", behind=info["behind"]
        )

        if disp and disp.action == "KEEP" and disp.reason == GENERIC_KEEP_REASON:
            next_action = explain_next_action(
                {
                    "branch": b,
                    "behind": info["behind"],
                    "open_waves": open_waves,
                    "dirty": is_dirty,
                },
                conflict_files=[f.path for f in triage.conflicted_files] if triage else [],
                siblings=siblings_map.get(b, []),
                all_files=files_map,
                conflict_triage=triage,
                overlap_context=(overlap_context_map or {}).get(b),
            )

        if (
            meta.get("base_tier") == "TOMBSTONE"
        ):  # validated registry pin only (engine enforces owner/expiry)
            tier = "D.1"
            next_action = "[TOMBSTONE] Flagged for tombstone decommissioning in registry"

        code_rev = code_reviews.get(b)
        if not code_rev:
            code_rev = compute_code_review(
                b,
                base_branch="main",
                root=ROOT,
                use_cache=True,
                enable_llm_summary=enable_llm,
                clean_merge=info["clean_merge"],
                worktree_info=branch_to_wt,
            )
        signals = code_rev.signals.to_dict() if hasattr(code_rev.signals, "to_dict") else {}
        recon = reconcile_review(
            code_rev.verdict.value, int(info.get("ahead", 0)), is_dirty, signals
        )
        review_verdict = recon.verdict
        superseded_by = (
            disp.superseded_by if disp and getattr(disp, "superseded_by", None) else None
        )
        if recon.tombstone_evidence and not tier.startswith(("D", "A")):
            tier = "D.1"
            next_action = tombstone_next_action(signals)
            superseded_by = superseded_by or {"ref": "main", "sha": signals.get("main_sha", "")}

        is_remote_only = is_remote_tracking_branch(b)
        if is_remote_only:
            rl = "🌐 [REMOTE_TRACKING_ONLY]"
            next_action = explain_next_action(
                {
                    "branch": b,
                    "behind": info["behind"],
                    "open_waves": open_waves,
                    "dirty": is_dirty,
                },
                conflict_files=[f.path for f in triage.conflicted_files] if triage else [],
                siblings=siblings_map.get(b, []),
                all_files=files_map,
                conflict_triage=triage,
            )
        else:
            rl = _determine_readiness_override(tier, disp, review_verdict, rl)

        risk_tier = compute_risk_tier(tier, info, is_dirty, open_waves, review_verdict)
        if meta.get("override_warning"):
            override_warnings.append(f"{b}: {meta['override_warning']}")

        pr_info = None
        if github_prs:
            from .github_adapter import get_pr_for_branch

            pr_info = get_pr_for_branch(b, github_prs)

        diff_sem = classify_branch_diff(
            list(info.get("changed_files", [])) + list((dirty_numstat or {}).keys()),
            branch=b,
            open_waves=open_waves,
            completed_waves=completed_waves,
            file_content_getter=lambda p: run_git(["show", f"{b}:{p}"], cwd=ROOT, check=False),
            clean_merge=info.get("clean_merge"),
            behind=info.get("behind"),
            repo_root=ROOT,
        )

        wt_path_obj = Path(wt["path"]) if (wt and wt.get("path")) else None
        from tools.pr_governance_core.conflict_triage import analyze_branch_file_diffs
        from tools.pr_governance_core.verdict_verifier import verify_branch_verdict_and_disposition

        topo = analyze_branch_file_diffs(
            b,
            ROOT,
            base="main",
            mb=info.get("merge_base_sha") or None,
            worktree_path=wt_path_obj,
        )
        has_topo = bool(topo.branch_files)

        dirty_files_cnt = (
            int(wt.get("authored_files_count") or wt.get("dirty_files_count") or (1 if is_dirty else 0))
            if wt
            else 0
        )

        row_item = {
            "branch": b,
            "tier": tier,
            "ahead": info["ahead"],
            "behind": info["behind"],
            "clean_merge": info["clean_merge"],
            "dirty": is_dirty,
            "dirty_files_count": dirty_files_cnt,
            "tip_sha": info.get("tip_sha", ""),
            "merge_base_sha": info.get("merge_base_sha", ""),
            "main_head_sha": info.get("main_head_sha", ""),
            "last_commit_subject": info.get("last_commit_subject", ""),
            "action": disp.action if disp else "",
            "disposition_action": disp.action if disp else "",
            "disposition_reason": disp.reason if disp else "",
            "is_remote": is_remote_only,
            "github_pr": pr_info.to_dict() if pr_info else None,
            "registry_rationale": rationale,
            "scope": scope,
            "domain_impact": meta.get("explanation", f"{scope}: {rationale}"),
            "domain": classify_domain(
                b,
                list(info.get("changed_files", [])) + list((dirty_numstat or {}).keys()),
                repo_root=wt_path_obj,
            ),
            "priority": meta,
            "value_score": meta.get("value", 0.0),
            "readiness": rl,
            "risk_score": rs,
            "risk_tier": risk_tier,
            "open_waves": open_waves,
            "completed_waves": completed_waves,
            "next_action": next_action,
            "superseded_by": superseded_by,
            "review_verdict": review_verdict,
            "review_note": recon.note,
            "code_review": code_rev.to_dict(),
            "signals": signals,
            "conflict_triage": triage.to_dict() if triage else None,
            "rebase_effort": triage.effort_band.value if triage else "NONE",
            "effort_score": triage.effort_score if triage else 0.0,
            "manual_conflicts": triage.manual_files_count if triage else 0,
            "diff_semantics": diff_sem.to_dict(),
            "diff_category": diff_sem.category,
            "is_docs_only_plan": diff_sem.is_docs_only and diff_sem.is_unstarted_plan,
            "code_files_count": diff_sem.code_files_count,
            "doc_files_count": diff_sem.doc_files_count,
            "dirty_files": list((dirty_numstat or {}).keys()),
            "deliverable_files_count": diff_sem.deliverable_files_count,
            "deliverable_dirs": diff_sem.deliverable_dirs,
            "has_deliverable_triad": diff_sem.has_deliverable_triad,
            "has_publication_receipt": diff_sem.has_publication_receipt,
            "overlap_files": topo.overlap_files if has_topo else None,
            "absorbed_files": topo.absorbed_files if has_topo else [],
            "salvageable_files": topo.salvageable_files if has_topo else [],
            "in_main_pcts": topo.in_main_pcts if has_topo else {},
            "file_numstat": topo.file_numstat if has_topo else {},
            "info": info,
            "wt": wt,
        }
        v_res = verify_branch_verdict_and_disposition(row_item, root=ROOT)
        row_item["verdict_verification"] = v_res.to_dict()
        row_item["requires_hitl_clarification"] = v_res.requires_hitl_clarification
        row_item["hitl_question_payload"] = v_res.hitl_question_payload
        from .completion_state import evaluate_branch_completion_state
        act_state = evaluate_branch_completion_state(row_item)
        row_item["completion_state"] = act_state.completion_state
        row_item["is_completed"] = act_state.is_completed
        row_item["is_in_progress"] = act_state.is_in_progress
        row_item["in_progress_reasons"] = act_state.in_progress_reasons
        global_queue.append(row_item)

    inv = build_worktree_inventory(
        ROOT,
        include_remote=include_remote,
        fetch_first=fetch_first,
        include_github=include_github,
    )
    covered_children: Set[str] = set()
    for wt in worktrees:
        if not wt.get("is_root") and wt.get("path"):
            covered_children.add(Path(wt["path"]).name)

    for entry in inv["worktrees"]:
        c = entry["classification"]
        covered_children.add(entry["name"])
        if c != "REGISTERED":
            global_queue.append(_build_orphan_queue_item(entry))

    for s in inv["stashes"]:
        global_queue.append(_build_stash_queue_item(s))

    # Coverage Invariant Check (INV-WT-COVERAGE)
    wt_dir = ROOT / ".worktrees"
    if wt_dir.exists():
        actual_children = {p.name for p in wt_dir.iterdir() if not p.name.startswith(".")}
        gap = actual_children - covered_children
        if gap:
            import time
            time.sleep(0.5)
            actual_children = {p.name for p in wt_dir.iterdir() if not p.name.startswith(".")}
            gap = actual_children - covered_children
            if gap:
                inv_retry = build_worktree_inventory(ROOT, include_remote=include_remote, fetch_first=False, include_github=include_github)
                for entry in inv_retry["worktrees"]:
                    if entry["name"] in gap:
                        covered_children.add(entry["name"])
                        if entry["classification"] != "REGISTERED":
                            global_queue.append(_build_orphan_queue_item(entry))
                actual_children = {p.name for p in wt_dir.iterdir() if not p.name.startswith(".")}
                gap = actual_children - covered_children
                if gap:
                    print(f"❌ WORKTREE_COVERAGE_GAP: Missing worktree children: {sorted(gap)}", file=sys.stderr)
                    return 1

    sort_queue(global_queue)
    assign_sub_ranks(global_queue)

    from tools.pr_governance_core.decision_api import (
        detect_hard_dependencies,
        compute_topological_execution_sequence,
    )
    branch_candidates = [it for it in global_queue if not it["tier"].startswith(("W", "D", "TOMBSTONE"))]
    b_names = [it["branch"] for it in branch_candidates]
    ahead_map = {it["branch"]: it.get("ahead", 0) for it in branch_candidates}
    _, hard_graph, cycles = detect_hard_dependencies(b_names, ahead_map, ROOT, "main")
    seq, blocked = compute_topological_execution_sequence(branch_candidates, hard_graph, cycles)

    _populate_sequence_and_eligibility(global_queue, seq, blocked, hard_graph, cycles)
    _enrich_outcome_and_ordering(global_queue, priority_map)

    res_code = _handle_json_or_receipt_output(args, global_queue, inv, seq, blocked)
    if res_code is not None:
        return res_code

    show_council = bool(getattr(args, "council", False))
    _render_queue_table(
        global_queue,
        inv_counts=inv["counts"],
        view_mode=getattr(args, "view", "compact"),
        repo_root=ROOT,
        inv=inv,
        show_council=show_council,
    )
    if override_warnings:
        print(
            f"⚠️ Registry pins ignored ({len(override_warnings)}; pins require owner, reason, expires_at):"
        )
        for w in override_warnings:
            print(f"   - {w}")
    return 0


def cmd_council(args: argparse.Namespace) -> int:
    """Convenience handler running PR queue inspection in executive view with 5-Agent Council deliberation."""
    args.view = "executive"
    args.council = True
    return cmd_list(args)
