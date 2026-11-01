"""tools/pr_governance_core/value_itemizer.py

Itemized value extraction engine for PR queue branches and worktrees.
Extracts grounded capability descriptions, author intents, and verification statuses
from implementation plans, commits, and client deliverables.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def clean_title(title: str) -> str:
    """Strips Markdown headers and common plan prefixes."""
    t = re.sub(r"^[#\s]+", "", title)
    prefixes = [
        "Implementation Plan:", "RCA & Remediation Plan:", "Antigravity implementation plan:",
        "Plan:", "RCA:", "Architecture & Implementation Plan:", "Architecture Plan:",
        "Implementation plan:"
    ]
    for p in prefixes:
        if t.lower().startswith(p.lower()):
            t = t[len(p):].strip()
    return t.strip()



CRITERION_WEIGHTS: Dict[str, float] = {
    "COST_REDUCTION": 5.0,
    "LATENCY_IMPROVEMENT": 4.5,
    "GRAPH_INTEGRITY": 3.5,
    "INTEGRITY_PROTECTION": 3.5,
    "FEATURE_AND_DELIVERABLE": 2.5,
    "FEATURE_CAPABILITY": 2.5,
    "CLIENT_DELIVERABLE": 2.5,
    "HOUSEKEEPING": 1.0,
    "UNKNOWN": 0.0,
}


class ItemizedValue(str):
    statement: str
    benefit_category: str
    criterion_score: float

    def __new__(
        cls,
        statement: str,
        benefit_category: str = "UNKNOWN",
        criterion_score: float = 0.0,
    ) -> ItemizedValue:
        obj = str.__new__(cls, statement)
        obj.statement = statement
        obj.benefit_category = benefit_category
        obj.criterion_score = criterion_score
        return obj

    def __iter__(self):
        return iter((self.statement, self.benefit_category, self.criterion_score))


def is_test_or_doc_path(path: str) -> bool:
    p = path.lower()
    return (
        p.startswith(("tests/", "docs/", "documentation/", ".agents/", ".github/", ".githooks/", "reports/"))
        or "/test_" in p
        or p.endswith(("_test.py", "_spec.py", ".md", ".txt"))
    )


def is_output_deliverable(path: str) -> bool:
    return path.startswith("output/")


def describes_benefit(text: Optional[str]) -> bool:
    """A purpose label alone cannot establish an expected outcome."""
    return bool(text and re.search(
        r"\b(reduc\w*|improv\w*|prevent\w*|restor\w*|preserv\w*|enabl\w*|"
        r"mak\w*|cut|sav\w*|protect\w*|increas\w*|lower\w*|speed\w*|eliminat\w*)\b",
        text, re.IGNORECASE,
    ))


def classify_criterion_from_diff(
    changed_files: Sequence[str] = (),
    desc: Optional[str] = None,
    tier: str = "",
) -> Tuple[str, float]:
    """Resolve the 5-criterion priority grounded in production diff globs and mechanism intent.

    Prevents test filenames or commit prefixes from inflating business value without qualifying
    production diffs.
    """
    prod_files = [f for f in changed_files if not is_test_or_doc_path(f)]
    deliverable_files = [f for f in changed_files if is_output_deliverable(f)]

    # If all files are tests, docs, or governance scripts, business value cannot be elevated
    if changed_files and not prod_files and not deliverable_files:
        return "HOUSEKEEPING", CRITERION_WEIGHTS["HOUSEKEEPING"]

    if deliverable_files:
        return "FEATURE_AND_DELIVERABLE", CRITERION_WEIGHTS["FEATURE_AND_DELIVERABLE"]

    has_benefit = describes_benefit(desc)
    desc_lower = (desc or "").lower()

    # Cost reduction: token optimization, pricing, budget, semantic cache
    cost_tokens = ("cost", "token", "pricing", "budget", "spend", "expense", "bill")
    cost_paths = ("token_optimization", "cost_tracker", "semantic_cache", "model_registry", "prompt_cache", "cost")
    if any(any(tok in p.lower() for tok in cost_paths) for p in prod_files) or (has_benefit and any(tok in desc_lower for tok in cost_tokens)):
        return "COST_REDUCTION", CRITERION_WEIGHTS["COST_REDUCTION"]

    # Latency improvement: query optimization, latency, perf, prefetch, speedup
    latency_tokens = ("latency", "perf", "speed", "faster", "throughput", "fast", "accelerat", "ms")
    latency_paths = ("latency", "perf", "prefetch", "fast_path", "parallel", "index", "speed")
    if any(any(tok in p.lower() for tok in latency_paths) for p in prod_files) or (has_benefit and any(tok in desc_lower for tok in latency_tokens)):
        return "LATENCY_IMPROVEMENT", CRITERION_WEIGHTS["LATENCY_IMPROVEMENT"]

    # Graph integrity: storage, graph, schema, locks, WAL, entity resolution, sanitizers
    integrity_tokens = ("integrity", "corruption", "leak", "concurrency", "lock", "zero-lock", "consistency", "resolver", "sanitizer", "egress")
    integrity_paths = ("storage", "graph", "schema", "sqlite", "wal", "lock", "entity", "resolver", "sanitizer", "pre_egress", "gateway")
    if any(any(tok in p.lower() for tok in integrity_paths) for p in prod_files) or (has_benefit and any(tok in desc_lower for tok in integrity_tokens)):
        return "GRAPH_INTEGRITY", CRITERION_WEIGHTS["GRAPH_INTEGRITY"]

    # Feature and deliverable: runtime, spark, copilot, pipelines, features
    feature_tokens = ("feature", "deliverable", "capability", "copilot", "runtime", "spark")
    feature_paths = ("runtime", "spark", "copilot", "pipeline", "features", "meeting", "session", "audio", "speech")
    if any(any(tok in p.lower() for tok in feature_paths) for p in prod_files) or (has_benefit and any(tok in desc_lower for tok in feature_tokens)):
        return "FEATURE_AND_DELIVERABLE", CRITERION_WEIGHTS["FEATURE_AND_DELIVERABLE"]

    # Tier-based fallback for branches with production files
    if tier.startswith("P0"):
        return "LATENCY_IMPROVEMENT", CRITERION_WEIGHTS["LATENCY_IMPROVEMENT"]
    if tier.startswith("P1"):
        return "GRAPH_INTEGRITY", CRITERION_WEIGHTS["GRAPH_INTEGRITY"]
    if tier.startswith("P2"):
        return "FEATURE_AND_DELIVERABLE", CRITERION_WEIGHTS["FEATURE_AND_DELIVERABLE"]

    return "HOUSEKEEPING", CRITERION_WEIGHTS["HOUSEKEEPING"]


def existing_output_files(
    changed_files: Sequence[str], repo_root: Optional[Path], worktree_path: Optional[str],
) -> List[str]:
    """Count only existing branch-diff deliverables, never deleted paths."""
    from tools.pr_governance_core.common import ROOT

    roots = [Path(worktree_path)] if worktree_path else [repo_root or ROOT]
    return [name for name in changed_files if name.startswith("output/")
            and any((root / name).is_file() for root in roots)]


def extract_plan_intent(plan_path: Optional[Path]) -> Tuple[Optional[str], Optional[str]]:
    """Extracts clean title and concise executive intent from a plan file."""
    if not plan_path or not plan_path.is_file():
        return None, None
    try:
        content = plan_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None, None

    title: Optional[str] = None
    exec_summary: Optional[str] = None
    lines = content.splitlines()

    for line in lines[:80]:
        line = line.strip()
        if not title and line.startswith("# "):
            title = clean_title(line)
            continue
        if title and not exec_summary:
            if line.startswith(("##", "---", "**", "|", ">", "#")) or not line:
                continue
            if any(line.lower().startswith(p) for p in [
                "status:", "branch:", "date:", "investigated pr:", "target:",
                "baseline:", "base revision:", "plan only", "investigation date:",
                "source review:",
            ]):
                continue
            if len(line) > 25:
                s = re.sub(r"\[.*?\]\(.*?\)", "", line)
                s = s.replace("**", "").replace("*", "").replace("`", "").strip()
                sentences = re.split(r"(?<=[.!?])\s+", s)
                if sentences and len(sentences[0]) > 20:
                    exec_summary = sentences[0]
                    break

    if exec_summary and len(exec_summary) > 20 and not exec_summary.lower().startswith(("baseline:", "base revision:", "plan only")):
        return title, exec_summary
    return title, None


def plan_belongs_to_branch(
    plan_path: Path, branch: str, root: Path, worktree_path: Optional[str] = None,
    changed_files: Sequence[str] = (),
) -> bool:
    """Require branch identity in a plan path, tracked change, or explicit header."""
    from tools.pr_governance_core.common import slugify_branch

    path = plan_path.resolve()
    wt = Path(worktree_path).resolve() if worktree_path else None
    relative = None
    for parent in (wt, root.resolve()):
        if parent:
            try:
                relative = path.relative_to(parent)
                break
            except ValueError:
                continue
    if relative is None:
        return False
    if relative.as_posix() in changed_files:
        return True
    slug = slugify_branch(branch).lower()
    identity = relative.as_posix().lower().replace("-", "_")
    if slug and slug in identity:
        return True
    stop = {"feat", "fix", "docs", "chore", "plan", "implementation", "branch", "pr", "queue"}
    tokens = {t for t in re.findall(r"[a-z0-9]+", branch.lower()) if t not in stop and not t.isdigit()}
    path_tokens = set(re.findall(r"[a-z0-9]+", identity)) - stop
    if tokens and len(tokens & path_tokens) >= min(2, len(tokens)):
        return True
    try:
        header = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:30])
    except OSError:
        return False
    return bool(re.search(rf"(?im)^\s*(?:\*\*)?branch(?:\*\*)?\s*:\s*`?{re.escape(branch)}`?\s*$", header))


def resolve_authoritative_plan(
    branch: str,
    repo_root: Optional[Path] = None,
    worktree_path: Optional[str] = None,
    evidence_links: Sequence[Dict[str, str]] = (),
    changed_files: Sequence[str] = (),
) -> Optional[Path]:
    """Resolves the authentic, authoritative plan path for a branch."""
    from tools.pr_governance_core.common import ROOT, slugify_branch

    root = repo_root or ROOT
    slug = slugify_branch(branch)
    wt_path = Path(worktree_path).resolve() if worktree_path else None

    candidate_plans: List[Path] = []
    evidence_plan_paths = set()

    for ev in evidence_links:
        if "plan" in ev.get("label", "").lower() and ev.get("url", "").startswith("file://"):
            tp = Path(urllib.parse.unquote(ev["url"][7:]))
            if tp.is_file() and plan_belongs_to_branch(tp, branch, root, worktree_path, changed_files):
                candidate_plans.append(tp)
                evidence_plan_paths.add(tp)

    search_dirs = [d for d in [wt_path / "docs" / "plans" if wt_path else None, root / "docs" / "plans"] if d and d.is_dir()]
    search_files = [p for p in [root / "plans" / slug / "implementation_plan.md", wt_path / "implementation_plan.md" if wt_path else None] if p and p.is_file()]
    for p in [*(f for sd in search_dirs for f in sd.glob("*.md")), *search_files]:
        if plan_belongs_to_branch(p, branch, root, worktree_path, changed_files):
            candidate_plans.append(p)

    def score_plan(p: Path) -> int:
        stem = p.stem.lower()
        if stem == slug.lower():
            return 100
        if stem.startswith(slug.lower()) or slug.lower().startswith(stem):
            return 80
        if p in evidence_plan_paths:
            return 75
        stop_words = {"feat", "fix", "docs", "chore", "plan", "branch", "pr", "queue", "test", "tests"}
        b_words = set(re.findall(r"[a-z0-9]+", branch.lower())) - stop_words
        p_words = set(re.findall(r"[a-z0-9]+", stem)) - stop_words
        overlap = len(b_words & p_words)
        if overlap >= 2:
            return 50 + overlap * 5
        if p.name == "implementation_plan.md" and wt_path and p.parent == wt_path:
            return 20
        return 0

    if not candidate_plans:
        return None

    candidate_plans.sort(key=score_plan, reverse=True)
    return candidate_plans[0]


def _format_commit_intent(root: Path, branch: str) -> Tuple[Optional[str], int]:
    try:
        res = subprocess.run(  # timeout=5
            ["git", "log", f"main..{branch}", "--format=%s", "-n", "3"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
            if lines:
                top_msg = lines[0]
                m = re.match(r"^[a-z]+(?:\([^\)]+\))?:\s*(.+)$", top_msg, re.IGNORECASE)
                commit_intent = m.group(1).strip() if m else top_msg
                return commit_intent, len(lines)
    except Exception:
        pass
    return None, 0


def extract_structured_value(
    branch: str,
    changed_files: Sequence[str] = (),
    repo_root: Optional[Path] = None,
    evidence_links: Sequence[Dict[str, str]] = (),
    worktree_path: Optional[str] = None,
    tier: str = "",
) -> Tuple[Optional[str], str, float]:
    """Extract structured (statement, benefit_category, criterion_score) grounded in diffs and intent."""
    from tools.pr_governance_core.common import ROOT

    root = repo_root or ROOT

    # 1. Authentic client deliverable in output/
    output_files = existing_output_files(changed_files, root, worktree_path)
    if output_files or ("live" in branch.lower() and output_files):
        meeting_name = "meeting"
        for of in output_files:
            parts = Path(of).parts
            if len(parts) >= 2:
                meeting_name = parts[1]
                break
        statement = f"Makes {len(output_files)} {meeting_name} client-deliverable artifacts available in output/."
        return statement, "FEATURE_AND_DELIVERABLE", CRITERION_WEIGHTS["FEATURE_AND_DELIVERABLE"]

    # 2. Plan-based intent
    plan_path = resolve_authoritative_plan(
        branch=branch,
        repo_root=root,
        worktree_path=worktree_path,
        evidence_links=evidence_links,
        changed_files=changed_files,
    )
    title, summary = extract_plan_intent(plan_path) if plan_path else (None, None)

    # 3. Commit-based intent if branch has unique commits
    commit_intent, _ = _format_commit_intent(root, branch)

    # 4. Core description resolution
    desc: Optional[str] = None
    boilerplate = ["pr queue inception protection", "catastrophic pr queue", "5-panel agent debate"]
    if summary and not any(bp in summary.lower() for bp in boilerplate):
        desc = summary
    elif title and not any(bp in title.lower() for bp in boilerplate):
        desc = title
    elif commit_intent:
        desc = commit_intent
    elif title:
        desc = title

    category, score = classify_criterion_from_diff(changed_files, desc, tier=tier)
    if not desc:
        return None, category, score

    statement = desc if desc.endswith((".", "!", "?")) else f"{desc}."
    return statement, category, score


def extract_itemized_value(
    branch: str,
    changed_files: Sequence[str] = (),
    repo_root: Optional[Path] = None,
    evidence_links: Sequence[Dict[str, str]] = (),
    worktree_path: Optional[str] = None,
    tier: str = "",
) -> Optional[ItemizedValue]:
    """Describe branch-owned intent or deliverables as an ItemizedValue.

    Returns None if no branch-bound description is available.
    """
    statement, category, score = extract_structured_value(
        branch=branch,
        changed_files=changed_files,
        repo_root=repo_root,
        evidence_links=evidence_links,
        worktree_path=worktree_path,
        tier=tier,
    )
    if not statement:
        return None
    return ItemizedValue(statement, category, score)

