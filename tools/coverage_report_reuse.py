"""tools/coverage_report_reuse.py

Core engine for retaining coverage qualification artifacts and safely reusing them post-merge.
Binds auditor source, configuration, complete audited input snapshot, and output-byte digests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("tools.coverage_report_reuse")

QUALITY_INPUT_REL_PATHS = (
    "reports/aidlc/mutation_score_baseline.json",
)


def resolve_primary_repo_root(repo_root: Path) -> Path:
    """Finds the primary git repository root across standalone and linked worktrees."""
    try:
        res = subprocess.run(["git", "rev-parse", "--git-common-dir"], timeout=15,
            cwd=repo_root, capture_output=True, text=True, check=True)
        c_dir = Path(res.stdout.strip())
        if c_dir.is_absolute():
            return c_dir.parent
        return (repo_root / c_dir).resolve().parent
    except Exception:
        return repo_root


def get_retained_package_dir(repo_root: Path, tree_digest: str) -> Path:
    """Returns canonical directory for a tree-digest retained coverage package."""
    primary_root = resolve_primary_repo_root(repo_root)
    return primary_root / ".local" / "coverage_qualification" / tree_digest


def hash_file_bytes(file_path: Path) -> str:
    """Computes SHA-256 of file bytes if existing, else returns sentinel."""
    if not file_path.is_file():
        return "FILE_NOT_FOUND"
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def compute_coverage_domain_digest(repo_root: Path) -> str:
    """Computes a deterministic digest scoped to tests/, audit_testing_gaps.py, and quality baselines."""
    primary_root = resolve_primary_repo_root(repo_root)
    hasher = hashlib.sha256(b"coverage-domain-v1\x00")
    hasher.update(hash_file_bytes(primary_root / "tools" / "audit_testing_gaps.py").encode("utf-8"))
    for rel_p in QUALITY_INPUT_REL_PATHS:
        f_path = repo_root / rel_p
        if not f_path.is_file():
            f_path = primary_root / rel_p
        hasher.update(rel_p.encode("utf-8"))
        hasher.update(hash_file_bytes(f_path).encode("utf-8"))
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD:tests"], cwd=repo_root, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            hasher.update(res.stdout.strip().encode("utf-8"))
    except Exception:
        pass
    return hasher.hexdigest()


def compute_audited_input_snapshot(
    repo_root: Path,
    tree_digest: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Computes a complete, verifiable snapshot of all inputs governing testing gap audit."""
    primary_root = resolve_primary_repo_root(repo_root)
    auditor_path = primary_root / "tools" / "audit_testing_gaps.py"
    auditor_sha = hash_file_bytes(auditor_path)

    quality_hashes: Dict[str, str] = {}
    for rel_p in QUALITY_INPUT_REL_PATHS:
        # Check repo_root first, then primary_root fallback
        f_path = repo_root / rel_p
        if not f_path.is_file():
            f_path = primary_root / rel_p
        quality_hashes[rel_p] = hash_file_bytes(f_path)

    cfg = config or {"min_e2e_score": 80.0, "fail_on_threshold": True}

    snapshot = {
        "tree_digest": tree_digest,
        "auditor_source_sha256": auditor_sha,
        "quality_input_hashes": quality_hashes,
        "configuration": cfg,
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
    }
    canon_bytes = json.dumps(snapshot, sort_keys=True).encode("utf-8")
    snapshot["snapshot_digest"] = hashlib.sha256(canon_bytes).hexdigest()
    return snapshot


def run_and_retain_coverage_audit(
    repo_root: Path,
    py: str,
    min_e2e_score: float = 80.0,
    fail_on_threshold: bool = True,
) -> Tuple[bool, str, float, Optional[Dict[str, Any]]]:
    """Runs audit_testing_gaps.py, retains outputs, and seals input/output digests."""
    from tools.attestation_core.digest import compute_tree_digest

    primary_root = resolve_primary_repo_root(repo_root)
    tree_digest = compute_tree_digest(repo_root)
    pkg_dir = get_retained_package_dir(repo_root, tree_digest)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    retained_md = pkg_dir / "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md"
    retained_json = pkg_dir / "testing_gaps_audit_report.json"
    metadata_path = pkg_dir / "metadata.json"

    if metadata_path.is_file():
        try:
            cached_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            is_valid, _ = _validate_package_metadata(
                repo_root, tree_digest, cached_meta, retained_md, retained_json
            )
            summary = cached_meta.get("summary", {})
            if is_valid and summary.get("overall_verdict") == "PASS":
                return True, "Reused existing coverage qualification package\n", 0.05, cached_meta
        except Exception:
            pass

    cmd = [
        py,
        str(primary_root / "tools" / "audit_testing_gaps.py"),
        "--repo-root",
        str(repo_root),
        "--output",
        str(retained_md),
        "--json-output",
        str(retained_json),
        "--min-e2e-score",
        str(min_e2e_score),
    ]
    if fail_on_threshold:
        cmd.append("--fail-on-threshold")

    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, timeout=180)
    dur = round(time.monotonic() - t0, 4)

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, output, dur, None

    md_sha = hash_file_bytes(retained_md)
    json_sha = hash_file_bytes(retained_json)
    cfg = {"min_e2e_score": min_e2e_score, "fail_on_threshold": fail_on_threshold}
    snapshot = compute_audited_input_snapshot(repo_root, tree_digest, config=cfg)

    summary: Dict[str, Any] = {}
    try:
        summary = json.loads(retained_json.read_text(encoding="utf-8"))
    except Exception:
        pass

    e2e_dim = summary.get("dimension_scores", {}).get("e2e", {})
    meta = {
        "schema_version": "1.0.0",
        "tree_digest": tree_digest,
        "audited_input_snapshot": snapshot,
        "output_byte_digests": {
            "markdown_sha256": md_sha,
            "json_sha256": json_sha,
        },
        "summary": {
            "overall_score": summary.get("overall_score", 0.0),
            "e2e_dimension_score": e2e_dim.get("score", 0.0),
            "overall_verdict": summary.get("overall_verdict", "FAIL"),
        },
        "retained_files": {
            "markdown_path": str(retained_md),
            "json_path": str(retained_json),
        },
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    metadata_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    # Mirror to worktree-local .local if running inside secondary worktree
    if repo_root != primary_root:
        wt_pkg = repo_root / ".local" / "coverage_qualification" / tree_digest
        if wt_pkg.resolve() != pkg_dir.resolve():
            wt_pkg.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(retained_md, wt_pkg / retained_md.name)
                shutil.copy2(retained_json, wt_pkg / retained_json.name)
                shutil.copy2(metadata_path, wt_pkg / metadata_path.name)
            except shutil.SameFileError:
                pass

    return True, output, dur, meta


def _validate_package_metadata(
    repo_root: Path,
    tree_digest: str,
    meta: Dict[str, Any],
    retained_md: Path,
    retained_json: Path,
) -> Tuple[bool, str]:
    """Validates candidate input equivalence and output-byte digest authenticity."""
    if meta.get("tree_digest") != tree_digest:
        return False, f"Tree digest mismatch: expected {tree_digest}, got {meta.get('tree_digest')}"

    expected_snap = compute_audited_input_snapshot(repo_root, tree_digest)
    actual_snap = meta.get("audited_input_snapshot", {})

    if actual_snap.get("auditor_source_sha256") != expected_snap["auditor_source_sha256"]:
        return False, "Auditor source code modified since qualification"

    if actual_snap.get("quality_input_hashes") != expected_snap["quality_input_hashes"]:
        return False, "Quality input report baseline modified since qualification"

    if actual_snap.get("python_version") != expected_snap["python_version"]:
        return False, "Python environment mismatch since qualification"

    if not retained_md.is_file() or not retained_json.is_file():
        return False, "Retained report files missing on disk"

    recorded_digests = meta.get("output_byte_digests", {})
    if hash_file_bytes(retained_md) != recorded_digests.get("markdown_sha256"):
        return False, "Retained markdown report corrupted or tampered"

    if hash_file_bytes(retained_json) != recorded_digests.get("json_sha256"):
        return False, "Retained JSON report corrupted or tampered"

    return True, "Valid"


def publish_retained_coverage_report(
    repo_root: Path,
    target_md: Optional[Path] = None,
    target_json: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validates and publishes the retained coverage report without recomputing."""
    from tools.attestation_core.digest import compute_tree_digest

    t0 = time.monotonic()
    primary_root = resolve_primary_repo_root(repo_root)
    tree_digest = compute_tree_digest(repo_root)
    pkg_dir = get_retained_package_dir(repo_root, tree_digest)

    # Worktree fallback
    if not pkg_dir.exists() and repo_root != primary_root:
        pkg_dir = repo_root / ".local" / "coverage_qualification" / tree_digest

    metadata_path = pkg_dir / "metadata.json"
    retained_md = pkg_dir / "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md"
    retained_json = pkg_dir / "testing_gaps_audit_report.json"

    if not metadata_path.is_file():
        return {
            "status": "FALLBACK_REQUIRED",
            "reused": False,
            "reason": f"No retained coverage package found for tree {tree_digest[:12]}",
        }

    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "FALLBACK_REQUIRED", "reused": False, "reason": f"Corrupt metadata: {e}"}

    is_valid, reason = _validate_package_metadata(
        repo_root, tree_digest, meta, retained_md, retained_json
    )
    if not is_valid:
        return {"status": "FALLBACK_REQUIRED", "reused": False, "reason": reason}

    out_md = target_md or (
        primary_root / "governance" / "decision_intelligence" / "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md"
    )
    out_json = target_json or (primary_root / "reports" / "testing_gaps_audit_report.json")

    try:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(retained_md, out_md)
        shutil.copy2(retained_json, out_json)
    except Exception as err:
        return {
            "status": "FAILED",
            "reused": False,
            "error": f"Failed publishing retained reports: {err}",
            "exit_code": 1,
        }

    dur = round(time.monotonic() - t0, 4)
    return {
        "status": "SUCCESS",
        "reused": True,
        "action": "PUBLISHED_RETAINED",
        "exit_code": 0,
        "duration_seconds": dur,
        "audit_invocations": 0,
        "tree_digest": tree_digest,
        "markdown_report": str(out_md),
        "json_report": str(out_json),
        "summary": meta.get("summary", {}),
    }


def extract_retained_coverage_audit_metadata(
    repo_root: Path, head_sha: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Extracts summary metadata dictionary from retained package if available."""
    try:
        from tools.attestation_core.digest import compute_tree_digest

        t_dig = compute_tree_digest(repo_root, ref=head_sha or "HEAD")
        m_file = get_retained_package_dir(repo_root, t_dig) / "metadata.json"
        if not m_file.is_file():
            m_file = repo_root / ".local" / "coverage_qualification" / t_dig / "metadata.json"
        if m_file.is_file():
            meta = json.loads(m_file.read_text(encoding="utf-8"))
            return {
                "tree_digest": meta.get("tree_digest"),
                "snapshot_digest": meta.get("audited_input_snapshot", {}).get("snapshot_digest"),
                "markdown_sha256": meta.get("output_byte_digests", {}).get("markdown_sha256"),
                "json_sha256": meta.get("output_byte_digests", {}).get("json_sha256"),
                "overall_score": meta.get("summary", {}).get("overall_score"),
                "e2e_dimension_score": meta.get("summary", {}).get("e2e_dimension_score"),
            }
    except Exception:
        pass
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retained coverage report qualification tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_p = subparsers.add_parser("record", help="Run audit and retain outputs.")
    record_p.add_argument("--repo-root", default=Path("."), type=Path, help="Target repository root.")
    record_p.add_argument("--min-e2e-score", default=80.0, type=float, help="Minimum E2E dimension score.")
    record_p.add_argument("--fail-on-threshold", action="store_true", help="Fail if below threshold.")

    publish_p = subparsers.add_parser("publish", help="Publish retained report if valid.")
    publish_p.add_argument("--repo-root", default=Path("."), type=Path, help="Target repository root.")

    args = parser.parse_args(argv)
    if args.command == "record":
        py = sys.executable
        ok, out, _, _ = run_and_retain_coverage_audit(
            repo_root=args.repo_root.resolve(),
            py=py,
            min_e2e_score=args.min_e2e_score,
            fail_on_threshold=args.fail_on_threshold,
        )
        sys.stdout.write(out)
        return 0 if ok else 1

    if args.command == "publish":
        res = publish_retained_coverage_report(repo_root=args.repo_root.resolve())
        print(json.dumps(res, indent=2))
        return 0 if res.get("status") == "SUCCESS" else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
