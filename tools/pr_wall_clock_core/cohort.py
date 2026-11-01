"""Cohort definitions and Git reconciliation for PR promotion wall clock analysis."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ORIGINAL_CUTOFF_SHA = "b12e3edf914d22562b84b65be0e0d19213b444e9"
OPTIMIZATION_MERGE_SHA = "e037b5e4141736c64a19530d96977dc9955d6c10"
OPTIMIZATION_BRANCH = "docs/pr-local-main-latency-plan-20261007"

PRE_OPTIMIZATION_PRS: Tuple[Tuple[str, str], ...] = (
    ("docs/concise-runtime-output-plan-20261007", "8e9ca876a5b8b1e21cf55014244b9b712588057d"),
    ("docs/antigravity-dual-mode-restoration-rca", "89b911d2f49f1701968aae24aa33e3076130c899"),
    ("docs/agent-configuration-streamlining-plan", "8fd6d730d398b9d7145a597df09c880094ea45fd"),
    ("docs/retrieval-calibration-plan", "b3d3afe162fb10215dd9565fdefe461edb3876a8"),
    ("feat/hitl-margin-autonomous-bypass-20261006", "e11bb6f2846e1db54982d1394023dbe75b761cf7"),
)

POST_OPTIMIZATION_PRS: Tuple[Tuple[str, str], ...] = (
    ("docs/pr-queue-dual-mode-refactor-plan", "3d8d4cf776f19ad49cb3f9aea73c03854b5dc3e9"),
    ("docs/prompt-length-fidelity-assessment", "74bc42cb3174f521a7f895a005e3a95521d72e94"),
    ("docs/module-budget-review-50-prs", "0dabe1db56a1a3c1eff5828dee4dedb214615027"),
    ("docs/scheduled-task-gates-nuclear-rca-20261007", "45b10fb983e2d0fb4b6c9573787a25733e60d94e"),
    ("feat/lens-phase1-sow-closeout-pipeline", "b12e3edf914d22562b84b65be0e0d19213b444e9"),
)

QUEUE_PR_BRANCH = "docs/pr-queue-dual-mode-refactor-plan"
QUEUE_PR_PRE_RELEASE_RUN_ID = "pr-review-c815e5606e24"


@dataclass(frozen=True)
class GitMergeRecord:
    commit_sha: str
    short_sha: str
    branch: str
    date_iso: str
    subject: str
    parent_shas: List[str]


def parse_git_merge_line(line: str) -> Optional[GitMergeRecord]:
    parts = line.strip().split("\t")
    if len(parts) < 4:
        return None
    full_sha, parents, date_iso, subject = parts[0], parts[1].split(), parts[2], parts[3]
    short_sha = full_sha[:9]
    # Extract branch name from message
    branch = ""
    if "Merge branch '" in subject:
        start = subject.find("Merge branch '") + len("Merge branch '")
        end = subject.find("'", start)
        if end != -1:
            branch = subject[start:end]
    return GitMergeRecord(
        commit_sha=full_sha,
        short_sha=short_sha,
        branch=branch,
        date_iso=date_iso,
        subject=subject,
        parent_shas=parents,
    )


def get_first_parent_merges(
    repo_root: Path,
    rev_range: Optional[str] = None,
    max_count: Optional[int] = None,
) -> List[GitMergeRecord]:
    cmd = ["git", "log", "--merges", "--first-parent", "--pretty=format:%H\t%P\t%cI\t%s"]
    if max_count:
        cmd.extend(["-n", str(max_count)])
    if rev_range:
        cmd.append(rev_range)
    try:
        out = subprocess.check_output(cmd, cwd=str(repo_root), text=True)
    except Exception as exc:
        return []
    records = []
    for line in out.splitlines():
        rec = parse_git_merge_line(line)
        if rec:
            records.append(rec)
    return records


def reconcile_original_cohort_to_git(repo_root: Path) -> Dict[str, GitMergeRecord]:
    merges = get_first_parent_merges(repo_root, max_count=50)
    by_sha = {m.commit_sha: m for m in merges}
    by_short = {m.short_sha: m for m in merges}
    reconciled = {}
    for branch, full_sha in PRE_OPTIMIZATION_PRS + POST_OPTIMIZATION_PRS + (
        (OPTIMIZATION_BRANCH, OPTIMIZATION_MERGE_SHA),
    ):
        rec = by_sha.get(full_sha) or by_short.get(full_sha[:9])
        if not rec:
            cmd = ["git", "show", "-s", "--pretty=format:%H\t%P\t%cI\t%s", full_sha]
            try:
                line = subprocess.check_output(cmd, cwd=str(repo_root), text=True).strip()
                rec = parse_git_merge_line(line)
            except Exception:
                rec = None
        if not rec:
            raise ValueError(f"Failed to reconcile merge commit {full_sha} for branch {branch}")
        reconciled[branch] = rec
    return reconciled


def get_subsequent_merges(repo_root: Path, new_cutoff_ref: str = "main") -> List[GitMergeRecord]:
    rev_range = f"{ORIGINAL_CUTOFF_SHA}..{new_cutoff_ref}"
    return get_first_parent_merges(repo_root, rev_range=rev_range)
