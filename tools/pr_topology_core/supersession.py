"""
Supersession and Evidence Engine for PR Queue Governance.

Determines whether a branch or worktree is completely or partially superseded
by main or another branch, providing concrete cryptographic and topological proof:
- Commit layer: Ancestor-of-main, patch-id equivalence, git cherry, merge-tree no-op
- Dirty layer: Exact blob matching, mode/symlink verification, residual file detection
- Chain resolution: Following supersession chains to final healthy survivor
- Tie / cycle gating: Detect mutual supersession or ties and escalate to operator
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.pr_governance_core.common import ROOT, run_git


@dataclass
class SupersessionTarget:
    kind: str  # "main" | "branch" | "none"
    ref: str  # e.g. "main", "feat/branch-name"
    sha: str  # e.g. "7f0258dc"
    confidence: str = "HIGH"  # "HIGH" | "MEDIUM" | "LOW"
    reason: str = ""
    chain: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SupersessionEvidence:
    layer: str  # "commit" | "dirty"
    evidence_type: str  # "ANCESTOR", "PATCH_ID", "MERGE_NOOP", "BLOB_SAME_PATH", "PARTIAL", "TIE", "NONE"
    target_ref: str
    target_sha: str
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    description: str
    files_covered: List[str] = field(default_factory=list)
    residual_files: List[str] = field(default_factory=list)
    commit_mappings: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BranchSupersessionResult:
    branch: str
    tip_sha: str
    main_sha: str
    is_superseded: bool
    superseded_by: Optional[SupersessionTarget] = None
    evidence: List[SupersessionEvidence] = field(default_factory=list)
    unique_commits: List[str] = field(default_factory=list)
    unique_dirty_files: List[str] = field(default_factory=list)
    tie_group: List[str] = field(default_factory=list)
    is_partial: bool = False
    residual_files: List[str] = field(default_factory=list)
    health: str = "HEALTHY"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch,
            "tip_sha": self.tip_sha,
            "main_sha": self.main_sha,
            "is_superseded": self.is_superseded,
            "superseded_by": self.superseded_by.to_dict() if self.superseded_by else None,
            "evidence": [e.to_dict() for e in self.evidence],
            "unique_commits": self.unique_commits,
            "unique_dirty_files": self.unique_dirty_files,
            "tie_group": self.tie_group,
            "is_partial": self.is_partial,
            "residual_files": self.residual_files,
            "health": self.health,
        }


def is_ancestor(ancestor: str, descendant: str, root: Path = ROOT) -> bool:
    """Returns True if ancestor is an ancestor of descendant."""
    res = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


def take_repository_snapshot(root: Path = ROOT) -> Dict[str, Any]:
    """Captures sorted refs, main sha, and worktree states to guarantee determinism."""
    refs_out = run_git(["for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"], cwd=root)
    branch_refs: Dict[str, str] = {}
    for line in refs_out.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            branch_refs[parts[0]] = parts[1]

    main_sha = branch_refs.get("main", "")
    if not main_sha:
        main_sha = run_git(["rev-parse", "main"], cwd=root).strip()

    main_tree = run_git(["rev-parse", "main^{tree}"], cwd=root).strip()
    wt_raw = run_git(["worktree", "list", "--porcelain"], cwd=root)

    digest_input = f"{sorted(branch_refs.items())}:{main_sha}:{main_tree}:{wt_raw}"
    snapshot_digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    return {
        "branch_refs": branch_refs,
        "main_sha": main_sha,
        "main_tree": main_tree,
        "snapshot_digest": snapshot_digest,
    }


def build_main_patch_id_index(
    root: Path = ROOT, base_branch: str = "main", max_commits: int = 200
) -> Dict[str, str]:
    """
    Builds a patch-id index over recent non-merge commits on main.
    Returns mapping: {patch_id: commit_sha}.
    """
    cmd = (
        f"git log -p --no-merges -n {max_commits} --format='commit %H' {base_branch} "
        f"| git patch-id --stable"
    )
    res = subprocess.run(
        cmd,
        cwd=root,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    idx: Dict[str, str] = {}
    if res.returncode == 0 and res.stdout:
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                idx[parts[0]] = parts[1]
    return idx


def _find_squash_merge_commit_on_main(
    branch: str, merge_base: str, root: Path, base_branch: str = "main"
) -> str:
    """Finds the earliest commit on main since merge_base where branch content is fully absorbed."""
    raw_revs = run_git(
        ["rev-list", "--reverse", f"{merge_base}..{base_branch}"], cwd=root
    ).splitlines()
    rev_list = [c.strip() for c in raw_revs if c.strip()]
    if not rev_list:
        return ""

    low = 0
    high = len(rev_list) - 1
    found_idx = -1

    while low <= high:
        mid = (low + high) // 2
        c_sha = rev_list[mid]
        c_tree = run_git(["rev-parse", f"{c_sha}^{{tree}}"], cwd=root).strip()
        mt_proc = subprocess.run(
            ["git", "merge-tree", "--write-tree", c_sha, branch],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if mt_proc.returncode == 0 and mt_proc.stdout.strip() == c_tree:
            found_idx = mid
            high = mid - 1
        else:
            low = mid + 1

    if found_idx != -1:
        return rev_list[found_idx][:8]
    return ""


def _evaluate_ancestor_supersession(
    branch: str,
    branch_sha: str,
    main_sha: str,
    root: Path,
    base_branch: str,
) -> Tuple[Optional[SupersessionTarget], List[SupersessionEvidence]]:
    """Evaluates ancestor supersession with inception immunity and merge receipt verification."""
    from tools.pr_governance_core.common import slugify_branch
    from tools.pr_governance_core.merge_proof import is_second_parent_of_merge_on_main

    merge_base = run_git(["merge-base", base_branch, branch], cwd=root).strip()
    slug = slugify_branch(branch)
    merge_receipt = root / "reports" / f"pr_merge_{slug}_receipt.json"
    has_merge_commit = is_second_parent_of_merge_on_main(branch_sha, branch_name=branch, root=root, main_ref=base_branch)
    has_receipt = merge_receipt.exists()
    has_proof = has_receipt or has_merge_commit

    if merge_base == branch_sha and not has_proof:
        return None, []

    from tools.pr_topology_core.birth import inspect_branch_birth
    birth_info = inspect_branch_birth(branch, root=root)
    if birth_info.is_inception_active and not has_proof:
        return None, []

    first_commit = ""
    ancestry = run_git(
        ["rev-list", "--ancestry-path", "--reverse", f"{branch}..{base_branch}"],
        cwd=root,
    ).splitlines()
    first_commit = ancestry[0].strip()[:8] if ancestry else branch_sha[:8]

    conf = "HIGH" if has_proof else "LOW"
    if has_receipt:
        reason = f"Branch merged into {base_branch} (verified by receipt)"
    elif has_merge_commit:
        reason = f"Branch merged into {base_branch} (verified by merge commit)"
    else:
        reason = f"Branch commit {branch_sha[:8]} is ancestor of {base_branch}@{first_commit or main_sha[:8]}"

    target = SupersessionTarget(
        kind="main",
        ref=base_branch,
        sha=first_commit or main_sha[:8],
        confidence=conf,
        reason=reason,
    )
    evidence = [
        SupersessionEvidence(
            layer="commit",
            evidence_type="ANCESTOR",
            target_ref=base_branch,
            target_sha=target.sha,
            confidence=conf,
            description=f"Branch commit {branch_sha[:8]} is ancestor of {base_branch}@{target.sha}",
        )
    ]
    return target, evidence


def _parse_cherry_commits(
    cherry_out: str, patch_idx: Dict[str, str], root: Path
) -> Tuple[List[str], Dict[str, str]]:
    """Parses git cherry output and maps patch-equivalent commits."""
    unique_commits: List[str] = []
    mapped_commits: Dict[str, str] = {}
    for line in cherry_out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        flag = parts[0]
        c_sha = parts[1] if len(parts) > 1 else ""
        if flag == "-":
            main_commit = patch_idx.get(c_sha, "")
            if not main_commit:
                pid_proc = subprocess.run(
                    f"git show {c_sha} | git patch-id --stable",
                    cwd=root,
                    shell=True,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if pid_proc.returncode == 0 and pid_proc.stdout:
                    pid = pid_proc.stdout.split()[0]
                    main_commit = patch_idx.get(pid, "")
            if main_commit:
                mapped_commits[c_sha[:8]] = main_commit[:8]
        elif flag == "+":
            unique_commits.append(c_sha[:8])
    return unique_commits, mapped_commits


def evaluate_commit_supersession(
    branch: str,
    branch_sha: str,
    main_sha: str,
    main_tree: str,
    patch_idx: Dict[str, str],
    root: Path = ROOT,
    base_branch: str = "main",
) -> Tuple[Optional[SupersessionTarget], List[SupersessionEvidence], List[str]]:
    """Evaluates whether branch's commits are absorbed into main."""
    try:
        run_git(["rev-parse", "--verify", branch], cwd=root, check=True)
    except Exception:
        return None, [], []

    # 1. Ancestor check
    if is_ancestor(branch, base_branch, root):
        target, evidence = _evaluate_ancestor_supersession(
            branch, branch_sha, main_sha, root, base_branch
        )
        if target:
            return target, evidence, []
        return None, [], []

    # 2. Check git cherry and patch-id
    cherry_out = run_git(["cherry", "-v", base_branch, branch], cwd=root)
    unique_commits, mapped_commits = _parse_cherry_commits(cherry_out, patch_idx, root)

    # 3. If all commits were marked '-' by cherry, verify content tree equality
    if not unique_commits:
        mt_proc = subprocess.run(
            ["git", "merge-tree", "--write-tree", base_branch, branch],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if mt_proc.returncode == 0 and mt_proc.stdout.strip() == main_tree:
            merge_base = run_git(["merge-base", base_branch, branch], cwd=root).strip()
            squash_commit = _find_squash_merge_commit_on_main(
                branch, merge_base, root, base_branch
            )
            valid_mapped = [v for v in mapped_commits.values() if v and v != base_branch]
            if valid_mapped and len(valid_mapped) == len(mapped_commits):
                latest_mapped = valid_mapped[-1]
                target = SupersessionTarget(
                    kind="main",
                    ref=base_branch,
                    sha=latest_mapped,
                    confidence="HIGH",
                    reason=f"All commits patch-equivalent to {base_branch} and tree matches",
                )
                evidence = [
                    SupersessionEvidence(
                        layer="commit",
                        evidence_type="PATCH_ID",
                        target_ref=base_branch,
                        target_sha=target.sha,
                        confidence="HIGH",
                        description=f"All commits patch-equivalent to {base_branch} and merge tree identical to {base_branch}^{{tree}}",
                        commit_mappings=mapped_commits,
                    )
                ]
                return target, evidence, []
            else:
                target_sha = squash_commit or (valid_mapped[-1] if valid_mapped else main_sha[:8])
                target = SupersessionTarget(
                    kind="main",
                    ref=base_branch,
                    sha=target_sha,
                    confidence="HIGH",
                    reason=f"Branch content absorbed into {base_branch}@{target_sha}",
                )
                evidence = [
                    SupersessionEvidence(
                        layer="commit",
                        evidence_type="MERGE_NOOP",
                        target_ref=base_branch,
                        target_sha=target_sha,
                        confidence="HIGH",
                        description=f"Merge tree identical to {base_branch}^{{tree}}; absorbed into {base_branch}@{target_sha}",
                        commit_mappings=mapped_commits,
                    )
                ]
                return target, evidence, []
        else:
            # Tree differed (e.g. whitespace/indentation differences, or context mismatch)
            evidence = [
                SupersessionEvidence(
                    layer="commit",
                    evidence_type="PARTIAL",
                    target_ref=base_branch,
                    target_sha=main_sha[:8],
                    confidence="LOW",
                    description="Commits match patch-id but merge tree differs from main (whitespace or semantic difference)",
                    commit_mappings=mapped_commits,
                )
            ]
            return None, evidence, [branch_sha[:8]]

    # 4. Check squash merge no-op
    mt_proc = subprocess.run(
        ["git", "merge-tree", "--write-tree", base_branch, branch],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if mt_proc.returncode == 0 and mt_proc.stdout.strip() == main_tree:
        merge_base = run_git(["merge-base", base_branch, branch], cwd=root).strip()
        squash_commit = _find_squash_merge_commit_on_main(
            branch, merge_base, root, base_branch
        )
        target_sha = squash_commit or main_sha[:8]
        target = SupersessionTarget(
            kind="main",
            ref=base_branch,
            sha=target_sha,
            confidence="HIGH",
            reason=f"Branch content squash-merged into {base_branch}@{target_sha}",
        )
        evidence = [
            SupersessionEvidence(
                layer="commit",
                evidence_type="MERGE_NOOP",
                target_ref=base_branch,
                target_sha=target_sha,
                confidence="HIGH",
                description=f"Merge tree identical to {base_branch}^{{tree}}; squash-merged into {base_branch}@{target_sha}",
                commit_mappings=mapped_commits,
            )
        ]
        return target, evidence, []

    evidence = []
    if mapped_commits:
        evidence.append(
            SupersessionEvidence(
                layer="commit",
                evidence_type="PARTIAL",
                target_ref=base_branch,
                target_sha=main_sha[:8],
                confidence="MEDIUM",
                description=f"{len(mapped_commits)} commit(s) absorbed in {base_branch}, {len(unique_commits)} unique commit(s)",
                commit_mappings=mapped_commits,
            )
        )

    return None, evidence, unique_commits


_TREE_FILES_CACHE: Dict[str, Set[str]] = {}
_B2B_SUPER_CACHE: Dict[Tuple[str, str], Tuple[bool, Optional[SupersessionEvidence], List[str]]] = {}


def get_tree_files(tree_sha: str, root: Path = ROOT) -> Set[str]:
    """Caches all file paths in a tree to avoid repeated subprocess invocations."""
    if tree_sha not in _TREE_FILES_CACHE:
        try:
            out = run_git(["ls-tree", "-r", "--name-only", tree_sha], cwd=root)
            _TREE_FILES_CACHE[tree_sha] = set(out.splitlines())
        except Exception:
            _TREE_FILES_CACHE[tree_sha] = set()
    return _TREE_FILES_CACHE[tree_sha]


def evaluate_branch_to_branch_supersession(
    b1: str,
    b1_sha: str,
    b2: str,
    b2_sha: str,
    b1_files: List[str],
    b2_files: List[str],
    root: Path = ROOT,
    base_branch: str = "main",
) -> Tuple[bool, Optional[SupersessionEvidence], List[str]]:
    """Evaluates whether b1's changes are completely absorbed into candidate b2."""
    if b1 == b2:
        return False, None, []

    cache_key = (b1_sha, b2_sha)
    if cache_key in _B2B_SUPER_CACHE:
        return _B2B_SUPER_CACHE[cache_key]

    s1 = set(b1_files)
    s2 = set(b2_files)
    if not s1.intersection(s2):
        res = (False, None, [])
        _B2B_SUPER_CACHE[cache_key] = res
        return res

    b2_tree = run_git(["rev-parse", f"{b2}^{{tree}}"], cwd=root).strip()
    b1_tree = run_git(["rev-parse", f"{b1}^{{tree}}"], cwd=root).strip()

    # Check mutual equivalence (cycle / tie)
    if b1_tree == b2_tree:
        tie_evidence = SupersessionEvidence(
            layer="commit",
            evidence_type="TIE",
            target_ref=b2,
            target_sha=b2_sha[:8],
            confidence="LOW",
            description=f"Mutual tree equivalence between {b1} and {b2}; operator review required",
        )
        res = (False, tie_evidence, [])
        _B2B_SUPER_CACHE[cache_key] = res
        return res

    # 3-way merge between base(b1, base_branch), ours(b2), and theirs(b1)
    base_sha = ""
    try:
        base_proc = subprocess.run(
            ["git", "merge-base", base_branch, b1],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if base_proc.returncode == 0:
            base_sha = base_proc.stdout.strip()
    except Exception:
        pass

    cmd = ["git", "merge-tree", "--write-tree"]
    if base_sha:
        cmd.append(f"--merge-base={base_sha}")
    cmd.extend([b2, b1])

    mt_proc = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if mt_proc.returncode != 0:
        res = (False, None, list(s1))
        _B2B_SUPER_CACHE[cache_key] = res
        return res

    merged_tree = mt_proc.stdout.strip()
    if merged_tree == b2_tree:
        # Extra safety check: verify that files introduced by b1 still exist in b2
        b2_tree_files = get_tree_files(b2_tree, root)
        missing_files = [f for f in s1 if f not in b2_tree_files]
        if missing_files:
            partial_evidence = SupersessionEvidence(
                layer="commit",
                evidence_type="PARTIAL",
                target_ref=b2,
                target_sha=b2_sha[:8],
                confidence="LOW",
                description=f"{b1} has files deleted or reverted in {b2}: {missing_files}",
                files_covered=sorted(list(s1 - set(missing_files))),
                residual_files=missing_files,
            )
            res = (False, partial_evidence, missing_files)
            _B2B_SUPER_CACHE[cache_key] = res
            return res

        evidence_type = "ANCESTOR_OF_BRANCH" if is_ancestor(b1, b2, root) else "MERGE_NOOP"
        evidence = SupersessionEvidence(
            layer="commit",
            evidence_type=evidence_type,
            target_ref=b2,
            target_sha=b2_sha[:8],
            confidence="HIGH",
            description=f"{b1} content completely absorbed into {b2}@{b2_sha[:8]}",
            files_covered=sorted(list(s1)),
        )
        res = (True, evidence, [])
        _B2B_SUPER_CACHE[cache_key] = res
        return res

    # Residual files: files modified when merging b1 into b2
    diff_tree_out = run_git(
        ["diff-tree", "--name-only", "-r", b2_tree, merged_tree], cwd=root
    )
    residual_files = [f.strip() for f in diff_tree_out.splitlines() if f.strip()]
    files_covered = sorted(list(s1.difference(residual_files)))

    partial_evidence = SupersessionEvidence(
        layer="commit",
        evidence_type="PARTIAL",
        target_ref=b2,
        target_sha=b2_sha[:8],
        confidence="LOW",
        description=f"Partial absorption into {b2}: {len(files_covered)} files covered, {len(residual_files)} residual files",
        files_covered=files_covered,
        residual_files=residual_files,
    )
    res = (False, partial_evidence, residual_files)
    _B2B_SUPER_CACHE[cache_key] = res
    return res


_TARGET_TREE_CACHE: Dict[Tuple[str, Path], Dict[str, Tuple[str, str]]] = {}


def get_target_tree_map(target_ref: str, root: Path = ROOT) -> Dict[str, Tuple[str, str]]:
    """Returns mapping {path: (mode, sha)} for target_ref to avoid thousands of git subprocesses."""
    key = (target_ref, root)
    if key not in _TARGET_TREE_CACHE:
        proc = subprocess.run(
            ["git", "ls-tree", "-r", "-z", target_ref],
            cwd=root,
            capture_output=True,
            check=False,
        )
        mapping = {}
        if proc.returncode == 0 and proc.stdout:
            for item in proc.stdout.split(b"\0"):
                if not item:
                    continue
                try:
                    meta, path_b = item.split(b"\t", 1)
                    parts = meta.split(b" ")
                    mode_b, sha_b = parts[0], parts[2]
                    mapping[path_b.decode("utf-8", errors="replace")] = (
                        mode_b.decode("ascii"),
                        sha_b.decode("ascii"),
                    )
                except Exception:
                    continue
        _TARGET_TREE_CACHE[key] = mapping
    return _TARGET_TREE_CACHE[key]


def compute_git_blob_hash(file_path: Path) -> str:
    """Computes the git sha1 blob hash in pure python without spawning a git hash-object process."""
    try:
        content = file_path.read_bytes()
        header = f"blob {len(content)}\0".encode("ascii")
        return hashlib.sha1(header + content).hexdigest()
    except Exception:
        return ""


def _audit_single_dirty_entry(
    entry: Dict[str, Any],
    wt_path: Path,
    target_ref: str,
    root: Path,
    target_tree_map: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Tuple[bool, str]:
    """Audits one dirty entry against target_ref. Returns (is_covered, path)."""
    path_str = entry.get("path", "")
    file_path = wt_path / path_str

    if target_tree_map is None:
        target_tree_map = get_target_tree_map(target_ref, root)

    target_entry = target_tree_map.get(path_str)

    if not file_path.exists():
        return (target_entry is None, path_str)

    if file_path.is_symlink():
        try:
            target_link = os.readlink(file_path)
            ref_blob = run_git(["show", f"{target_ref}:{path_str}"], cwd=root).strip()
            return (target_link == ref_blob, path_str)
        except Exception:
            return (False, path_str)

    if target_entry is None:
        return (False, path_str)

    target_mode, target_sha = target_entry
    local_sha = compute_git_blob_hash(file_path)
    if local_sha and local_sha == target_sha:
        try:
            file_mode = oct(os.stat(file_path).st_mode)[-3:]
            target_oct_mode = target_mode[-3:]
            if not target_oct_mode or file_mode == target_oct_mode:
                return (True, path_str)
        except Exception:
            return (True, path_str)

    return (False, path_str)


def _audit_dirty_code_hunk_absorption(
    wt_path: Path,
    rel_path: str,
    target_ref: str,
    root: Path,
) -> bool:
    """Checks whether added lines in a dirty file are already absorbed in target_ref:<rel_path>."""
    full_p = wt_path / rel_path
    if not full_p.is_file() or rel_path not in get_target_tree_map(target_ref, root):
        return False
    try:
        target_show = subprocess.run(
            ["git", "show", f"{target_ref}:{rel_path}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if target_show.returncode != 0 or not target_show.stdout:
            return False
        target_content = target_show.stdout
        proc = subprocess.run(
            ["git", "diff", target_ref, "--", rel_path],
            cwd=wt_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            return False
        diff_text = proc.stdout
        if diff_text.strip():
            added_lines = [
                line[1:].strip()
                for line in diff_text.splitlines()
                if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
            ]
        else:
            local_text = full_p.read_text(encoding="utf-8", errors="replace")
            if local_text == target_content:
                return True
            added_lines = [line.strip() for line in local_text.splitlines() if line.strip()]
        if not added_lines:
            return False
        absorbed_count = sum(1 for line in added_lines if line in target_content)
        return (absorbed_count / len(added_lines)) >= 0.85
    except Exception:
        return False


def _collect_dirty_and_residual_files(
    wt_path: Path,
    target_ref: str,
    root: Path,
    ignore_review_artifacts: bool,
) -> Tuple[List[str], List[str], List[str]]:
    from tools.pr_governance_core.worktree import (
        is_ephemeral_worktree_path,
        find_valuable_ignored_files,
        parse_porcelain_v2_z,
    )

    val_ignored = find_valuable_ignored_files(wt_path)
    if ignore_review_artifacts:
        val_ignored = [v for v in val_ignored if not is_ephemeral_worktree_path(v)]
    residual_files: List[str] = list(val_ignored)

    proc = subprocess.run(
        ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
        cwd=wt_path,
        capture_output=True,
        check=False,
    )
    entries: List[Dict[str, Any]] = []
    if proc.returncode == 0 and proc.stdout:
        entries = parse_porcelain_v2_z(proc.stdout)
        if ignore_review_artifacts:
            entries = [
                e
                for e in entries
                if not is_ephemeral_worktree_path(e.get("path", ""))
            ]

    covered_files: List[str] = []
    diff_proc = subprocess.run(
        ["git", "diff", "--name-only", target_ref],
        cwd=wt_path,
        capture_output=True,
        text=True,
        check=False,
    )
    diff_set = set(diff_proc.stdout.splitlines()) if diff_proc.returncode == 0 else None
    target_tree_map = get_target_tree_map(target_ref, root)

    for entry in entries:
        p = entry.get("path", "")
        e_type = entry.get("type", "")
        if diff_set is not None and e_type in ("1", "2", "u"):
            if p not in diff_set or _audit_dirty_code_hunk_absorption(wt_path, p, target_ref, root):
                covered_files.append(p)
            elif p not in residual_files:
                residual_files.append(p)
            continue

        is_covered, p = _audit_single_dirty_entry(
            entry, wt_path, target_ref, root, target_tree_map=target_tree_map
        )
        if is_covered or _audit_dirty_code_hunk_absorption(wt_path, p, target_ref, root):
            covered_files.append(p)
        elif p not in residual_files:
            residual_files.append(p)

    return covered_files, residual_files, val_ignored


def evaluate_dirty_file_coverage(
    wt_path: Path,
    target_ref: str,
    root: Path = ROOT,
    ignore_review_artifacts: bool = True,
) -> Tuple[bool, List[SupersessionEvidence], List[str]]:
    """Audits whether all authored uncommitted modifications in wt_path match target_ref."""
    if not wt_path.exists():
        return True, [], []

    covered_files, residual_files, val_ignored = _collect_dirty_and_residual_files(
        wt_path, target_ref, root, ignore_review_artifacts
    )

    if not residual_files:
        evidence = [
            SupersessionEvidence(
                layer="dirty",
                evidence_type="BLOB_SAME_PATH",
                target_ref=target_ref,
                target_sha="",
                confidence="HIGH",
                description="All uncommitted authored files match target tip blobs or are absorbed",
                files_covered=covered_files,
            )
        ]
        return True, evidence, []

    from tools.pr_governance_core.live_meeting_bridge import (
        evaluate_worktree_meeting_supersession,
    )

    all_dirty_files = covered_files + residual_files
    meeting_sup = evaluate_worktree_meeting_supersession(wt_path, all_dirty_files, root)
    if meeting_sup and meeting_sup.get("is_superseded"):
        from tools.pr_governance_core.diff_semantics import is_deliverable_file
        deliv_residual = [f for f in residual_files if is_deliverable_file(f)]
        code_residual = [f for f in residual_files if not is_deliverable_file(f)]
        c_dir = meeting_sup["canonical_dir"]
        w_state = meeting_sup["worktree_state"]
        covered_by_meeting = covered_files + deliv_residual
        if not code_residual:
            evidence = [
                SupersessionEvidence(
                    layer="dirty",
                    evidence_type="CANONICAL_MEETING_SUPERSEDED",
                    target_ref=c_dir,
                    target_sha="",
                    confidence="HIGH",
                    description=(
                        f"Superseded by PACKAGE_READY deliverable on {target_ref} "
                        f"under {c_dir}/ ({w_state})"
                    ),
                    files_covered=covered_by_meeting,
                )
            ]
            return True, evidence, []
        else:
            evidence = [
                SupersessionEvidence(
                    layer="dirty",
                    evidence_type="CANONICAL_MEETING_SUPERSEDED",
                    target_ref=c_dir,
                    target_sha="",
                    confidence="HIGH",
                    description=(
                        f"Deliverables superseded by PACKAGE_READY on {target_ref} "
                        f"under {c_dir}/ ({w_state}), but {len(code_residual)} uncommitted code file(s) remain"
                    ),
                    files_covered=covered_by_meeting,
                    residual_files=code_residual,
                )
            ]
            return False, evidence, code_residual

    if meeting_sup and meeting_sup.get("is_salvageable"):
        w_state = meeting_sup["worktree_state"]
        evidence = [
            SupersessionEvidence(
                layer="dirty",
                evidence_type="SALVAGEABLE_DELIVERABLES",
                target_ref=target_ref,
                target_sha="",
                confidence="MEDIUM",
                description=f"Salvageable deliverables ({w_state})",
                files_covered=covered_files,
                residual_files=residual_files,
            )
        ]
        return False, evidence, residual_files

    desc = f"{len(covered_files)} dirty files covered, {len(residual_files)} unique dirty files"
    if val_ignored:
        desc += f" (including {len(val_ignored)} valuable ignored file(s))"

    evidence = [
        SupersessionEvidence(
            layer="dirty",
            evidence_type="PARTIAL",
            target_ref=target_ref,
            target_sha="",
            confidence="LOW",
            description=desc,
            files_covered=covered_files,
            residual_files=residual_files,
        )
    ]
    return False, evidence, residual_files


def resolve_supersession_chains(
    direct_targets: Dict[str, SupersessionTarget],
    branch_health: Dict[str, str],
) -> Dict[str, Optional[SupersessionTarget]]:
    """
    Follows supersession chains (A -> B -> C) to the final healthy survivor.
    Rejects cycles (A -> B -> A) and chains leading to unhealthy/conflicted branches.
    """
    resolved: Dict[str, Optional[SupersessionTarget]] = {}

    for branch, target in direct_targets.items():
        curr = branch
        chain = [curr]
        chain_target: Optional[SupersessionTarget] = target

        while curr in direct_targets:
            curr_target = direct_targets[curr]
            if curr_target.kind == "main":
                chain_target = curr_target
                break
            nxt = curr_target.ref
            if nxt in chain:
                # Cycle detected
                chain_target = None
                break
            chain.append(nxt)
            curr = nxt
            chain_target = curr_target

        if chain_target and chain_target.kind != "main":
            # Check terminal branch health
            health = branch_health.get(curr, "HEALTHY")
            if health != "HEALTHY":
                chain_target = None

        if chain_target:
            resolved[branch] = SupersessionTarget(
                kind=chain_target.kind,
                ref=chain_target.ref,
                sha=chain_target.sha,
                confidence=chain_target.confidence,
                reason=chain_target.reason,
                chain=chain,
            )
        else:
            resolved[branch] = None

    return resolved
