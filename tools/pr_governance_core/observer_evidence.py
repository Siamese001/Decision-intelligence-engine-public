"""Evidence link discovery for the PR queue observer."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import unquote

from tools.pr_governance_core.common import slugify_branch
from tools.pr_governance_core.value_itemizer import plan_belongs_to_branch


def _receipt_belongs_to_branch(path: Path, branch: str, head_sha: str) -> bool:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or data.get("branch") != branch:
        return False
    receipt_head = data.get("head_sha") or data.get("branch_sha") or data.get("commit_sha")
    return not receipt_head or receipt_head == head_sha


def discover_evidence_links(
    b_name: str,
    b_snap: Any,
    changed_files: List[str],
    repo_root: Path,
) -> List[Dict[str, str]]:
    """Discovers verifiable evidence links for a branch, eliminating Evidence: N/A starvation."""
    links: List[Dict[str, str]] = []
    slug = slugify_branch(b_name)
    wt_val = getattr(b_snap, "worktree_path", None) if not isinstance(b_snap, dict) else b_snap.get("worktree_path")
    wt_dir = Path(wt_val).resolve() if wt_val and Path(wt_val).is_dir() else None
    root = repo_root.resolve()
    head_sha = getattr(b_snap, "head_sha", "") if not isinstance(b_snap, dict) else b_snap.get("head_sha", "")

    existing = getattr(b_snap, "evidence_links", None) if not isinstance(b_snap, dict) else b_snap.get("evidence_links")
    if existing and isinstance(existing, list):
        for link in existing:
            url, label = link.get("url", ""), link.get("label", "")
            if not url.startswith("file://"):
                continue
            path = Path(unquote(url[7:]))
            owned_plan = label == "Plan" and path.is_file() and plan_belongs_to_branch(path, b_name, root, wt_val, changed_files)
            owned_receipt = "Receipt" in label and _receipt_belongs_to_branch(path, b_name, head_sha)
            if owned_plan or owned_receipt:
                links.append(link)

    plan_found = False
    plans = getattr(b_snap, "plans", []) if not isinstance(b_snap, dict) else b_snap.get("plans", [])
    for plan_rec in plans:
        p_rel = getattr(plan_rec, "plan_path", "") if not isinstance(plan_rec, dict) else plan_rec.get("plan_path", "")
        if not p_rel:
            continue
        cands = [wt_dir / p_rel] if wt_dir else []
        cands.append(root / p_rel)
        for cand in cands:
            if cand.is_file() and plan_belongs_to_branch(cand, b_name, root, wt_val, changed_files):
                links.append({"label": "Plan", "url": f"file://{cand.resolve()}"})
                plan_found = True
                break
        if plan_found:
            break

    if not plan_found:
        cands = []
        if wt_dir:
            cands.extend([
                wt_dir / "implementation_plan.md",
                wt_dir / "plans" / slug / "implementation_plan.md",
                wt_dir / "docs" / "plans" / f"{slug}.md",
            ])
        cands.extend([
            root / "plans" / slug / "implementation_plan.md",
            root / "docs" / "plans" / f"{slug}.md",
        ])
        for cand in cands:
            if cand.is_file() and plan_belongs_to_branch(cand, b_name, root, wt_val, changed_files):
                links.append({"label": "Plan", "url": f"file://{cand.resolve()}"})
                plan_found = True
                break

    receipt_cands = []
    if wt_dir:
        receipt_cands.extend([
            ("Readiness Receipt", wt_dir / "reports" / "readiness" / f"local_main_readiness_{slug}_receipt.json"),
            ("Review Receipt", wt_dir / "reports" / f"pr_review_{slug}_receipt.json"),
        ])
    receipt_cands.extend([
        ("Readiness Receipt", root / "reports" / "readiness" / f"local_main_readiness_{slug}_receipt.json"),
        ("Review Receipt", root / "reports" / f"pr_review_{slug}_receipt.json"),
    ])
    for label, r_path in receipt_cands:
        if _receipt_belongs_to_branch(r_path, b_name, head_sha):
            links.append({"label": label, "url": f"file://{r_path.resolve()}"})

    if not any("Receipt" in l["label"] for l in links) and wt_dir:
        r_dir = wt_dir / "reports"
        if r_dir.is_dir():
            wave_receipts = sorted(r_dir.glob("wave*_receipt.json"))
            owned = [path for path in wave_receipts if _receipt_belongs_to_branch(path, b_name, head_sha)]
            if owned:
                links.append({"label": "Wave Receipt", "url": f"file://{owned[-1].resolve()}"})

    deliv_count = 0
    for f in changed_files:
        if f.startswith("output/") and deliv_count < 2:
            source = wt_dir if wt_dir else root
            cand = source / f if (source / f).is_file() else None
            if cand:
                links.append({"label": f"Deliverable ({Path(f).name})", "url": f"file://{cand.resolve()}"})
                deliv_count += 1

    seen_urls: Set[str] = set()
    deduped: List[Dict[str, str]] = []
    for l in links:
        if l["url"] not in seen_urls:
            seen_urls.add(l["url"])
            deduped.append(l)
    return deduped
