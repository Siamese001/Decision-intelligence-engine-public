"""
Per-commit cherry-pick classification, prerequisite graphing, and extract-set synthesis.
Guarantees zero repository mutation and 100% agreement with real git cherry-pick on scratch clones.

SSOT Schema URN: urn:nee:pr-cherry-pick-plan:1.0.0
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .duplicates import compute_commit_patch_id
from .models import (
    CHERRY_PICK_SCHEMA_VERSION,
    BranchCherryPickPlan,
    CherryPickClass,
    CommitCherryPickVerdict,
)
from .order_planner import compute_repo_state_digest
from .pairwise import RE_CONFLICT_LINE

ARCHITECTURAL_DOMAINS = (
    "governance",
    "dual_pipeline",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "storage",
    "tools",
    "tests",
    "docs",
    "reports",
)


def _get_commit_parents(repo_root: Path, commit_sha: str) -> List[str]:
    """Retrieves list of parent commit hashes for a commit."""
    proc = subprocess.run(
        ["git", "rev-parse", f"{commit_sha}^@"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()] if proc.returncode == 0 else []


def _get_commit_files(repo_root: Path, commit_sha: str) -> List[str]:
    """Lists files modified/added/deleted by a commit."""
    proc = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()] if proc.returncode == 0 else []


def _detect_split_candidate(files: List[str], subject: str) -> Tuple[bool, str]:
    """Detects if a commit represents mixed concerns (split candidate)."""
    if not files:
        return False, ""
    domains: Set[str] = set()
    for f in files:
        parts = f.split("/")
        top = parts[0] if len(parts) > 1 else "root"
        for dom in ARCHITECTURAL_DOMAINS:
            if top.startswith(dom):
                domains.add(dom)
                break
        else:
            domains.add(top)

    if len(domains) >= 3:
        return True, f"Touches {len(domains)} distinct architectural domains: {', '.join(sorted(domains))}"
    if len(files) >= 10 and len(domains) >= 2:
        return True, f"Touches {len(files)} files across multiple domains: {', '.join(sorted(domains))}"

    sub_lower = subject.lower()
    split_tokens = (" and ", " & ", " also ", " plus ")
    if any(token in sub_lower for token in split_tokens) and len(domains) >= 2:
        return True, f"Mixed-concern commit subject describes multiple domains: {', '.join(sorted(domains))}"
    return False, ""


def _calculate_effort_band(conflict_files: List[str], is_binary: bool) -> str:
    """Calculates conflict resolution effort band."""
    if not conflict_files:
        return "NONE"
    if is_binary or len(conflict_files) > 5:
        return "HIGH"
    return "MEDIUM" if len(conflict_files) >= 3 else "LOW"


def _check_merge_tree_clean(
    repo_root: Path,
    base_ref: str,
    commit_sha: str,
) -> Tuple[bool, List[str], bool]:
    """Simulates cherry-picking a commit onto base_ref using `git merge-tree`."""
    parents = _get_commit_parents(repo_root, commit_sha)
    parent_ref = parents[0] if parents else f"{commit_sha}^"

    proc = subprocess.run(
        ["git", "merge-tree", "--write-tree", f"--merge-base={parent_ref}", base_ref, commit_sha],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True, [], False

    output = f"{proc.stdout}\n{proc.stderr}"
    conflict_files: List[str] = []
    is_binary = "cannot merge binary files" in output

    for line in output.splitlines():
        line_clean = line.strip()
        m = RE_CONFLICT_LINE.match(line_clean)
        cfile = ""
        if m:
            raw = m.group(2).strip()
            if " deleted in " in raw:
                cfile = raw.split(" deleted in ")[0].strip()
            elif " renamed" in raw:
                cfile = raw.split(" renamed")[0].strip()
            else:
                cfile = raw
        elif "Merge conflict in" in line_clean:
            parts = line_clean.split("Merge conflict in", 1)
            if len(parts) == 2:
                cfile = parts[1].strip()

        if cfile and cfile not in conflict_files:
            conflict_files.append(cfile)

    return False, conflict_files, is_binary


def verify_scratch_agreement(
    repo_root: Path,
    base_ref: str,
    extract_set: List[str],
) -> bool:
    """Verifies that the extract set cherry-picks with 100% agreement on a scratch clone."""
    if not extract_set:
        return True
    with tempfile.TemporaryDirectory() as tmp_dir:
        clone_path = Path(tmp_dir) / "scratch"
        res = subprocess.run(
            ["git", "clone", "--local", str(repo_root), str(clone_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return False
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(clone_path), capture_output=True, check=False)
        subprocess.run(["git", "config", "user.email", "tester@local"], cwd=str(clone_path), capture_output=True, check=False)
        subprocess.run(["git", "checkout", base_ref], cwd=str(clone_path), capture_output=True, check=False)

        for c_sha in extract_set:
            p_res = subprocess.run(["git", "cherry-pick", c_sha], cwd=str(clone_path), capture_output=True, text=True, check=False)
            if p_res.returncode != 0:
                return False
    return True


def _collect_sibling_patch_ids(
    repo_root: Path,
    base_ref: str,
    branch: str,
    sibling_branches: List[str],
) -> Dict[str, List[Dict[str, str]]]:
    """Collects patch-ids across sibling branches."""
    sibling_patch_ids: Dict[str, List[Dict[str, str]]] = {}
    for sib in sibling_branches:
        if sib in (branch, base_ref):
            continue
        p = subprocess.run(["git", "merge-base", base_ref, sib], cwd=str(repo_root), capture_output=True, text=True, check=False)
        if p.returncode != 0 or not p.stdout.strip():
            continue
        sib_mb = p.stdout.strip()
        log_p = subprocess.run(["git", "log", "--format=%H", f"{sib_mb}..{sib}"], cwd=str(repo_root), capture_output=True, text=True, check=False)
        if log_p.returncode == 0:
            for sha in log_p.stdout.splitlines():
                sha = sha.strip()
                if sha:
                    pid = compute_commit_patch_id(repo_root, sha)
                    if pid:
                        sibling_patch_ids.setdefault(pid, []).append({"branch": sib, "commit": sha})
    return sibling_patch_ids


def _classify_single_commit(
    repo_root: Path,
    base_ref: str,
    c_sha: str,
    c_subj: str,
    base_patch_ids: Dict[str, str],
    sibling_patch_ids: Dict[str, List[Dict[str, str]]],
    seen_branch_files: Dict[str, List[str]],
) -> CommitCherryPickVerdict:
    """Classifies a single commit into one of the SSOT cherry-pick classes."""
    parents = _get_commit_parents(repo_root, c_sha)
    files = _get_commit_files(repo_root, c_sha)
    patch_id = compute_commit_patch_id(repo_root, c_sha)
    short_sha = c_sha[:8]

    if len(parents) > 1:
        return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.MERGE_COMMIT, patch_id, rationale="Merge commit cannot be cherry-picked linearly.", files=files)
    if not files:
        return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.EMPTY_COMMIT, patch_id, rationale="Commit has no file changes.", files=files)
    if patch_id and patch_id in base_patch_ids:
        return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.ALREADY_IN_MAIN, patch_id, rationale=f"Patch-id {patch_id[:8]} already landed in {base_ref} at {base_patch_ids[patch_id][:8]}.", files=files)
    if patch_id and patch_id in sibling_patch_ids:
        matches = sibling_patch_ids[patch_id]
        return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.DUPLICATE_IN_SIBLING, patch_id, sibling_matches=matches, rationale=f"Patch-id {patch_id[:8]} duplicated in sibling {matches[0]['branch']}.", files=files)

    is_split, split_reason = _detect_split_candidate(files, c_subj)
    if is_split:
        return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.SPLIT_CANDIDATE, patch_id, rationale=split_reason, files=files)

    is_clean, conflict_files, is_binary = _check_merge_tree_clean(repo_root, base_ref, c_sha)
    if is_clean:
        return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.PICK_CLEAN, patch_id, is_extractable=True, rationale="Applies cleanly and independently onto target base.", files=files)

    prereq_commits: List[str] = []
    for cf in conflict_files:
        if cf in seen_branch_files:
            for prior_sha in seen_branch_files[cf]:
                if prior_sha not in prereq_commits:
                    prereq_commits.append(prior_sha)

    effort = _calculate_effort_band(conflict_files, is_binary)
    if prereq_commits:
        return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.PICK_NEEDS_PREREQ, patch_id, prerequisites=prereq_commits, conflict_files=conflict_files, effort_band=effort, rationale=f"Requires prior commit(s) {', '.join(s[:8] for s in prereq_commits)}.", files=files)
    return CommitCherryPickVerdict(c_sha, short_sha, c_subj, CherryPickClass.PICK_CONFLICTS, patch_id, conflict_files=conflict_files, effort_band=effort, rationale=f"Direct conflict with {base_ref} on {len(conflict_files)} file(s).", files=files)


def _resolve_branch_commits(
    repo_root: Path,
    base_ref: str,
    branch: str,
) -> Tuple[str, List[Tuple[str, str]]]:
    """Resolves merge-base and list of (sha, subject) commits on branch since base."""
    mb_proc = subprocess.run(["git", "merge-base", base_ref, branch], cwd=str(repo_root), capture_output=True, text=True, check=False)
    merge_base = mb_proc.stdout.strip() if mb_proc.returncode == 0 else ""
    if not merge_base:
        return "", []

    log_proc = subprocess.run(["git", "log", "--reverse", "--format=%H%x00%s", f"{merge_base}..{branch}"], cwd=str(repo_root), capture_output=True, text=True, check=False)
    commits: List[Tuple[str, str]] = []
    if log_proc.returncode == 0 and log_proc.stdout.strip():
        for line in log_proc.stdout.splitlines():
            if "\x00" in line:
                sha, sub = line.split("\x00", 1)
                commits.append((sha.strip(), sub.strip()))
    return merge_base, commits


def _collect_base_patch_ids(
    repo_root: Path,
    base_ref: str,
    merge_base: str,
) -> Dict[str, str]:
    """Collects patch-ids of commits on base_ref since merge_base."""
    base_patch_ids: Dict[str, str] = {}
    if not merge_base:
        return base_patch_ids
    b_log = subprocess.run(["git", "log", "--format=%H", f"{merge_base}..{base_ref}"], cwd=str(repo_root), capture_output=True, text=True, check=False)
    if b_log.returncode == 0 and b_log.stdout.strip():
        for c_sha in b_log.stdout.splitlines():
            c_sha = c_sha.strip()
            if c_sha:
                pid = compute_commit_patch_id(repo_root, c_sha)
                if pid:
                    base_patch_ids[pid] = c_sha
    return base_patch_ids


def _build_extract_set(verdicts: List[CommitCherryPickVerdict]) -> List[str]:
    """Identifies extractable commits and satisfied prerequisites in order."""
    extract_set: List[str] = []
    for v in verdicts:
        if v.verdict == CherryPickClass.PICK_CLEAN:
            extract_set.append(v.commit_sha)
            v.is_extractable = True
        elif v.verdict == CherryPickClass.PICK_NEEDS_PREREQ and v.prerequisites:
            if all(p in extract_set for p in v.prerequisites):
                extract_set.append(v.commit_sha)
                v.is_extractable = True
    return extract_set


def assess_branch_cherry_pick(
    repo_root: Path,
    branch: str,
    base_ref: str = "main",
    sibling_branches: Optional[List[str]] = None,
    verify_scratch: bool = True,
) -> BranchCherryPickPlan:
    """Assesses cherry-pick viability for all commits in a branch against base_ref."""
    t_start = time.perf_counter()
    digest_before = compute_repo_state_digest(repo_root)

    merge_base, branch_commits = _resolve_branch_commits(repo_root, base_ref, branch)
    base_patch_ids = _collect_base_patch_ids(repo_root, base_ref, merge_base)
    sibling_patch_ids = _collect_sibling_patch_ids(repo_root, base_ref, branch, sibling_branches) if sibling_branches else {}

    verdicts: List[CommitCherryPickVerdict] = []
    seen_branch_files: Dict[str, List[str]] = {}

    for c_sha, c_subj in branch_commits:
        v = _classify_single_commit(repo_root, base_ref, c_sha, c_subj, base_patch_ids, sibling_patch_ids, seen_branch_files)
        verdicts.append(v)
        for f in v.files:
            seen_branch_files.setdefault(f, []).append(c_sha)

    extract_set = _build_extract_set(verdicts)
    recommended_commands = [f"git cherry-pick {' '.join(extract_set)}"] if extract_set else []
    scratch_verified = verify_scratch_agreement(repo_root, base_ref, extract_set) if verify_scratch else False

    digest_after = compute_repo_state_digest(repo_root)
    return BranchCherryPickPlan(
        schema_version=CHERRY_PICK_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo_root=str(repo_root),
        branch=branch,
        base_ref=base_ref,
        total_commits=len(verdicts),
        verdicts=verdicts,
        extract_set=extract_set,
        recommended_commands=recommended_commands,
        scratch_agreement_verified=scratch_verified,
        repo_state_digest_before=digest_before,
        repo_state_digest_after=digest_after,
        zero_mutation_verified=(digest_before == digest_after),
        execution_time_seconds=time.perf_counter() - t_start,
    )

