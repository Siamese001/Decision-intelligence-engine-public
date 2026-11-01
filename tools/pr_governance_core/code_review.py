"""
Semantic Diff Code Review Engine (Wave 3).
Inspects the actual 3-dot diff vs main, calculates deterministic signals S1–S11,
and derives an authoritative ReviewVerdict with zero worktree pollution.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.pr_governance_core.code_review_rules import (
    BranchCodeReview,
    ChangeClass,
    DeterministicSignals,
    ReviewVerdict,
    classify_file_path,
    evaluate_review_verdict,
)
from tools.pr_governance_core.common import ROOT, logic_digest, run_git

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=8)
def get_git_common_dir(root: Path = ROOT) -> Path:
    """Finds the root .git directory across primary repos and worktrees."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            cand = Path(proc.stdout.strip())
            if cand.is_absolute():
                return cand.resolve()
    except Exception:
        pass
    fallback = root / ".git"
    return fallback.resolve()


def _get_cache_dir(root: Path = ROOT) -> Path:
    git_dir = get_git_common_dir(root)
    cache_dir = git_dir / "pr_code_review_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _check_clean_merge(base_branch: str, branch: str, root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "merge-tree", "--write-tree", base_branch, branch],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _parse_numstat(
    merge_base: str, branch: str, root: Path
) -> Tuple[int, int, int, int, Dict[str, Tuple[int, int]]]:
    """Runs git diff --numstat merge_base...branch and returns line counts and file mappings."""
    proc = subprocess.run(
        ["git", "diff", "--numstat", f"{merge_base}...{branch}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return 0, 0, 0, 0, {}

    total_added = 0
    total_deleted = 0
    binary_count = 0
    file_stats: Dict[str, Tuple[int, int]] = {}

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        added_str, deleted_str, path_str = parts[0], parts[1], parts[2]
        if added_str == "-" and deleted_str == "-":
            binary_count += 1
            file_stats[path_str] = (0, 0)
            continue
        try:
            a = int(added_str)
            d = int(deleted_str)
            total_added += a
            total_deleted += d
            # Handle git rename arrows: path/{old => new}/file
            if " => " in path_str:
                path_str = re.sub(r"\{.*? => (.*?)\}", r"\1", path_str).replace("//", "/")
            file_stats[path_str] = (a, d)
        except ValueError:
            continue

    return len(file_stats), total_added, total_deleted, binary_count, file_stats


def _check_cherry_commits(
    base_branch: str, branch: str, root: Path
) -> Tuple[int, int, int, float, int]:
    """Runs git cherry to find patch-equivalent commits and count unique production commits."""
    proc = subprocess.run(
        ["git", "cherry", "-v", base_branch, branch],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return 0, 0, 0, 0.0, 0

    in_main = 0
    unique = 0
    unique_shas: List[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        flag = parts[0]
        c_sha = parts[1] if len(parts) > 1 else ""
        if flag == "-":
            in_main += 1
        elif flag == "+":
            unique += 1
            if c_sha:
                unique_shas.append(c_sha)

    unique_prod = 0
    if unique_shas:
        show_proc = subprocess.run(
            ["git", "show", "--name-only", "--format=COMMIT %H"] + unique_shas,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if show_proc.returncode == 0 and show_proc.stdout:
            curr_has_prod = False
            for line in show_proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("COMMIT "):
                    if curr_has_prod:
                        unique_prod += 1
                    curr_has_prod = False
                else:
                    if classify_file_path(line) == ChangeClass.PRODUCTION_CODE:
                        curr_has_prod = True
            if curr_has_prod:
                unique_prod += 1

    total = in_main + unique
    ratio = (in_main / total) if total > 0 else 0.0
    return total, in_main, unique, ratio, unique_prod


def _audit_commit_footprint(
    merge_base: str, branch: str, root: Path
) -> Tuple[int, bool, List[str]]:
    """Inspects commit log in merge_base..branch for oversized commits and reverts."""
    log_proc = subprocess.run(
        ["git", "log", "--name-only", "--format=COMMIT %H %s", f"{merge_base}..{branch}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if log_proc.returncode != 0 or not log_proc.stdout.strip():
        return 0, False, []

    max_files = 0
    reverts = []
    current_commit_files = 0
    for line in log_proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT "):
            if current_commit_files > max_files:
                max_files = current_commit_files
            current_commit_files = 0
            parts = line[7:].split(None, 1)
            c_sha = parts[0]
            c_msg = parts[1] if len(parts) > 1 else ""
            if c_msg.startswith("Revert ") or "revert" in c_msg.lower():
                reverts.append(f"{c_sha[:8]}: {c_msg}")
        else:
            current_commit_files += 1

    if current_commit_files > max_files:
        max_files = current_commit_files

    has_mass_reformat = max_files > 300
    return max_files, has_mass_reformat, reverts


def _check_stale_paths(
    merge_base: str,
    base_branch: str,
    branch_files: List[str],
    root: Path,
) -> List[str]:
    """Finds paths modified by branch that main moved, archived, or deleted since fork."""
    stale: List[str] = []
    # 1. Paths deleted or renamed on main since merge_base
    del_proc = subprocess.run(
        ["git", "diff", "--diff-filter=D", "--name-only", f"{merge_base}..{base_branch}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if del_proc.returncode == 0 and del_proc.stdout.strip():
        deleted_on_main = set(del_proc.stdout.splitlines())
        for f in branch_files:
            if f in deleted_on_main:
                stale.append(f)

    # 2. Check for known archived judge paths: dual_pipeline/llm_judge_core/{forensic,graph,recap,leverage}.py
    # If the branch modifies those files directly while main moved them to archive/
    archived_modules = {
        "dual_pipeline/llm_judge_core/forensic.py",
        "dual_pipeline/llm_judge_core/graph.py",
        "dual_pipeline/llm_judge_core/recap.py",
        "dual_pipeline/llm_judge_core/leverage.py",
    }
    for f in branch_files:
        if f in archived_modules and f not in stale:
            # Check if main moved it to archive/
            cat_proc = subprocess.run(
                ["git", "cat-file", "-e", f"{base_branch}:dual_pipeline/llm_judge_core/archive/{Path(f).name}"],
                cwd=root,
                capture_output=True,
                check=False,
            )
            if cat_proc.returncode == 0:
                stale.append(f)

    return stale


def _detect_governance_hazards(
    merge_base: str,
    branch: str,
    file_stats: Dict[str, Tuple[int, int]],
    root: Path,
    worktree_path: Optional[Path] = None,
) -> List[str]:
    """Detects security and governance hazards in the diff."""
    hazards: List[str] = []

    # 1. Unsigned entries in hitl_decision_ledger.json
    if "hitl_decision_ledger.json" in file_stats:
        diff_proc = subprocess.run(
            ["git", "diff", f"{merge_base}...{branch}", "--", "hitl_decision_ledger.json"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        added_lines = []
        if diff_proc.returncode == 0 and diff_proc.stdout:
            added_lines.extend(
                line[1:].strip()
                for line in diff_proc.stdout.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
        if worktree_path and (worktree_path / "hitl_decision_ledger.json").exists():
            wt_diff = subprocess.run(
                ["git", "diff", "-U0", "HEAD", "--", "hitl_decision_ledger.json"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if wt_diff.returncode == 0 and wt_diff.stdout:
                added_lines.extend(
                    line[1:].strip()
                    for line in wt_diff.stdout.splitlines()
                    if line.startswith("+") and not line.startswith("+++")
                )
        added_content = " ".join(added_lines)
        if '"decision_id"' in added_content and '"signature"' not in added_content:
            hazards.append(
                "Unsigned HITL decision entries added to hitl_decision_ledger.json (missing Ed25519 signature)"
            )
        if "Direct SQL Execution" in added_content:
            hazards.append(
                "Remediation policy specifies 'Direct SQL Execution' bypassing Universal Write Gateway"
            )

    # 2. Scratch files at repo root and prohibited meeting files
    for f, (added, _deleted) in file_stats.items():
        if classify_file_path(f) == ChangeClass.SCRATCH_FILES and added > 0:
            hazards.append(f"Root-level scratch/revert script committed: {f}")
        f_obj = Path(f)
        if f_obj.parts and f_obj.parts[0] in ("dual_pipeline", "pipeline_a_intake", "pipeline_b_extraction"):
            if added > 0 and f_obj.name in (
                "standup_healer.py", "neet_healer.py", "nexus_healer.py",
                "jake_amit_miles_healer.py", "mid_sprint_healer.py", "standup_1009_content.py"
            ):
                hazards.append(f"Meeting-specific file forbidden under Invariant 8: {f}")

    # 3. Inspect Python, SQL, and config hunks for governance violations
    code_exts = {".py", ".sql", ".sh", ".toml", ".yaml", ".yml", ".json"}
    hunk_target_files = [
        f for f in file_stats
        if Path(f).suffix.lower() in code_exts and not f.startswith(("tests/", "tools/pr_governance_core/code_review"))
    ]
    if hunk_target_files:
        diff_cmd = ["git", "diff", "-U0", f"{merge_base}...{branch}", "--"] + hunk_target_files
        dp = subprocess.run(diff_cmd, cwd=root, capture_output=True, text=True, check=False, timeout=30.0)
        lines_by_file: Dict[str, List[str]] = {}
        curr_file = ""
        if dp.returncode == 0 and dp.stdout:
            for line in dp.stdout.splitlines():
                if line.startswith("+++ b/"):
                    curr_file = line[6:].strip()
                    continue
                if line.startswith("+") and not line.startswith("+++") and curr_file:
                    lines_by_file.setdefault(curr_file, []).append(line[1:].strip())

        if worktree_path and worktree_path.exists():
            for f in hunk_target_files:
                full_p = worktree_path / f
                if not full_p.is_file():
                    continue
                is_tr = bool(run_git(["ls-files", "--error-unmatch", f], cwd=worktree_path, check=False).strip())
                if is_tr:
                    wt_diff = run_git(["diff", "-U0", "HEAD", "--", f], cwd=worktree_path, check=False)
                    if wt_diff:
                        for line in wt_diff.splitlines():
                            if line.startswith("+") and not line.startswith("+++"):
                                lines_by_file.setdefault(f, []).append(line[1:].strip())
                else:
                    try:
                        for line in full_p.read_text(encoding="utf-8", errors="replace").splitlines():
                            if line.strip():
                                lines_by_file.setdefault(f, []).append(line.strip())
                    except Exception:
                        pass

        for f, added_lines in lines_by_file.items():
            f_obj = Path(f)
            if f_obj.parts and f_obj.parts[0] in ("dual_pipeline", "pipeline_a_intake", "pipeline_b_extraction"):
                if f_obj.name in (
                    "standup_healer.py", "neet_healer.py", "nexus_healer.py",
                    "jake_amit_miles_healer.py", "mid_sprint_healer.py", "standup_1009_content.py"
                ):
                    hazards.append(f"Meeting-specific file forbidden under Invariant 8: {f}")

            for content in added_lines:
                if re.search(r"(?:core\.bare\s*=\s*true|git\s+config\s+.*core\.bare\s+true)", content, re.IGNORECASE):
                    hazards.append("Violation of INV-WT-03: 'core.bare = true' detected in diff")
                if re.search(r"os\.environ\.get\(\s*[\"'](?:[A-Z_]*API_KEY|[A-Z_]*SECRET|OPENAI_API_KEY|GEMINI_API_KEY)[\"']\s*\)", content):
                    hazards.append("Raw os.environ secret access detected; must use CredentialManager SSOT")
                if re.search(r"(?:INSERT\s+INTO|DELETE\s+FROM|UPDATE)\s+(?:canonical_graph|nee_graph|nodes|edges)", content, re.IGNORECASE):
                    if not any(safe in content for safe in ("GovernedGraphWriter", "UniversalWriteGateway", "test_")):
                        hazards.append("Direct SQL mutation against knowledge graph tables bypassing Universal Write Gateway")

                if f_obj.parts and f_obj.parts[0] in ("dual_pipeline", "pipeline_a_intake", "pipeline_b_extraction"):
                    if re.search(r"\b(?:is_mid_sprint|is_lens_catchup|is_neet|_is_neet|is_bcg|_is_bcg|is_standup|_is_standup|is_jarvis|_is_jarvis|heal_standup|heal_mid_sprint|heal_lens_catchup|heal_neet)\b", content):
                        hazards.append(f"Forbidden meeting-specific identifier or predicate in {f}: '{content[:60]}'")
                    if re.search(r"[\"'](?:meeting-\d{8}-|proposal:[a-z0-9\-]+|person:[a-z0-9\-]+)[\"']", content):
                        hazards.append(f"Anti-overfitting violation in {f}: hardcoded meeting or person entity: '{content[:60]}'")
                    if f_obj.name == "graph_healer.py" and re.search(r"[\"'](?:symphony|tanner|Andrew proposed|apple store)[\"']", content, re.IGNORECASE):
                        hazards.append(f"Anti-overfitting violation in {f}: hardcoded meeting string: '{content[:60]}'")
                    if ("llm_judge" in f or "eval" in f) and re.search(r"eval_data\[[\"']score[\"']\]\s*\+?=", content):
                        hazards.append(f"Artificial judge score tampering detected in {f}: '{content[:60]}'")

    return sorted(list(set(hazards)))


def _check_whitespace_or_formatting_only(
    merge_base: str, branch: str, file_stats: Dict[str, Tuple[int, int]], root: Path
) -> Tuple[bool, int]:
    """Checks whether the diff is purely whitespace/formatting changes."""
    if not file_stats:
        return False, 0

    proc = subprocess.run(
        ["git", "diff", "-w", "--ignore-blank-lines", "--shortstat", f"{merge_base}...{branch}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False, len(file_stats)

    out = proc.stdout.strip()
    if not out:
        return True, 0

    # Parse shortstat: "X files changed, Y insertions(+), Z deletions(-)"
    m = re.search(r"(\d+)\s+files?\s+changed", out)
    substantive_files = int(m.group(1)) if m else len(file_stats)

    # If substantive files changed is 0, it's whitespace only
    if substantive_files == 0:
        return True, 0

    return False, substantive_files


def _run_semantic_panel_review(
    branch: str,
    base_branch: str,
    root: Path,
    has_files_changed: bool,
    verdict: ReviewVerdict,
    verdict_reason: str,
    recommended_action: str,
    persist_receipt: bool = False,
) -> Tuple[Optional[Dict[str, Any]], ReviewVerdict, str, str]:
    """Runs the 4-agent SemanticDiffReviewPanel and integrates verdict overrides."""
    if not has_files_changed:
        return None, verdict, verdict_reason, recommended_action
    try:
        from tools.semantic_diff_reviewer import SemanticDiffReviewPanel
        panel = SemanticDiffReviewPanel(root=root)
        report = panel.run_review(
            branch, base_branch=base_branch, persist_receipt=persist_receipt
        )
        if report.verdict in ("BLOCKED", "SPLIT_REQUIRED") and verdict in (ReviewVerdict.MERGE_READY, ReviewVerdict.REVIEW_READY):
            v = ReviewVerdict.GOVERNANCE_BLOCK if report.verdict == "BLOCKED" else ReviewVerdict.SPLIT_REQUIRED
            vr = f"{report.verdict} by Semantic Diff Review Panel ({len(report.findings)} finding(s))"
            ra = f"[{v.value}] Resolve critical semantic review findings before merge"
            return report.to_dict(), v, vr, ra
        return report.to_dict(), verdict, verdict_reason, recommended_action
    except Exception as e:
        logger.warning("Semantic diff review failed for %s: %s", branch, e)
        return None, verdict, verdict_reason, recommended_action


def _build_branch_code_review(
    branch: str,
    signals: DeterministicSignals,
    verdict: ReviewVerdict,
    verdict_reason: str,
    recommended_action: str,
    evidence_digest: str,
    llm_summary: Optional[str],
    semantic_review: Optional[Dict[str, Any]],
) -> BranchCodeReview:
    return BranchCodeReview(
        branch=branch,
        verdict=verdict,
        verdict_reason=verdict_reason,
        recommended_action=recommended_action,
        signals=signals,
        evidence_digest=evidence_digest,
        llm_intent_summary=llm_summary,
        semantic_review=semantic_review,
    )


def audit_post_fork_regression(
    branch: str,
    base_branch: str = "main",
    root: Optional[Path] = None,
) -> Tuple[bool, List[str]]:
    """Checks whether branch deletes functions, classes, or lines added to base_branch after fork point."""
    r = root or ROOT
    reflog_out = run_git(["reflog", "show", branch, "--format=%H %gs"], cwd=r, check=False)
    fork_sha = None
    for line in reversed(reflog_out.splitlines()):
        if "branch: Created from" in line or "checkout: moving from" in line:
            parts = line.split(maxsplit=1)
            if parts:
                fork_sha = parts[0]
                break

    if not fork_sha:
        mb = run_git(["merge-base", base_branch, branch], cwd=r, check=False).strip()
        fork_sha = mb

    if not fork_sha:
        return True, []

    branch_msgs = run_git(["log", f"{base_branch}..{branch}", "--format=%B"], cwd=r, check=False)
    has_supersedes = bool(re.search(r"(?:Supersedes-Commit|supersedes):\s*([a-f0-9]{7,40})", branch_msgs, re.IGNORECASE))
    if has_supersedes:
        return True, []

    branch_touched = set(run_git(["diff", "--name-only", f"{base_branch}...{branch}"], cwd=r, check=False).splitlines())
    regressions: List[str] = []

    # Same-branch ratchet check against base_branch (enforced even when fork_sha == base_branch)
    try:
        from tools.lint_test_and_healer_ratchet import (
            _has_ledger_authorization,
            check_healer_ratchet,
            check_test_ratchet,
        )

        for f in sorted(branch_touched):
            norm = f.replace("\\", "/").strip()
            if not norm:
                continue
            is_test = norm.startswith("tests/") and norm.endswith(".py")
            is_healer = (
                norm.startswith("dual_pipeline/transcript_qa_core/")
                and "healer" in Path(norm).name
                and norm.endswith(".py")
            )
            if not (is_test or is_healer):
                continue
            main_src = run_git(["show", f"{base_branch}:{norm}"], cwd=r, check=False)
            if not main_src:
                continue
            branch_src = run_git(["show", f"{branch}:{norm}"], cwd=r, check=False)
            if is_test and not _has_ledger_authorization(r, norm):
                if not branch_src:
                    regressions.append(f"{norm}: existing test file deleted from {base_branch} without Supersedes-Commit.")
                else:
                    regressions.extend(check_test_ratchet(main_src, branch_src, norm))
            elif is_healer and branch_src:
                regressions.extend(check_healer_ratchet(main_src, branch_src, norm))
    except Exception:
        pass

    main_commits = [c.strip() for c in run_git(["rev-list", f"{fork_sha}..{base_branch}"], cwd=r, check=False).splitlines() if c.strip()]
    if not main_commits:
        return len(regressions) == 0, regressions

    main_touched = set(run_git(["diff", "--name-only", f"{fork_sha}..{base_branch}"], cwd=r, check=False).splitlines())
    overlap = main_touched & branch_touched
    if not overlap:
        return len(regressions) == 0, regressions
    from .diff_semantics import is_code_file
    for f in sorted(overlap):
        if not is_code_file(f):
            continue
        diff_main = run_git(["diff", "-U0", f"{fork_sha}..{base_branch}", "--", f], cwd=r, check=False)
        added_on_main = {
            line[1:].strip() for line in diff_main.splitlines()
            if line.startswith("+") and not line.startswith("+++") and len(line[1:].strip()) > 15
            and not line[1:].strip().startswith(("#", "//", "/*", "*"))
        }
        if not added_on_main:
            continue

        diff_branch = run_git(["diff", "-U0", f"{base_branch}...{branch}", "--", f], cwd=r, check=False)
        deleted_in_branch = {
            line[1:].strip() for line in diff_branch.splitlines()
            if line.startswith("-") and not line.startswith("---") and len(line[1:].strip()) > 15
        }
        reverted_lines = added_on_main & deleted_in_branch
        if reverted_lines:
            sample = list(reverted_lines)[0]
            regressions.append(
                f"{f}: reverts line(s) added on {base_branch} after fork point {fork_sha[:8]} without 'Supersedes-Commit:' (e.g. '{sample[:60]}')"
            )

    return len(regressions) == 0, regressions


def compute_code_review(
    branch: str,
    base_branch: str = "main",
    root: Path = ROOT,
    use_cache: bool = True,
    enable_llm_summary: bool = False,
    clean_merge: Optional[bool] = None,
    persist_receipt: bool = False,
    worktree_info: Optional[Dict[str, Any]] = None,
) -> BranchCodeReview:
    """
    Computes a comprehensive semantic code review for the branch against base_branch.
    Uses cached result if available to guarantee sub-millisecond warm lookups.
    """
    try:
        main_sha = run_git(["rev-parse", base_branch], cwd=root).strip()
        branch_sha = run_git(["rev-parse", branch], cwd=root).strip()
    except Exception as e:
        logger.warning("Failed to resolve branch SHAs for review: %s", e)
        signals = DeterministicSignals(branch_sha=branch)
        return BranchCodeReview(
            branch=branch,
            verdict=ReviewVerdict.REVIEW_READY,
            verdict_reason=f"Failed to inspect git branch: {e}",
            recommended_action="[REVIEW_READY] Git error; manual inspection required",
            signals=signals,
        )

    sem_rev_path = root / "tools" / "semantic_diff_reviewer.py"
    files_to_hash = [__file__, str(Path(__file__).with_name("code_review_rules.py"))]
    if sem_rev_path.exists():
        files_to_hash.append(str(sem_rev_path))
    rules_key = logic_digest(*files_to_hash)

    wt_dirty_suffix = ""
    if worktree_info:
        wt = worktree_info.get(branch)
        if wt and wt.get("dirty"):
            wt_dirty_suffix = f"_dirty_{wt.get('authored_files_count', 1)}"

    cache_file = _get_cache_dir(root) / f"{main_sha[:12]}_{branch_sha[:12]}_{rules_key}{wt_dirty_suffix}.json"
    if use_cache and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                sig_data = data.get("signals", {})
                signals = DeterministicSignals(**sig_data)
                cached_llm = data.get("llm_intent_summary")
                if enable_llm_summary and not cached_llm:
                    from tools.pr_governance_core.llm_summary import generate_advisory_llm_summary
                    cached_llm = generate_advisory_llm_summary(
                        branch, signals, list(sig_data.get("class_counts", {}).keys()), root=root
                    )
                    data["llm_intent_summary"] = cached_llm
                    with open(cache_file, "w", encoding="utf-8") as fw:
                        json.dump(data, fw, indent=2)

                return BranchCodeReview(
                    branch=data.get("branch", branch),
                    verdict=ReviewVerdict(data.get("verdict", ReviewVerdict.REVIEW_READY.value)),
                    verdict_reason=data.get("verdict_reason", ""),
                    recommended_action=data.get("recommended_action", ""),
                    signals=signals,
                    evidence_digest=data.get("evidence_digest", ""),
                    llm_intent_summary=cached_llm,
                    semantic_review=data.get("semantic_review"),
                )
        except Exception as e:
            logger.warning("Cache read failed for %s: %s", branch, e)

    # Calculate 3-dot diff coordinates
    try:
        merge_base = run_git(["merge-base", base_branch, branch], cwd=root).strip()
    except Exception:
        merge_base = main_sha

    is_clean = clean_merge if clean_merge is not None else _check_clean_merge(base_branch, branch, root)
    wt_p = (
        Path(worktree_info[branch]["path"])
        if worktree_info and worktree_info.get(branch) and worktree_info[branch].get("path")
        else None
    )
    total_files, insertions, deletions, binary_count, file_stats = _parse_numstat(
        merge_base, branch, root
    )
    if wt_p and wt_p.exists():
        from tools.pr_governance_core.verdict_verifier import (
            collect_dirty_worktree_files,
            compute_dirty_file_in_main_pct,
        )
        dirty_files = collect_dirty_worktree_files(wt_p, set(file_stats.keys()))
        for df in dirty_files:
            _, numstat = compute_dirty_file_in_main_pct(df, wt_p, base_branch, root)
            file_stats[df] = numstat
            insertions += numstat[0]
            deletions += numstat[1]
        total_files = len(file_stats)

    # Classify files
    class_counts: Dict[str, int] = {}
    class_lines: Dict[str, int] = {}
    for f, (added, deleted) in file_stats.items():
        c_cls = classify_file_path(f).value
        class_counts[c_cls] = class_counts.get(c_cls, 0) + 1
        class_lines[c_cls] = class_lines.get(c_cls, 0) + (added + deleted)

    # Trivial / whitespace
    is_ws_only, substantive_files = _check_whitespace_or_formatting_only(
        merge_base, branch, file_stats, root
    )

    # Cherry commits
    total_c, in_main_c, unique_c, cherry_ratio, unique_prod_c = _check_cherry_commits(
        base_branch, branch, root
    )

    # Per-commit footprint
    max_commit_files, has_mass_reformat, reverts = _audit_commit_footprint(
        merge_base, branch, root
    )

    # Stale paths
    stale_paths = _check_stale_paths(merge_base, base_branch, list(file_stats.keys()), root)

    # Test coverage
    prod_files = class_counts.get(ChangeClass.PRODUCTION_CODE.value, 0)
    test_files = class_counts.get(ChangeClass.TEST_CODE.value, 0)
    has_prod = prod_files > 0
    has_test = test_files > 0
    missing_tests = has_prod and not has_test

    # Governance hazards
    hazards = _detect_governance_hazards(merge_base, branch, file_stats, root, worktree_path=wt_p)
    anti_reg_ok, regressions = audit_post_fork_regression(branch, base_branch=base_branch, root=root)
    if not anti_reg_ok:
        hazards.extend(regressions)

    # Name vs content mismatch
    mismatch = False
    mismatch_desc = ""
    lower_b = branch.lower()
    if (lower_b.startswith("feat/") or lower_b.startswith("fix/")) and prod_files == 0:
        if is_ws_only:
            mismatch = True
            mismatch_desc = "Branch prefixed feat/fix but contains only whitespace formatting"
        elif class_counts.get(ChangeClass.DOCUMENTATION.value, 0) > 0:
            mismatch = True
            mismatch_desc = "Branch prefixed feat/fix but contains documentation only"
    elif (lower_b.startswith("docs/") or lower_b.startswith("doc/")) and prod_files > 0:
        mismatch = True
        mismatch_desc = f"Branch prefixed docs/ but contains {prod_files} production or governance file(s)"

    from tools.pr_governance_core.diff_semantics import extract_file_hunks
    file_hunks_list = extract_file_hunks(merge_base, branch, root, worktree_path=wt_p)
    symbols_found = []
    for h in file_hunks_list:
        if h.enclosing_symbol:
            symbols_found.append(h.enclosing_symbol)
        for line in h.added_lines:
            m_def = re.match(r"^\s*(?:async\s+)?def\s+([a-zA-Z_0-9]+)\s*\(", line)
            if m_def:
                symbols_found.append(f"def {m_def.group(1)}():")
            m_cls = re.match(r"^\s*class\s+([a-zA-Z_0-9]+)", line)
            if m_cls:
                symbols_found.append(f"class {m_cls.group(1)}")
    modified_ast_symbols = sorted(list(set(symbols_found)))

    substantive_hunks = [
        h
        for h in file_hunks_list
        if classify_file_path(h.file_path) in (ChangeClass.PRODUCTION_CODE, ChangeClass.TEST_CODE, ChangeClass.CONFIG)
    ]
    if substantive_hunks:
        total_added = sum(len(h.added_lines) for h in substantive_hunks)
        absorbed_added = 0
        for h in substantive_hunks:
            try:
                show_p = subprocess.run(["git", "show", f"main:{h.file_path}"], cwd=root, capture_output=True, text=True, check=False, timeout=30.0)
                if show_p.returncode == 0:
                    main_lines = {ln.strip() for ln in show_p.stdout.splitlines() if ln.strip()}
                    absorbed_added += sum(1 for ln in h.added_lines if ln.strip() in main_lines)
            except Exception:
                pass
        hunk_absorption = absorbed_added / max(total_added, 1)
    else:
        hunk_absorption = 0.0

    signals = DeterministicSignals(
        files_changed=total_files,
        insertions=insertions,
        deletions=deletions,
        binary_files=binary_count,
        class_counts=class_counts,
        class_lines=class_lines,
        is_whitespace_or_import_only=is_ws_only,
        substantive_files_changed=substantive_files,
        total_commits=total_c,
        commits_in_main=in_main_c,
        unique_commits=unique_c,
        unique_production_commits=unique_prod_c,
        patch_equivalent_ratio=cherry_ratio,
        max_files_in_single_commit=max_commit_files,
        has_mass_reformat_commit=has_mass_reformat,
        stale_paths=stale_paths,
        has_production_changes=has_prod,
        has_test_changes=has_test,
        missing_test_coverage=missing_tests,
        governance_hazards=hazards,
        name_content_mismatch=mismatch,
        mismatch_description=mismatch_desc,
        revert_commits=reverts,
        merge_base_sha=merge_base[:8],
        main_sha=main_sha[:8],
        branch_sha=branch_sha[:8],
        file_hunks=[h.to_dict() for h in file_hunks_list],
        hunk_absorption_ratio=hunk_absorption,
        modified_ast_symbols=modified_ast_symbols,
    )

    verdict, verdict_reason, recommended_action = evaluate_review_verdict(
        signals=signals,
        is_clean_merge=is_clean,
        branch_name=branch,
    )

    # Compute digest
    sig_json = json.dumps(signals.to_dict(), sort_keys=True)
    evidence_digest = hashlib.sha256(sig_json.encode("utf-8")).hexdigest()

    llm_summary = None
    if enable_llm_summary:
        from tools.pr_governance_core.llm_summary import generate_advisory_llm_summary
        llm_summary = generate_advisory_llm_summary(
            branch, signals, list(file_stats.keys()), root=root
        )

    semantic_review, verdict, verdict_reason, recommended_action = _run_semantic_panel_review(
        branch=branch,
        base_branch=base_branch,
        root=root,
        has_files_changed=(total_files > 0),
        verdict=verdict,
        verdict_reason=verdict_reason,
        recommended_action=recommended_action,
        persist_receipt=persist_receipt,
    )

    review = _build_branch_code_review(
        branch=branch,
        signals=signals,
        verdict=verdict,
        verdict_reason=verdict_reason,
        recommended_action=recommended_action,
        evidence_digest=evidence_digest,
        llm_summary=llm_summary,
        semantic_review=semantic_review,
    )
    review.post_fork_regression_result = (anti_reg_ok, regressions)  # type: ignore[attr-defined]

    # Save to cache
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(review.to_dict(), f, indent=2)
    except Exception as e:
        logger.warning("Cache write failed for %s: %s", branch, e)

    return review
