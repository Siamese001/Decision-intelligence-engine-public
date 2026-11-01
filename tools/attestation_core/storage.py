"""tools/attestation_core/storage.py

Storage and discovery of tree-digest bound attestations in `.local/receipts/<tree>.json`
and `refs/notes/nee-attest`, with fallback to historical in-tree receipts.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .digest import compute_branch_delta_digest, compute_git_tree_oid, compute_tree_digest
from .envelope import (
    READINESS_PREDICATE_TYPE,
    create_in_toto_statement,
    unwrap_dsse_envelope,
    verify_statement_subject,
    wrap_dsse_envelope,
)

logger = logging.getLogger("tools.attestation_core.storage")
DEFAULT_RECEIPTS_DIR = Path(".local/receipts")
GIT_NOTES_REF = "refs/notes/nee-attest"


def get_attestation_path(
    repo_root: Path,
    tree_digest: str,
    predicate_type: str = READINESS_PREDICATE_TYPE,
) -> Path:
    if "pr-review" in predicate_type:
        namespace = "pr_review"
    elif "readiness" in predicate_type:
        namespace = "readiness"
    else:
        namespace = "common"
    return repo_root / DEFAULT_RECEIPTS_DIR / namespace / f"{tree_digest}.json"


def save_readiness_attestation(
    repo_root: Path,
    predicate: Dict[str, Any],
    tree_digest: Optional[str] = None,
    head_sha: Optional[str] = None,
    predicate_type: str = READINESS_PREDICATE_TYPE,
) -> Path:
    """Creates, wraps, and writes a tree-digest attestation to .local/receipts/<namespace>/<tree>.json."""
    ref = head_sha or str(predicate.get("head_commit_sha") or predicate.get("head_sha") or "HEAD")
    digest = tree_digest or compute_tree_digest(repo_root, ref=ref)
    enriched_pred = dict(predicate)
    branch_tree_oid = compute_git_tree_oid(repo_root, ref=ref)
    main_tree_oid = compute_git_tree_oid(repo_root, ref="main")
    delta_digest = compute_branch_delta_digest(repo_root, ref=ref, base_ref="main")
    if branch_tree_oid:
        enriched_pred.setdefault("branch_tree_oid", branch_tree_oid)
    if main_tree_oid:
        enriched_pred.setdefault("main_tree_oid", main_tree_oid)
    if delta_digest:
        enriched_pred.setdefault("branch_delta_digest", delta_digest)

    stmt = create_in_toto_statement(digest, enriched_pred, predicate_type=predicate_type)
    envelope = wrap_dsse_envelope(stmt)

    out_path = get_attestation_path(repo_root, digest, predicate_type=predicate_type)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(envelope, indent=2) + "\n"
    out_path.write_text(serialized, encoding="utf-8")

    # Also write delta-indexed attestation for zero-file-churn disjoint rebase validation
    if delta_digest:
        delta_stmt = create_in_toto_statement(delta_digest, enriched_pred, predicate_type=predicate_type)
        delta_env = wrap_dsse_envelope(delta_stmt)
        delta_path = get_attestation_path(repo_root, f"delta_{delta_digest}", predicate_type=predicate_type)
        delta_path.parent.mkdir(parents=True, exist_ok=True)
        delta_path.write_text(json.dumps(delta_env, indent=2) + "\n", encoding="utf-8")

    # Optionally persist to git notes if on a valid commit
    if head_sha:
        try:
            subprocess.run(
                [
                    "git",
                    "notes",
                    f"--ref={GIT_NOTES_REF}",
                    "add",
                    "-f",
                    "-m",
                    json.dumps(envelope),
                    head_sha,
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10.0,
            )
        except Exception as exc:
            _ = exc

    return out_path


save_attestation = save_readiness_attestation


def load_attestation_by_tree(
    repo_root: Path,
    tree_digest: str,
    head_sha: Optional[str] = None,
    predicate_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Finds and unwraps attestation matching the given tree digest and optional predicate type."""
    # 1. Check namespaced path if predicate_type is provided
    candidates: list[Path] = []
    if predicate_type:
        candidates.append(get_attestation_path(repo_root, tree_digest, predicate_type))
    # Fallback to legacy root path
    candidates.append(repo_root / DEFAULT_RECEIPTS_DIR / f"{tree_digest}.json")

    for local_p in candidates:
        if local_p.is_file():
            try:
                data = json.loads(local_p.read_text(encoding="utf-8"))
                stmt = unwrap_dsse_envelope(data)
                if verify_statement_subject(stmt, tree_digest):
                    if not predicate_type or stmt.get("predicateType") == predicate_type:
                        return stmt
            except Exception as e:
                logger.debug(f"Failed to read local attestation at {local_p}: {e}")

    # 2. Check git notes on head_sha
    if head_sha:
        try:
            proc = subprocess.run(
                ["git", "notes", f"--ref={GIT_NOTES_REF}", "show", head_sha],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10.0,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout.strip())
                stmt = unwrap_dsse_envelope(data)
                if verify_statement_subject(stmt, tree_digest):
                    if not predicate_type or stmt.get("predicateType") == predicate_type:
                        return stmt
        except Exception as exc:
            _ = exc

    return None


def _is_disjoint_rebase_equivalent(
    repo_root: Path,
    branch: str,
    old_head_sha: str,
    new_head_sha: str,
    old_base_sha: str,
) -> Tuple[bool, str]:
    """Verifies whether `new_head_sha` is a pure disjoint rebase of `old_head_sha` onto current `main`."""
    if not old_head_sha or not new_head_sha:
        return False, ""
    try:
        old_delta = compute_branch_delta_digest(
            repo_root, ref=old_head_sha, base_ref=old_base_sha or "main"
        )
        new_delta = compute_branch_delta_digest(repo_root, ref=new_head_sha, base_ref="main")
        if not old_delta or old_delta != new_delta:
            return False, ""
        main_proc = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        main_sha = main_proc.stdout.strip() if main_proc.returncode == 0 else ""
        if not main_sha:
            return False, ""
        if not old_base_sha:
            mb_proc = subprocess.run(
                ["git", "merge-base", "main", old_head_sha],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10.0,
            )
            old_base_sha = mb_proc.stdout.strip() if mb_proc.returncode == 0 else ""
        if not old_base_sha:
            return False, ""
        from tools.readiness_planner import check_disjoint_base_advance_equivalence

        diff_proc = subprocess.run(
            ["git", "diff", "--name-only", f"{main_sha}...{new_head_sha}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
        branch_diff = diff_proc.stdout.splitlines() if diff_proc.returncode == 0 else []
        is_disjoint, _ = check_disjoint_base_advance_equivalence(
            old_base_sha, main_sha, branch_diff, repo_root=repo_root
        )
        return is_disjoint, main_sha
    except Exception as exc:
        _ = exc
        return False, ""


def _matches_legacy_receipt(data: Dict[str, Any], branch: str, head_sha: str) -> bool:
    rec_sha = data.get("head_commit_sha") or data.get("head_sha")
    rec_branch = data.get("branch")
    return (
        rec_branch == branch
        and data.get("terminal_outcome") == "PASS"
        and bool(rec_sha and rec_sha == head_sha)
    )


def _check_legacy_receipts(
    repo_root: Path,
    branch: str,
    head_sha: str,
    slug: str,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    branch_receipt_path = (
        repo_root / "reports" / "readiness" / f"local_main_readiness_{slug}_receipt.json"
    )
    global_receipt_path = repo_root / "reports" / "local_main_readiness_receipt.json"

    for p in (branch_receipt_path, global_receipt_path):
        if not p.is_file():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if _matches_legacy_receipt(data, branch, head_sha):
                    return (
                        True,
                        data,
                        f"legacy in-tree receipt ({p.relative_to(repo_root)})",
                    )
                rec_sha = str(data.get("head_commit_sha") or data.get("head_sha") or "")
                rec_base = str(data.get("base_commit_sha") or data.get("base_sha") or "")
                if data.get("branch") == branch and data.get("terminal_outcome") == "PASS" and rec_sha:
                    is_disjoint, new_main = _is_disjoint_rebase_equivalent(
                        repo_root, branch, rec_sha, head_sha, rec_base
                    )
                    if is_disjoint:
                        rebound = dict(data)
                        rebound["head_commit_sha"] = head_sha
                        rebound["head_sha"] = head_sha
                        rebound["base_commit_sha"] = new_main
                        rebound["base_sha"] = new_main
                        return (
                            True,
                            rebound,
                            f"legacy in-tree receipt via disjoint delta-OID ({p.relative_to(repo_root)})",
                        )
        except Exception as exc:
            _ = exc

    return False, None, "none"


def check_readiness_qualification(
    repo_root: Path,
    branch: str,
    head_sha: str,
    slug: str,
    expected_predicate_type: str = READINESS_PREDICATE_TYPE,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Checks if branch has a valid readiness attestation or legacy receipt.

    Returns:
        (is_passed, attestation_or_receipt, source_description)
    """
    try:
        tree_digest = compute_tree_digest(repo_root, ref=head_sha or "HEAD")
        stmt = load_attestation_by_tree(
            repo_root, tree_digest, head_sha=head_sha, predicate_type=expected_predicate_type
        )
        if stmt is not None:
            pred = stmt.get("predicate", {})
            if (
                stmt.get("predicateType") == expected_predicate_type
                and pred.get("terminal_outcome") == "PASS"
            ):
                return (
                    True,
                    stmt,
                    f"out-of-tree DSSE attestation (.local/receipts/{tree_digest[:12]}...)",
                )
        # Check delta-indexed DSSE attestation for pure disjoint rebases
        delta_digest = compute_branch_delta_digest(repo_root, ref=head_sha or "HEAD", base_ref="main")
        if delta_digest:
            delta_path = get_attestation_path(
                repo_root, f"delta_{delta_digest}", predicate_type=expected_predicate_type
            )
            if delta_path.is_file():
                data = json.loads(delta_path.read_text(encoding="utf-8"))
                d_stmt = unwrap_dsse_envelope(data)
                if verify_statement_subject(d_stmt, delta_digest):
                    pred = dict(d_stmt.get("predicate") or {})
                    old_sha = str(pred.get("head_commit_sha") or pred.get("head_sha") or "")
                    old_base = str(pred.get("base_commit_sha") or pred.get("base_sha") or "")
                    if pred.get("terminal_outcome") == "PASS" and pred.get("branch") in (None, branch):
                        is_disjoint, new_main = _is_disjoint_rebase_equivalent(
                            repo_root, branch, old_sha, head_sha, old_base
                        )
                        if is_disjoint:
                            pred["head_commit_sha"] = head_sha
                            pred["head_sha"] = head_sha
                            pred["base_commit_sha"] = new_main
                            pred["base_sha"] = new_main
                            rebound_stmt = dict(d_stmt)
                            rebound_stmt["predicate"] = pred
                            return (
                                True,
                                rebound_stmt,
                                f"out-of-tree DSSE delta attestation (.local/receipts/delta_{delta_digest[:12]}...)",
                            )
    except Exception as e:
        logger.debug(f"Tree digest attestation check failed: {e}")

    return _check_legacy_receipts(repo_root, branch, head_sha, slug)
